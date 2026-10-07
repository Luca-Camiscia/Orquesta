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
import os
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
from orquesta.revision import (
    CONFIDENCE_ESCALATION, JUDGE, JUDGE_CANDIDATES, ORCHESTRATOR, REVIEW_PREFERENCE,
    elegir_revisor, parsear_veredicto, prompt_review, razones_bloqueo,
)

USER = "usuario"
MAX_MODERATED_STEPS = 4
MODES = ("rondas", "moderada", "critica", "posturas", "seccionado")
STANCES = ("a favor", "en contra", "neutral")
DEFAULT_ASPECTS = ("corrección", "seguridad", "performance", "mantenibilidad", "tests")
HISTORY_CHARS = 24000
CLAUDE_TIMEOUT = 540
CLAUDE_SLOT_WAIT_S = 60.0
CANCELLED = "ESTADO: CANCELADO — lo cortaste vos"
_ctx = __import__("threading").local()  # clave de cancelación del turno en curso
_ALL = object()


def request_cancel(cid: str) -> int:
    """Corta el turno en curso de una conversación (mata los procesos y frena la ronda)."""
    return procs.cancel(cid)


class ChatError(Exception):
    pass


class NeedsForce(ChatError):
    """Mergear sin una review aprobada requiere confirmación explícita."""


