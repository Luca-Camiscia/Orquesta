"""Chat de la UI: proyectos, conversaciones individuales y reuniones con agentes.

- Un proyecto es un nombre más un directorio de trabajo. Cada conversación pertenece a un proyecto.
- Individual: le habla un solo participante. Reunión en "rondas": cada mensaje del usuario dispara
  una ronda en orden (cada uno ve lo anterior), y con @agente le habla a uno solo. Reunión
  "moderada": el orquestador decide a quién preguntar (hasta MAX_MODERATED_STEPS) y cierra con una
  síntesis.
- Los backends no comparten memoria: cada participante recibe el historial en su prompt.
- Escritura solo vía worktree: los agentes write corren en `orquesta/<agente>/<tarea>` y el usuario
  decide Mergear o Descartar. El orquestador y los agentes claude corren en plan mode (solo lectura).
Las conversaciones se guardan en ~/orquesta/conversaciones/ (fuera de git).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from filelock import FileLock

from orquesta import manifest as mf
from orquesta import procs, runner, worktrees
from orquesta.paths import Paths

ORCHESTRATOR = "orquestador"
USER = "usuario"
MAX_MODERATED_STEPS = 4
HISTORY_CHARS = 24000
CLAUDE_TIMEOUT = 540
CANCELLED = "ESTADO: CANCELADO — lo cortaste vos"
_ctx = __import__("threading").local()  # clave de cancelación del turno en curso


def request_cancel(cid: str) -> int:
    """Corta el turno en curso de una conversación (mata los procesos y frena la ronda)."""
    return procs.cancel(cid)


class ChatError(Exception):
    pass


@dataclass
class Reply:
    text: str
    error: str | None = None
    meta: dict = field(default_factory=dict)


Executor = Callable[[Paths, dict, str, str], Reply]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _id() -> str:
    return uuid.uuid4().hex[:10]


class Store:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.root = paths.home / "conversaciones"
        (self.root / "conv").mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.root / ".lock"), timeout=30)

    # --- proyectos ---
    def _projects_file(self) -> Path:
        return self.root / "proyectos.json"

    def list_projects(self) -> list[dict]:
        try:
            return json.loads(self._projects_file().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []

    def create_project(self, name: str, directory: str) -> dict:
        d = Path(directory).expanduser()
        if not name.strip():
            raise ChatError("el proyecto necesita un nombre")
        if not d.is_dir():
            raise ChatError(f"no existe el directorio {d}")
        with self.lock:
            items = self.list_projects()
            pr = {"id": _id(), "name": name.strip(), "dir": str(d.resolve()), "created": _now()}
            items.append(pr)
            self._projects_file().write_text(json.dumps(items, indent=2, ensure_ascii=False))
        return pr

    def get_project(self, pid: str) -> dict:
        pr = next((p for p in self.list_projects() if p["id"] == pid), None)
        if pr is None:
            raise ChatError(f"no existe el proyecto {pid}")
        return pr

    # --- conversaciones ---
    def participants(self) -> list[str]:
        deployed = sorted(((mf.load(self.paths) or {}).get("agents") or {}).keys())
        return [ORCHESTRATOR, *deployed]

    def _conv_file(self, cid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{10}", cid or ""):
            raise ChatError("id de conversación inválido")
        return self.root / "conv" / f"{cid}.json"

    def create_conversation(self, project_id: str, title: str, kind: str, participants: list[str],
                            mode: str = "rondas") -> dict:
        self.get_project(project_id)
        valid = set(self.participants())
        unknown = [p for p in participants if p not in valid]
        if unknown:
            raise ChatError(f"participantes desconocidos o sin desplegar: {', '.join(unknown)}")
        if kind not in ("individual", "reunion"):
            raise ChatError("kind tiene que ser individual o reunion")
        if kind == "individual" and len(participants) != 1:
            raise ChatError("una conversación individual tiene exactamente un participante")
        if kind == "reunion" and not participants:
            raise ChatError("una reunión necesita al menos un participante")
        if mode not in ("rondas", "moderada"):
            raise ChatError("mode tiene que ser rondas o moderada")
        conv = {"id": _id(), "project_id": project_id, "title": title.strip() or "Sin título",
                "kind": kind, "mode": mode if kind == "reunion" else "rondas",
                "participants": list(dict.fromkeys(participants)), "messages": [], "worktrees": [],
                "busy": False, "created": _now(), "updated": _now()}
        self.save(conv)
        return conv

    def get_conversation(self, cid: str) -> dict:
        try:
            return json.loads(self._conv_file(cid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ChatError(f"no existe la conversación {cid}") from None

    def list_conversations(self, project_id: str | None = None) -> list[dict]:
        out = []
        for f in (self.root / "conv").glob("*.json"):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if project_id is None or c["project_id"] == project_id:
                item = {k: c[k] for k in ("id", "project_id", "title", "kind", "mode",
                                          "participants", "busy", "updated")}
                item["status"] = status_of(c)
                out.append(item)
        return sorted(out, key=lambda c: c["updated"], reverse=True)

    def save(self, conv: dict) -> None:
        conv["updated"] = _now()
        f = self._conv_file(conv["id"])
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(conv, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)

    def update(self, cid: str, fn: Callable[[dict], None]) -> dict:
        with self.lock:
            conv = self.get_conversation(cid)
            fn(conv)
            self.save(conv)
            return conv

    def delete_conversation(self, cid: str) -> None:
        self._conv_file(cid).unlink(missing_ok=True)

    def set_participants(self, cid: str, participants: list[str]) -> dict:
        valid = set(self.participants())
        unknown = [p for p in participants if p not in valid]
        if unknown:
            raise ChatError(f"participantes desconocidos: {', '.join(unknown)}")
        if not participants:
            raise ChatError("tiene que quedar al menos un participante")

        def fn(c):
            if c["kind"] == "individual" and len(participants) != 1:
                raise ChatError("una conversación individual tiene exactamente un participante")
            c["participants"] = list(dict.fromkeys(participants))
        return self.update(cid, fn)


def status_of(conv: dict) -> str:
    if conv.get("busy"):
        return "respondiendo"
    if any(w.get("status") == "abierto" for w in conv.get("worktrees", [])):
        return "esperando-revision"
    if not conv.get("messages"):
        return "nueva"
    if conv["messages"][-1].get("status") == "error":
        return "error"
    return "lista"


def export_markdown(store: "Store", cid: str) -> str:
    conv = store.get_conversation(cid)
    pr = store.get_project(conv["project_id"])
    kind = f"reunión {conv['mode']}" if conv["kind"] == "reunion" else "individual"
    out = [f"# {conv['title']}", "",
           f"Proyecto «{pr['name']}» ({pr['dir']}), {kind}. Participantes: "
           f"{', '.join(conv['participants'])}. Exportado {_now()}.", ""]
    for m in conv["messages"]:
        extra = f" ({m['meta']['latency_s']} s)" if m.get("meta", {}).get("latency_s") else ""
        out += [f"## {m['author']}{extra}", ""]
        if m.get("meta", {}).get("pregunta"):
            out += [f"> El moderador preguntó: {m['meta']['pregunta']}", ""]
        out += [m["text"] or f"_{m['status']}_", ""]
    for w in conv.get("worktrees", []):
        out += [f"## Cambios de {w['agent']} en `{w['branch']}` ({w['status']})", "", "```",
                w.get("diffstat") or "", "```", ""]
    return "\n".join(out)


# --- ejecución -------------------------------------------------------------------------------

def strip_mood_tag(text: str) -> str:
    """El orquestador lee CLAUDE.md y abre con `[mood: x]`: en el chat no aporta."""
    return re.sub(r"^\s*\[mood:[^\]\n]*\]\s*", "", text, count=1)


def _claude(args: list[str], prompt: str, cwd: str) -> Reply:
    cmd = ["claude", "-p", "--permission-mode", "plan", "--output-format", "json", *args, prompt]
    try:
        p = procs.run(cmd, cwd=cwd, timeout=CLAUDE_TIMEOUT, key=getattr(_ctx, "key", None))
    except FileNotFoundError:
        return Reply("", error="ESTADO: ERROR — no encuentro `claude` en el PATH")
    if p.cancelled:
        return Reply("", error=CANCELLED)
    if p.timed_out:
        return Reply("", error=f"ESTADO: TIMEOUT — claude no respondió en {CLAUDE_TIMEOUT} s")
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Reply(p.stdout.strip(), error=f"ESTADO: FALLO_BACKEND — {p.stderr.strip()[-300:]}")
    if data.get("is_error") or p.returncode != 0:
        return Reply(str(data.get("result", "")), error=f"ESTADO: FALLO_BACKEND — {data.get('subtype', '')}")
    return Reply(strip_mood_tag(str(data.get("result", ""))).strip())


def default_executor(paths: Paths, project: dict, participant: str, prompt: str) -> Reply:
    cwd = project["dir"]
    if participant == ORCHESTRATOR:
        return _claude(["--model", "opus"], prompt, cwd)
    entry = mf.deployed_agent(mf.load(paths), participant)
    if entry is None:
        return Reply("", error=f"ESTADO: ERROR — {participant} no está desplegado")
    if entry.backend == "claude":
        return _claude(["--agent", participant], prompt, cwd)
    task = None
    if entry.mode == "write":
        if entry.isolation != "worktree":
            return Reply("", error=f"ESTADO: ERROR — {participant} escribe sin worktree; en el chat solo "
                                   f"se permite escritura en worktree")
        task = "chat-" + datetime.now().strftime("%m%d-%H%M%S")
    r = runner.run(runner.RunRequest(agent=participant, cwd=Path(cwd), prompt=prompt, task=task,
                                     cancel_key=getattr(_ctx, "key", None)), paths)
    if r.status == "CANCELADO":
        return Reply(r.output, error=CANCELLED, meta={})
    meta = {k: r.meta.get(k) for k in ("branch", "worktree", "commit", "diffstat") if r.meta.get(k)}
    meta["latency_s"] = r.latency_s
    if r.status != "OK":
        return Reply(r.output, error=f"ESTADO: {r.status} — {r.detail}", meta=meta)
    return Reply(r.output, meta=meta)


# --- turnos ----------------------------------------------------------------------------------

def _history(conv: dict) -> str:
    lines = [f"[{m['author']}]: {m['text']}" for m in conv["messages"] if m["status"] != "pending"]
    text = "\n\n".join(lines)
    return text[-HISTORY_CHARS:]


def _agent_prompt(conv: dict, project: dict, who: str, question: str | None) -> str:
    others = ", ".join(p for p in conv["participants"] if p != who) or "nadie más"
    kind = "una reunión" if conv["kind"] == "reunion" else "una conversación individual"
    ask = (f"El moderador te pregunta: {question}" if question
           else "Respondé al último mensaje del usuario, teniendo en cuenta lo que dijeron los demás.")
    return (f"Estás en {kind} de orquesta, proyecto «{project['name']}» (directorio {project['dir']}). "
            f"Sos {who}. Otros participantes: {others}.\n\nHistorial:\n{_history(conv)}\n\n---\n{ask} "
            f"Respondé en castellano, directo y conciso.")


def _moderator_prompt(conv: dict, project: dict, step: int, final: bool) -> str:
    agents = [p for p in conv["participants"] if p != ORCHESTRATOR]
    rule = ("Ya no podés consultar más: respondé SOLO con `SINTESIS: <síntesis final con decisión y "
            "desacuerdos>`." if final else
            f"Llevás {step} de {MAX_MODERATED_STEPS} consultas. Respondé SOLO con una de estas formas:\n"
            f"`PREGUNTAR @<agente>: <pregunta concreta>` (agentes: {', '.join(agents)})\n"
            f"`SINTESIS: <síntesis final con decisión y desacuerdos>`")
    return (f"Sos el orquestador y moderás una reunión de orquesta, proyecto «{project['name']}» "
            f"(directorio {project['dir']}).\n\nHistorial:\n{_history(conv)}\n\n---\n{rule}")


def _speak(store: Store, cid: str, project: dict, who: str, executor: Executor,
           question: str | None = None, prompt: str | None = None) -> Reply:
    mid = _id()

    def add(c):
        c["messages"].append({"id": mid, "author": who, "text": "", "status": "pending", "ts": _now(),
                              "meta": {"pregunta": question} if question else {}})
    conv = store.update(cid, add)
    reply = executor(store.paths, project, who, prompt or _agent_prompt(conv, project, who, question))

    def done(c):
        m = next(x for x in c["messages"] if x["id"] == mid)
        m["status"] = ("interrupted" if reply.error == CANCELLED or procs.is_cancelled(cid)
                       else "error" if reply.error else "done")
        m["text"] = (f"{reply.error}\n\n{reply.text}".strip() if reply.error else reply.text)
        m["meta"].update(reply.meta)
        if reply.meta.get("branch"):
            c["worktrees"].append({"agent": who, "branch": reply.meta["branch"],
                                   "worktree": reply.meta.get("worktree"), "commit": reply.meta.get("commit"),
                                   "diffstat": reply.meta.get("diffstat", ""), "status": "abierto"})
    store.update(cid, done)
    return reply


def _set_text(store: Store, cid: str, author: str, text: str) -> None:
    def fn(c):
        c["messages"].append({"id": _id(), "author": author, "text": text, "status": "done",
                              "ts": _now(), "meta": {}})
    store.update(cid, fn)


def run_turn(store: Store, cid: str, text: str, executor: Executor = default_executor) -> None:
    conv = store.get_conversation(cid)
    project = store.get_project(conv["project_id"])
    procs.reset(cid)
    _ctx.key = cid
    _set_text(store, cid, USER, text)
    try:
        if conv["kind"] == "individual":
            _speak(store, cid, project, conv["participants"][0], executor)
        elif conv["mode"] == "rondas":
            mentioned = [m for m in re.findall(r"@([a-z0-9-]+)", text) if m in conv["participants"]]
            for who in (list(dict.fromkeys(mentioned)) or conv["participants"]):
                if procs.is_cancelled(cid):
                    break
                _speak(store, cid, project, who, executor)
        else:
            _moderate(store, cid, project, executor)
    finally:
        _ctx.key = None
        store.update(cid, lambda c: c.update(busy=False))


def _moderate(store: Store, cid: str, project: dict, executor: Executor) -> None:
    agents = [p for p in store.get_conversation(cid)["participants"] if p != ORCHESTRATOR]
    step = 0
    while True:
        if procs.is_cancelled(cid):
            return
        final = step >= MAX_MODERATED_STEPS
        conv = store.get_conversation(cid)
        r = executor(store.paths, project, ORCHESTRATOR, _moderator_prompt(conv, project, step, final))
        if r.error:
            _set_text(store, cid, ORCHESTRATOR, f"{r.error}\n\n{r.text}".strip())
            return
        ask = re.search(r"PREGUNTAR\s+@([a-z0-9-]+)\s*:\s*(.+)", r.text, re.S)
        if not final and ask and ask.group(1) in agents:
            _speak(store, cid, project, ask.group(1), executor, question=ask.group(2).strip())
            step += 1
            continue
        synth = re.search(r"SINTESIS\s*:\s*(.+)", r.text, re.S)
        _set_text(store, cid, ORCHESTRATOR, synth.group(1).strip() if synth else r.text.strip())
        return


# --- worktrees del chat ----------------------------------------------------------------------

def _wt(conv: dict, branch: str) -> dict:
    wt = next((w for w in conv["worktrees"] if w["branch"] == branch), None)
    if wt is None or wt["status"] != "abierto":
        raise ChatError(f"no hay un worktree abierto con la rama {branch}")
    return wt


def merge_worktree(store: Store, cid: str, branch: str) -> None:
    conv = store.get_conversation(cid)
    _wt(conv, branch)
    repo = worktrees.git_toplevel(Path(store.get_project(conv["project_id"])["dir"]))
    if repo is None:
        raise ChatError("el proyecto no es un repo git")
    p = subprocess.run(["git", "merge", "--no-edit", branch], cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=repo, capture_output=True)
        raise ChatError(f"el merge falló y se abortó: {(p.stderr or p.stdout).strip()[:300]}")
    worktrees.clean(store.paths, branch)
    store.update(cid, lambda c: _wt(c, branch).update(status="mergeado"))


def discard_worktree(store: Store, cid: str, branch: str) -> None:
    _wt(store.get_conversation(cid), branch)
    worktrees.clean(store.paths, branch, force=True)
    store.update(cid, lambda c: _wt(c, branch).update(status="descartado"))