REVIEW_DIFF_CHARS = 20000


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

    def _ecosystem_project(self) -> dict:
        d = str(self.paths.home.resolve())
        return {
            "id": "orquesta",
            "name": "Ecosistema orquesta",
            "dir": d,
            "directory": d,
            "sistema": True,
            "created": _now(),
        }

    def _user_projects(self) -> list[dict]:
        try:
            return json.loads(self._projects_file().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return []

    def list_projects(self) -> list[dict]:
        user_projects = [p for p in self._user_projects() if p.get("id") != "orquesta"]
        res = []
        for p in user_projects:
            item = dict(p)
            if "dir" in item and "directory" not in item:
                item["directory"] = item["dir"]
            res.append(item)
        return [self._ecosystem_project(), *res]

    def create_project(self, name: str, directory: str) -> dict:
        d = Path(directory).expanduser()
        if not name.strip():
            raise ChatError("el proyecto necesita un nombre")
        if name.strip().lower() == "ecosistema orquesta" or name.strip() == "orquesta":
            raise ChatError("nombre reservado para el proyecto del sistema")
        if not d.is_dir():
            raise ChatError(f"no existe el directorio {d}")
        with self.lock:
            items = self._user_projects()
            resolved = str(d.resolve())
            pr = {"id": _id(), "name": name.strip(), "dir": resolved, "directory": resolved, "created": _now()}
            items.append(pr)
            self._projects_file().write_text(json.dumps(items, indent=2, ensure_ascii=False))
        return pr

    def project_for_dir(self, directory: str) -> dict:
        """Reusa el proyecto del directorio, o lo crea y persiste sin duplicarlo."""
        with self.lock:
            pr = next((p for p in self.list_projects() if p["dir"] == directory), None)
            if pr is not None:
                return pr
            pr = {"id": _id(), "name": os.path.basename(directory.rstrip("/")),
                  "dir": directory, "directory": directory, "created": _now()}
            items = self._user_projects()
            items.append(pr)
            self._projects_file().write_text(json.dumps(items, indent=2, ensure_ascii=False))
            return pr

    def get_conversation_dir(self, cid: str) -> Path:
        d = self.paths.home / "sandbox" / "sin-proyecto" / cid
        d.mkdir(parents=True, exist_ok=True)
        return d

    def get_project_for_conversation(self, conv: dict) -> dict:
        pid = conv.get("project_id")
        if pid is None or pid in ("", "sin-proyecto", "none"):
            cdir = str(self.get_conversation_dir(conv["id"]).resolve())
            return {
                "id": None,
                "name": "Sin proyecto",
                "dir": cdir,
                "directory": cdir,
            }
        return self.get_project(pid)

    project_of = get_project_for_conversation

    def get_project(self, pid: str | None, cid: str | None = None) -> dict:
        if pid is None or pid in ("", "sin-proyecto", "none"):
            cdir = str(self.get_conversation_dir(cid or "default").resolve())
            return {
                "id": None,
                "name": "Sin proyecto",
                "dir": cdir,
                "directory": cdir,
            }
        if pid == "orquesta":
            return self._ecosystem_project()
        pr = next((p for p in self.list_projects() if p["id"] == pid), None)
        if pr is None:
            raise ChatError(f"no existe el proyecto {pid}")
        return pr

    def delete_project(self, pid: str) -> None:
        if pid == "orquesta":
            raise ChatError("no se puede borrar el proyecto del sistema")
        with self.lock:
            items = self._user_projects()
            new_items = [p for p in items if p["id"] != pid]
            if len(new_items) == len(items):
                raise ChatError(f"no existe el proyecto {pid}")
            self._projects_file().write_text(json.dumps(new_items, indent=2, ensure_ascii=False))

    def update_project(self, pid: str, name: str | None = None, directory: str | None = None) -> dict:
        if pid == "orquesta":
            raise ChatError("no se puede renombrar ni cambiar el directorio del proyecto del sistema")
        with self.lock:
            items = self._user_projects()
            pr = next((p for p in items if p["id"] == pid), None)
            if pr is None:
                raise ChatError(f"no existe el proyecto {pid}")
            if name is not None:
                if not name.strip():
                    raise ChatError("el proyecto necesita un nombre")
                if name.strip().lower() == "ecosistema orquesta" or name.strip() == "orquesta":
                    raise ChatError("nombre reservado para el proyecto del sistema")
                pr["name"] = name.strip()
            if directory is not None:
                d = Path(directory).expanduser()
                if not d.is_dir():
                    raise ChatError(f"no existe el directorio {d}")
                resolved = str(d.resolve())
                pr["dir"] = resolved
                pr["directory"] = resolved
            self._projects_file().write_text(json.dumps(items, indent=2, ensure_ascii=False))
            return pr

    def rename_project(self, pid: str, name: str) -> dict:
        if pid == "orquesta":
            raise ChatError("no se puede renombrar el proyecto del sistema")
        return self.update_project(pid, name=name)

    def change_project_directory(self, pid: str, directory: str) -> dict:
        if pid == "orquesta":
            raise ChatError("no se puede cambiar el directorio del proyecto del sistema")
        return self.update_project(pid, directory=directory)

    # --- conversaciones ---
    def is_write_agent(self, participant: str) -> bool:
        if participant == ORCHESTRATOR:
            return False
        manifest = mf.load(self.paths)
        entry = mf.deployed_agent(manifest, participant)
        if entry is not None:
            return entry.mode == "write"
        agents = (manifest or {}).get("agents") or {}
        return agents.get(participant, {}).get("mode") == "write"

    def participants(self) -> list[str]:
        deployed = sorted(((mf.load(self.paths) or {}).get("agents") or {}).keys())
        return [ORCHESTRATOR, *deployed]

    def _conv_file(self, cid: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{10}", cid or ""):
            raise ChatError("id de conversación inválido")
        return self.root / "conv" / f"{cid}.json"

    def create_conversation(self, project_id: str | None, title: str | None, kind: str, participants: list[str],
                            mode: str = "rondas", origen: str | None = None,
                            aspectos: list[str] | None = None) -> dict:
        if project_id in ("", "sin-proyecto", "none"):
            project_id = None
        if project_id is not None:
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
        if mode not in MODES:
            raise ChatError(f"mode tiene que ser uno de: {', '.join(MODES)}")
        if project_id is None:
            for p in participants:
                if self.is_write_agent(p):
                    raise ChatError(f"los agentes de escritura no pueden participar en conversaciones sin proyecto ({p})")
        cid = _id()
        if project_id is None:
            self.get_conversation_dir(cid)
        conv = {"id": cid, "project_id": project_id, "title": (title or "").strip() or "Sin título",
                "kind": kind, "mode": mode if kind == "reunion" else "rondas",
                "participants": list(dict.fromkeys(participants)), "messages": [], "worktrees": [],
                "busy": False, "created": _now(), "updated": _now(), "origen": origen}
        if aspectos is not None:
            conv["aspectos"] = aspectos
        self.save(conv)
        return conv

    def get_conversation(self, cid: str) -> dict:
        try:
            return json.loads(self._conv_file(cid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ChatError(f"no existe la conversación {cid}") from None

    def list_conversations(self, project_id: str | None = _ALL) -> list[dict]:
        out = []
        for f in (self.root / "conv").glob("*.json"):
            try:
                c = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if project_id is _ALL:
                pass
            elif project_id in (None, "sin-proyecto", "none", ""):
                if c.get("project_id") is not None:
                    continue
            else:
                if c.get("project_id") != project_id:
                    continue
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
            if c["kind"] == "individual" and len(set(participants)) > 1:
                c["kind"] = "reunion"
            if c.get("project_id") is None:
                for p in participants:
                    if self.is_write_agent(p):
                        raise ChatError(f"los agentes de escritura no pueden participar en conversaciones sin proyecto ({p})")
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
    pr = store.get_project_for_conversation(conv)
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
    u = data.get("usage") or {}
    usage = {"input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
             "cost_usd": data.get("total_cost_usd")}
    return Reply(strip_mood_tag(str(data.get("result", ""))).strip(), meta={"usage": usage})


def _record_claude(paths: Paths, who: str, r: Reply) -> Reply:
    from orquesta import usage
    u = r.meta.get("usage") or {}
    status = "OK" if not r.error else ("RATE_LIMIT" if "RATE_LIMIT" in r.error or "limit" in r.error.lower()
                                       else "ERROR")
    try:
        usage.record(paths, backend="claude", agent=who, status=status, tokens_in=u.get("input_tokens"),
                     tokens_out=u.get("output_tokens"), cost_usd=u.get("cost_usd"))
    except Exception:
        pass
    return r


def default_executor(paths: Paths, project: dict, participant: str, prompt: str) -> Reply:
    cwd = project["dir"]
    try:
        manifest = mf.load(paths)
        entry = mf.deployed_agent(manifest, participant)
    except mf.ManifestError as exc:
        return Reply("", error=f"ESTADO: ERROR — {exc}; corré `orquesta doctor`")
    if participant != ORCHESTRATOR and entry is None:
        return Reply("", error=f"ESTADO: ERROR — {participant} no está desplegado")
    if participant == ORCHESTRATOR or entry.backend == "claude":
        limit = mf.max_parallel(manifest, "claude")
        with runner.acquire_slot(paths, "claude", limit, CLAUDE_SLOT_WAIT_S) as slot:
            if slot is None:
                return Reply("", error=f"ESTADO: OCUPADO — los {limit} lugares de claude están ocupados "
                                       "(max_parallel): esperá y volvé a intentar")
            args = ["--model", "opus"] if participant == ORCHESTRATOR else ["--agent", participant]
            return _record_claude(paths, participant, _claude(args, prompt, cwd))
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
    meta = {k: r.meta.get(k) for k in ("branch", "worktree", "commit", "diffstat", "usage", "contrato")
            if r.meta.get(k)}
    meta["latency_s"] = r.latency_s
    if r.status != "OK":
        return Reply(r.output, error=f"ESTADO: {r.status} — {r.detail}", meta=meta)
    return Reply(r.output, meta=meta)


# --- turnos ----------------------------------------------------------------------------------

BRIEF_KEEP = 8  # mensajes recientes que se pasan completos (Laban et al. 2025, arXiv 2505.06120)


def _labels(conv: dict) -> dict[str, str]:
    """Etiquetas anónimas por turno, en orden aleatorio (sesgo de identidad y de posición:
    arXiv 2510.07517 y 2406.07791)."""
    import random
    names = list(conv["participants"]) + [ORCHESTRATOR, JUDGE]
    rnd = random.Random(f"{conv['id']}:{len(conv['messages'])}")
    rnd.shuffle(names)
    return {n: f"Participante {chr(65 + i)}" for i, n in enumerate(dict.fromkeys(names))}


def _es_propuesta(m: dict) -> bool:
    """Respuesta de un participante a la consigna: no cuenta el veredicto del juez ni la review automática."""
    return (m["status"] == "done" and bool(m["text"].strip())
            and (m.get("meta") or {}).get("rol") not in ("veredicto", "review"))


_GANADORA = re.compile(r"^[\s>*_`\-]*Ganadora:\s*[*_`]*\s*([AB])\b", re.M | re.I)


def _ganadora(texto: str) -> str | None:
    """Letra elegida por el juez; tolera el Markdown que suelen agregar los modelos. Si nombra las dos, None."""
    letras = {m.upper() for m in _GANADORA.findall(texto or "")}
    return letras.pop() if len(letras) == 1 else None


def _history(conv: dict, viewer: str | None = None, anon: bool = False, until_user: bool = False,
             reverse_proposals: tuple[str, ...] = ()) -> str:
    msgs = [m for m in conv["messages"] if m["status"] != "pending"]
    if until_user:
        last_user = max((i for i, m in enumerate(msgs) if m["author"] == USER), default=-1)
        msgs = msgs[:last_user + 1]
    if reverse_proposals:
        last_user = max((i for i, m in enumerate(msgs) if m["author"] == USER), default=-1)
        rounds: dict[int | None, list[int]] = {}
        for i, m in enumerate(msgs):
            if i > last_user and m["author"] in reverse_proposals and _es_propuesta(m):
                rounds.setdefault((m.get("meta") or {}).get("ronda"), []).append(i)
        for indices in rounds.values():
            proposals = [msgs[i] for i in reversed(indices)]
            for i, m in zip(indices, proposals):
                msgs[i] = m
    omitted = 0
    if len(msgs) > BRIEF_KEEP:
        keep_idx = set(range(len(msgs) - BRIEF_KEEP, len(msgs)))
        keep_idx |= {i for i, m in enumerate(msgs) if (m.get("meta") or {}).get("rol") == "veredicto"}
        omitted = len(msgs) - len(keep_idx)
        msgs = [m for i, m in enumerate(msgs) if i in keep_idx]
    labels = _labels(conv) if anon else {}
    names = list(labels)

    def who(a: str) -> str:
        if a == USER:
            return "usuario"
        if anon and a == viewer:
            return "VOS"
        return labels.get(a, a) if anon else a
    lines = [f"[{who(m['author'])}]: {_redact_map(m['text'], labels) if anon else m['text']}" for m in msgs]
    head = [f"[... {omitted} mensajes anteriores omitidos; los veredictos se conservan ...]"] if omitted else []
    return "\n\n".join(head + lines)[-HISTORY_CHARS:]


def _redact_map(text: str, labels: dict[str, str]) -> str:
    for n in sorted(labels, key=len, reverse=True):
        text = re.sub(re.escape(n), labels[n], text, flags=re.I)
    return text


def _agent_prompt(conv: dict, project: dict, who: str, question: str | None) -> str:
    anon = conv["kind"] == "reunion"
    n_others = len([p for p in conv["participants"] if p != who])
    others = (f"{n_others} más, anónimos" if anon else
              ", ".join(p for p in conv["participants"] if p != who) or "nadie más")
    kind = "una reunión" if anon else "una conversación individual"
    ask = (f"El moderador te pregunta: {question}" if question
           else "Respondé al último mensaje del usuario, teniendo en cuenta lo que dijeron los demás.")
    if anon:
        ask = _redact_map(ask, _labels(conv))
    return (f"Estás en {kind} de orquesta, proyecto «{project['name']}» (directorio {project['dir']}). "
            f"Sos {who}. Otros participantes: {others}.\n\nHistorial:\n{_history(conv, who, anon)}\n\n---\n{ask} "
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
           question: str | None = None, prompt: str | None = None, meta: dict | None = None) -> Reply:
    mid = _id()
    _ctx.key = cid

    def add(c):
        base = {"pregunta": question} if question else {}
        c["messages"].append({"id": mid, "author": who, "text": "", "status": "pending", "ts": _now(),
                              "meta": {**base, **(meta or {})}})
    conv = store.update(cid, add)
    reply = executor(store.paths, project, who, prompt or _agent_prompt(conv, project, who, question))

    def done(c):
        m = next(x for x in c["messages"] if x["id"] == mid)
        m["status"] = ("interrupted" if reply.error == CANCELLED or procs.is_cancelled(cid)
                       else "error" if reply.error else "done")
        m["text"] = (f"{reply.error}\n\n{reply.text}".strip() if reply.error else reply.text)
        m["meta"].update(reply.meta)
        if not m["meta"].get("contrato"):
            m["meta"]["contrato"] = runner.parse_contract(reply.text)
        if reply.meta.get("branch"):
            c["worktrees"].append({"agent": who, "branch": reply.meta["branch"],
                                   "worktree": reply.meta.get("worktree"), "commit": reply.meta.get("commit"),
                                   "diffstat": reply.meta.get("diffstat", ""), "status": "abierto"})
    store.update(cid, done)
    if reply.meta.get("branch") and not reply.error and not procs.is_cancelled(cid):
        _verify_and_review(store, cid, project, who, reply, executor)
    return reply


def _calibrate(store: Store, who: str, confianza, acierto: bool, fuente: str) -> None:
    if confianza is None:
        return
    from orquesta import calibration
    entry = ((mf.load(store.paths) or {}).get("agents") or {}).get(who) or {}
    try:
        calibration.record(store.paths, backend=entry.get("backend", "claude"), agent=who,
                           confianza=confianza, acierto=acierto, fuente=fuente)
    except Exception:
        pass


def _repo_of(project: dict) -> Path:
    """Repo git del proyecto (el proyecto puede ser un subdirectorio del repo)."""
    d = Path(project["dir"])
    return worktrees.git_toplevel(d) or d


def _project_wt(store: Store, project: dict, branch: str) -> worktrees.Worktree | None:
    try:
        return worktrees._find(store.paths, branch, _repo_of(project))
    except worktrees.WorktreeError:
        return None


def _verify_and_review(store: Store, cid: str, project: dict, writer: str, reply: Reply,
                       executor: Executor) -> None:
    branch = reply.meta["branch"]
    confianza = (reply.meta.get("contrato") or runner.parse_contract(reply.text) or {}).get("confianza")
    wt = _project_wt(store, project, branch)
    tests = worktrees.run_tests(wt) if wt is not None else None
    store.update(cid, lambda c: next(x for x in c["worktrees"] if x["branch"] == branch).update(
        confianza_autor=confianza))
    if tests is not None:
        store.update(cid, lambda c: next(x for x in c["worktrees"] if x["branch"] == branch).update(tests=tests))
        _calibrate(store, writer, confianza, tests["ok"], "tests")
    verdict = _auto_review(store, cid, project, writer, branch, executor, tests)
    if verdict is not None:
        _calibrate(store, writer, confianza, verdict == "APROBAR", "review")


def _reviewer_for(store: Store, writer: str) -> str:
    manifest = mf.load(store.paths) or {}
    reviewer = elegir_revisor(manifest, writer)
    return reviewer if reviewer is not None else ORCHESTRATOR


def _auto_review(store: Store, cid: str, project: dict, writer: str, branch: str, executor: Executor,
                 tests: dict | None = None) -> str | None:
    """Review cruzado automático, ciega al autor: lo escrito por una familia de modelos lo revisa otra
    (autopreferencia: arXiv 2404.13076). Solo puede RECHAZAR con evidencia concreta (arXiv 2509.01494)."""
    reviewer = _reviewer_for(store, writer)
    wt = _project_wt(store, project, branch)
    diff = ""
    if wt is not None:
        diff = subprocess.run(["git", "diff", f"{wt.base_sha}..{branch}"], cwd=wt.repo,
                              capture_output=True, text=True).stdout[:REVIEW_DIFF_CHARS]
    if tests is None:
        evidencia = "No hay comando de tests configurado para el proyecto (.orquesta/proyecto.toml)."
    else:
        evidencia = (f"Resultado de los tests del proyecto: {'PASARON' if tests['ok'] else 'FALLARON'} "
                     f"(`{tests['cmd']}`).\n```\n{tests['salida'][-800:]}\n```")
    prompt = prompt_review(project["name"], project["dir"], diff, evidencia)
    r = _speak(store, cid, project, reviewer, executor, prompt=prompt, meta={"rol": "review", "branch": branch})
    verdict = parsear_veredicto(r.text)

    def save(c):
        w = next((x for x in c["worktrees"] if x["branch"] == branch), None)
        if w is not None:
            w["review"] = {"by": reviewer, "veredicto": verdict}
    store.update(cid, save)
    return verdict


def _set_text(store: Store, cid: str, author: str, text: str) -> None:
    def fn(c):
        c["messages"].append({"id": _id(), "author": author, "text": text, "status": "done",
                              "ts": _now(), "meta": {}})
    store.update(cid, fn)


def run_turn(store: Store, cid: str, text: str, executor: Executor = default_executor) -> None:
    conv = store.get_conversation(cid)
    project = store.get_project_for_conversation(conv)
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
        elif conv["mode"] == "moderada":
            _moderate(store, cid, project, executor)
        elif conv["mode"] == "critica":
            _mode_critica(store, cid, project, executor)
        elif conv["mode"] == "posturas":
            _mode_posturas(store, cid, project, executor)
        else:
            _mode_seccionado(store, cid, project, executor, text)
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


# --- modos con evidencia (crítica, posturas, seccionado) --------------------------------------

def _history_until_user(conv: dict) -> str:
    """Historial hasta el último mensaje del usuario (sin las respuestas de la ronda en curso)."""
    return _history(conv, None, conv["kind"] == "reunion", until_user=True)


def _history_until_user_old(conv: dict) -> str:
    msgs = conv["messages"]
    last_user = max((i for i, m in enumerate(msgs) if m["author"] == USER), default=-1)
    lines = [f"[{m['author']}]: {m['text']}" for m in msgs[:last_user + 1] if m["status"] != "pending"]
    return "\n\n".join(lines)[-HISTORY_CHARS:]


def _frame(project: dict, who: str, history: str, instruction: str) -> str:
    return (f"Estás en una reunión de orquesta, proyecto «{project['name']}» (directorio {project['dir']}). "
            f"Sos {who}.\n\nHistorial:\n{history}\n\n---\n{instruction}\n"
            f"Respondé en castellano y cerrá con RESULTADO, CONFIANZA (0-100), EVIDENCIA y SIGUIENTE PASO.")


def _parallel(store: Store, cid: str, project: dict, executor: Executor,
              jobs: list[tuple[str, str, dict]]) -> dict[str, Reply]:
    import threading
    out: dict[str, Reply] = {}
    errors: list[Exception] = []

    def work(who: str, prompt: str, meta: dict) -> None:
        try:
            out[who] = _speak(store, cid, project, who, executor, prompt=prompt, meta=meta)
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=work, args=job) for job in jobs]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    if errors:
        raise errors[0]
    return out


def _family(store: Store, name: str) -> str:
    if name == ORCHESTRATOR:
        return "claude"
    return (((mf.load(store.paths) or {}).get("agents") or {}).get(name) or {}).get("backend", "?")


def _judge_for(store: Store, participants: list[str]) -> str:
    """Juez de una familia que no participa (sesgo de autopreferencia y de familia: arXiv 2404.13076).
    Si participan las tres familias, juzga Claude, con los participantes anónimos."""
    deployed = (mf.load(store.paths) or {}).get("agents") or {}
    present = {_family(store, p) for p in participants}
    pool = [n for n in JUDGE_CANDIDATES if n in deployed] + \
           [n for n, e in deployed.items() if e.get("mode") == "read-only" and n not in JUDGE_CANDIDATES]
    for fam in ("claude", "codex", "agy"):
        if fam in present:
            continue
        if fam == "claude":
            return JUDGE if JUDGE in deployed else ORCHESTRATOR
        cand = next((n for n in pool if deployed[n].get("backend") == fam and n not in participants), None)
        if cand:
            return cand
    return JUDGE if JUDGE in deployed else ORCHESTRATOR


def _judge(store: Store, cid: str, project: dict, executor: Executor, rubric: str,
           simetrico: bool = False) -> None:
    if procs.is_cancelled(cid):
        return
    judge = _judge_for(store, store.get_conversation(cid)["participants"])
    conv = store.get_conversation(cid)
    last_user = max((i for i, m in enumerate(conv["messages"]) if m["author"] == USER), default=-1)
    turno = [m for m in conv["messages"][last_user + 1:] if m["author"] in conv["participants"] and _es_propuesta(m)]
    proposals = tuple(dict.fromkeys(m["author"] for m in turno))
    # Si las propuestas no entran enteras en el historial, cada orden recortaría una distinta y la divergencia
    # no sería sensibilidad al orden: en ese caso se juzga una sola vez.
    entran = sum(len(m["text"]) for m in turno) <= HISTORY_CHARS // 2
    histories = [_history(conv, judge, True)]
    decision_rule = ""
    if simetrico and entran and len(conv["participants"]) == len(proposals) == 2:
        # Ambos prompts parten de la misma foto: etiquetas estables y sin el primer veredicto.
        histories.append(_history(conv, judge, True, reverse_proposals=proposals))
        labels = _labels(conv)
        a, b = ("VOS" if who == judge else labels[who] for who in proposals)
        decision_rule = (f"La propuesta A corresponde a {a} y la propuesta B a {b}, sin importar su "
                         "posición en el historial. Incluí una única línea exacta `Ganadora: A` o "
                         "`Ganadora: B` para identificar la propuesta ganadora, ")
    decisions = []
    for history in histories:
        if procs.is_cancelled(cid):
            return
        prompt = (f"Sos el juez de una reunión de orquesta, proyecto «{project['name']}». No aportes ideas nuevas "
                  f"salvo para desempatar.\n\nHistorial (participantes anónimos, en orden aleatorio):\n"
                  f"{history}\n\n---\n{rubric}\n"
                  f"Evaluá por evidencia verificable, no por elocuencia ni cantidad de votos; señalá consensos "
                  f"sospechosos y no premies el largo ni el relleno. Entregá: decisión final, desacuerdos que "
                  f"quedan, qué verificar después, {decision_rule}y cerrá "
                  f"con RESULTADO y CONFIANZA (0-100).")
        reply = _speak(store, cid, project, judge, executor, prompt=prompt, meta={"rol": "veredicto"})
        decisions.append(_ganadora(reply.text) if not reply.error else None)
    if len(decisions) != 2 or procs.is_cancelled(cid):
        return
    if None in decisions:
        uncertainty = "INCIERTO: no se pudo verificar la sensibilidad al orden por falta de una decisión válida."
    elif decisions[0] != decisions[1]:
        quien = {"A": a, "B": b}
        uncertainty = ("INCIERTO por sensibilidad al orden: las dos evaluaciones eligieron propuestas distintas "
                       f"(1.ª: {quien[decisions[0]]}; 2.ª, con el orden invertido: {quien[decisions[1]]}).")
    else:
        return
    store.update(cid, lambda c: c["messages"].append({"id": _id(), "author": judge, "text": uncertainty,
                                                    "status": "done", "ts": _now(),
                                                    "meta": {"rol": "veredicto"}}))


def _redact(text: str, names: list[str]) -> str:
    """Anonimato real: un agente puede nombrarse a sí mismo en su respuesta."""
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(re.escape(n), "[participante]", text, flags=re.I)
    return text


def _agents_of(conv: dict) -> list[str]:
    return list(conv["participants"])


def _mode_critica(store: Store, cid: str, project: dict, executor: Executor) -> None:
    conv = store.get_conversation(cid)
    who_all = _agents_of(conv)
    hist = _history_until_user(conv)
    blind = ("Respondé de forma INDEPENDIENTE: todavía no ves a los demás. Dá tu respuesta, el "
             "razonamiento y tu confianza.")
    r0 = _parallel(store, cid, project, executor, [(w, _frame(project, w, hist, blind), {"ronda": 0})
                                                  for w in who_all])
    if procs.is_cancelled(cid) or len(who_all) < 2:
        return _judge(store, cid, project, executor, "Sintetizá las respuestas.", simetrico=True)
    # Debate selectivo (iMAD, arXiv 2511.11306; "Debate or Vote", 2508.17536): si la ronda a ciegas ya
    # coincide, no se debate: se cierra con el juez y se ahorran todas las llamadas de crítica.
    judge = _judge_for(store, who_all)
    answers = "\n\n".join(f"Respuesta {chr(65 + i)}:\n{_redact(r0[w].text, who_all)}"
                           for i, w in enumerate(x for x in who_all if x in r0))
    det = executor(store.paths, project, judge,
                   f"Estas son respuestas independientes a la misma pregunta:\n\n{answers}\n\n¿Hay un "
                   f"desacuerdo sustantivo entre ellas (conclusiones distintas, no solo de forma)? Respondé "
                   f"SOLO con `DESACUERDO: SI` o `DESACUERDO: NO`.")
    if re.search(r"DESACUERDO:\s*NO", det.text or "", re.I):
        return _judge(store, cid, project, executor,
                      "Las respuestas independientes coinciden: confirmá la conclusión común y su evidencia.")
    jobs = []
    for w in who_all:
        others = [_redact(r0[o].text, who_all) for o in who_all if o != w and o in r0]
        labeled = "\n\n".join(f"Respuesta {chr(65 + i)}:\n{txt}" for i, txt in enumerate(others))
        instr = (f"TU respuesta de la ronda 0:\n{_redact(r0[w].text, who_all) if w in r0 else '(sin respuesta)'}\n\n"
                 f"Respuestas de los demás (anónimas):\n{labeled}\n\n"
                 f"Criticá al menos un punto ajeno con evidencia y revisá la tuya. Decí explícitamente si "
                 f"CAMBIASTE de postura y por qué; no cambies sin evidencia nueva.")
        jobs.append((w, _frame(project, w, hist, instr), {"ronda": 1}))
    _parallel(store, cid, project, executor, jobs)
    _judge(store, cid, project, executor,
           "Compará la ronda 0 (independiente) con la 1 (crítica): qué se sostuvo, qué cambió y con qué evidencia.",
           simetrico=True)


def _mode_posturas(store: Store, cid: str, project: dict, executor: Executor) -> None:
    conv = store.get_conversation(cid)
    who_all = _agents_of(conv)
    stances = {w: STANCES[i % len(STANCES)] for i, w in enumerate(who_all)}
    for rnd, task in ((1, "Argumentá tu postura asignada con evidencia concreta."),
                      (2, "Replicá los argumentos contrarios. Mantené tu postura salvo evidencia nueva "
                          "verificable, y si cedés en algo decí exactamente en qué.")):
        for w in who_all:
            if procs.is_cancelled(cid):
                return
            conv = store.get_conversation(cid)
            instr = (f"Tu postura asignada: {stances[w]}. Es un rol para explorar el desacuerdo: sostenela "
                     f"aunque no sea tu opinión. {task}")
            _speak(store, cid, project, w, executor, prompt=_frame(project, w, _history(conv, w, True), instr),
                   meta={"ronda": rnd, "postura": stances[w]})
    _judge(store, cid, project, executor,
           "Cada participante defendió una postura asignada. Decidí por la evidencia, no por la postura.")


def _mode_seccionado(store: Store, cid: str, project: dict, executor: Executor, text: str) -> None:
    conv = store.get_conversation(cid)
    who_all = _agents_of(conv)
    m = re.search(r"aspectos?\s*:\s*(.+)$", text, re.I | re.S)
    aspects = conv.get("aspectos") or (
        [a.strip() for a in re.split(r"[;,\n]", m.group(1)) if a.strip()] if m else list(DEFAULT_ASPECTS))
    hist = _history_until_user(conv)
    jobs = [(w, _frame(project, w, hist, f"Tu aspecto: {aspects[i % len(aspects)]}. Cubrí SOLO ese aspecto, "
                                         f"en profundidad; los otros los cubre otro participante."),
             {"aspecto": aspects[i % len(aspects)]}) for i, w in enumerate(who_all)]
    _parallel(store, cid, project, executor, jobs)
    _judge(store, cid, project, executor, "Consolidá los aspectos en un informe único, priorizado por impacto.")


# --- búsqueda -------------------------------------------------------------------------------

def search(store: Store, query: str, limit: int = 30) -> list[dict]:
    q = query.strip().lower()
    if not q:
        return []
    out = []
    for f in sorted((store.root / "conv").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for m in c["messages"]:
            i = m["text"].lower().find(q)
            if i >= 0:
                start = max(0, i - 60)
                out.append({"conversation_id": c["id"], "project_id": c["project_id"], "title": c["title"],
                            "author": m["author"], "snippet": m["text"][start:i + len(q) + 80]})
                break
        if len(out) >= limit:
            break
    return out


# --- worktrees del chat ----------------------------------------------------------------------

def _wt(conv: dict, branch: str) -> dict:
    wt = next((w for w in conv["worktrees"] if w["branch"] == branch), None)
    if wt is None or wt["status"] != "abierto":
        raise ChatError(f"no hay un worktree abierto con la rama {branch}")
    return wt


def merge_worktree(store: Store, cid: str, branch: str, force: bool = False) -> None:
    conv = store.get_conversation(cid)
    wt = _wt(conv, branch)
    if not force:
        razones = razones_bloqueo(wt)
        if razones:
            raise NeedsForce(razones[0])
    try:
        worktrees.merge(store.paths, branch, repo=wt.get("repo") or _repo_of(store.project_of(conv)))
    except worktrees.WorktreeError as exc:
        raise ChatError(str(exc)) from None
    store.update(cid, lambda c: _wt(c, branch).update(status="mergeado"))


def discard_worktree(store: Store, cid: str, branch: str) -> None:
    conv = store.get_conversation(cid)
    wt = _wt(conv, branch)
    worktrees.clean(store.paths, branch, force=True,
                    repo=wt.get("repo") or _repo_of(store.project_of(conv)))
    store.update(cid, lambda c: _wt(c, branch).update(status="descartado"))
