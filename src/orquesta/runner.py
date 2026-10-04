"""Runner de backends externos (`orquesta run <agente>`).

Usa la configuración APLICADA del agente (manifiesto del deploy) y la invocación de su backend
(backends.yaml), y aplica en código las reglas de orquestación:
- `.orquesta-off` en el proyecto → no delega;
- `limits.max_parallel` por backend (lock files);
- agentes write con worktree → rama `orquesta/<agente>/<tarea>`, commit hecho por el runner;
- sandbox de SO (bwrap) cuando backends.yaml lo pide;
- timeout, detección de rate limit, salida vacía y escrituras en modo read-only.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from filelock import FileLock, Timeout

from orquesta import manifest as mf
from orquesta import worktrees
from orquesta.config import load_backends
from orquesta.paths import Paths
from orquesta.schema import Backend, ConfigError, ModeSpec

EXIT_CODES = {"OK": 0, "ERROR": 2, "ORQUESTA_OFF": 3, "NO_GIT": 4, "OCUPADO": 5,
              "FALLO_BACKEND": 6, "VIOLACION_READ_ONLY": 7, "RATE_LIMIT": 75, "TIMEOUT": 124}


@dataclass
class RunRequest:
    agent: str
    cwd: Path
    prompt: str
    task: str | None = None
    wait_s: float = 60.0


@dataclass
class RunResult:
    status: str
    output: str = ""
    detail: str = ""
    latency_s: float = 0.0
    meta: dict = field(default_factory=dict)
    stderr_tail: str = ""

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.status]


@dataclass
class Command:
    argv: list[str]
    stdin: str | None
    temp_files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# --- armado del comando ----------------------------------------------------------------------

def _sandbox_prefix(backend: Backend, spec: ModeSpec | None, mode: str | None, cwd: Path,
                    platform: str, home: Path) -> tuple[list[str], list[str]]:
    if spec is None or not spec.os_sandbox:
        return [], []
    cfg = backend.os_sandbox.get("linux" if platform.startswith("linux") else platform)
    if cfg is None:
        return [], [f"sin sandbox de SO en {platform}: el modo {mode} no está garantizado, solo se "
                    f"detectan escrituras con git status"]
    prefix = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    for item in cfg.writable:
        p = str(home) + item[1:] if item.startswith("~") else item
        if Path(p).exists():
            prefix += ["--bind", p, p]
    if spec.os_sandbox == "workspace":
        prefix += ["--bind", str(cwd), str(cwd)]
    return prefix + ["--die-with-parent", "--"], []


def build_command(backend: Backend, *, mode: str | None, model: str | None, cwd: Path,
                  prompt: str, state_dir: Path, platform: str = sys.platform,
                  home: Path | None = None, extra_flags: list[str] | tuple = ()) -> Command:
    spec = backend.modes.get(mode) if mode else None
    argv = [*backend.command, *backend.common_flags, *extra_flags]
    if spec:
        argv += spec.flags
    if model and backend.model_flag:
        argv += [f.replace("{model}", model) for f in backend.model_flag]
    if backend.cwd_flag:
        argv += [f.replace("{cwd}", str(cwd)) for f in backend.cwd_flag]
    temp: list[Path] = []
    stdin = None
    if backend.prompt.via == "stdin":
        argv += backend.prompt.flag
        stdin = prompt
    else:
        text = prompt
        limit = backend.prompt.max_arg_chars
        if limit and len(prompt) > limit:
            folder = state_dir / "prompts"
            folder.mkdir(parents=True, exist_ok=True)
            f = folder / f"{uuid.uuid4().hex}.md"
            f.write_text(prompt, encoding="utf-8")
            temp.append(f)
            text = (f"Tus instrucciones completas están en el archivo {f}. Leelo entero y seguilo al "
                    f"pie de la letra: esa es tu tarea.")
        argv += [*backend.prompt.flag, text]
    prefix, warnings = _sandbox_prefix(backend, spec, mode, cwd, platform, home or Path.home())
    return Command(prefix + argv, stdin, temp, warnings)


# --- salida ----------------------------------------------------------------------------------

def _tail(text, n: int = 20) -> str:
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return "\n".join((text or "").strip().splitlines()[-n:])


def parse_output(backend: Backend, stdout: str) -> tuple[str, str | None]:
    """(texto de la respuesta, error de parseo o de status)."""
    out = backend.output
    if out.format == "text":
        return stdout.strip(), None
    data = None
    for line in reversed(stdout.strip().splitlines()):
        try:
            data = json.loads(line)
            break
        except ValueError:
            continue
    if not isinstance(data, dict):
        return stdout.strip(), "la salida del backend no es JSON válido"
    text = str(data.get(out.text_key or "", "") or "").strip()
    if out.status_key and out.ok_status is not None:
        status = data.get(out.status_key)
        if status != out.ok_status:
            return text, f"el backend respondió status {status!r} (se esperaba {out.ok_status!r})"
    return text, None


def _rate_limited(backend: Backend, text: str) -> bool:
    low = text.lower()
    return any(p.lower() in low for p in backend.rate_limit_patterns)


def _execute(cmd: Command, backend: Backend, cwd: Path, timeout: float) -> RunResult:
    exe = cmd.argv[0]
    if shutil.which(exe) is None and not Path(exe).is_file():
        hint = " (instalalo con: sudo apt install bubblewrap)" if exe == "bwrap" else ""
        return RunResult("ERROR", detail=f"no encuentro {exe!r} en el PATH{hint}")
    kwargs = {"input": cmd.stdin} if cmd.stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(cmd.argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              **kwargs)
    except subprocess.TimeoutExpired as exc:
        return RunResult("TIMEOUT", detail=f"el backend no terminó en {timeout:g} s",
                         stderr_tail=_tail(exc.stderr))
    finally:
        for f in cmd.temp_files:
            f.unlink(missing_ok=True)
    stdout, stderr = proc.stdout or "", proc.stderr or ""
    text, parse_err = parse_output(backend, stdout)
    failed = proc.returncode != 0 or parse_err is not None or not text
    if failed and _rate_limited(backend, f"{stdout}\n{stderr}"):
        return RunResult("RATE_LIMIT", output=text, stderr_tail=_tail(stderr),
                         detail="el backend reportó límite de uso o de cuota")
    if proc.returncode != 0:
        return RunResult("FALLO_BACKEND", output=text, stderr_tail=_tail(stderr),
                         detail=f"el backend terminó con código {proc.returncode}")
    if parse_err:
        return RunResult("FALLO_BACKEND", output=text, detail=parse_err, stderr_tail=_tail(stderr))
    if not text:
        return RunResult("FALLO_BACKEND", stderr_tail=_tail(stderr),
                         detail="salida vacía (¿volvió el bug de stdout sin TTY?)")
    return RunResult("OK", output=text, stderr_tail=_tail(stderr))


# --- reglas ----------------------------------------------------------------------------------

def find_orquesta_off(cwd: Path) -> Path | None:
    root = worktrees.git_toplevel(cwd)
    p = cwd
    while True:
        if (p / ".orquesta-off").exists():
            return p / ".orquesta-off"
        if root is None or p == root or p.parent == p:
            return None
        p = p.parent


@dataclass(frozen=True)
class TreeState:
    entries: frozenset
    digest: str


def tree_state(cwd: Path) -> TreeState | None:
    root = worktrees.git_toplevel(cwd)
    if root is None:
        return None

    def git(*args: str) -> bytes:
        return subprocess.run(["git", *args], cwd=root, capture_output=True).stdout

    status = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
    entries = frozenset(e.decode(errors="replace") for e in status.split(b"\0") if e)
    h = hashlib.sha256(status + git("diff", "--no-ext-diff", "--binary"))
    for e in sorted(entries):
        if e.startswith("?? "):
            st = (root / e[3:]).stat()
            h.update(f"{e}:{st.st_size}:{st.st_mtime_ns}".encode())
    return TreeState(entries, h.hexdigest())


def compose_prompt(agent: mf.DeployedAgent, task: str, wt: worktrees.Worktree | None) -> str:
    parts = [agent.backend_prompt.strip()] if agent.backend_prompt.strip() else []
    if agent.mode == "read-only":
        parts.append("MODO SOLO LECTURA: no crees, modifiques ni borres archivos. Si hace falta un "
                     "cambio, describilo en tu respuesta.")
    elif wt is not None:
        parts.append(f"Trabajás en un git worktree aislado ({wt.path}, rama {wt.branch}). Hacé solo "
                     f"los cambios pedidos y no hagas commits: el runner commitea.")
    parts.append(task.strip())
    return "\n\n---\n\n".join(parts)


@contextlib.contextmanager
def acquire_slot(paths: Paths, backend: str, limit: int, wait_s: float) -> Iterator[int | None]:
    folder = paths.state_dir / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        for i in range(max(1, limit)):
            lock = FileLock(str(folder / f"{backend}-{i}.lock"))
            try:
                lock.acquire(timeout=0)
            except Timeout:
                continue
            try:
                yield i
            finally:
                lock.release()
            return
        if time.monotonic() >= deadline:
            yield None
            return
        time.sleep(0.2)


@contextlib.contextmanager
def hold_slots(paths: Paths, backend: str, n: int) -> Iterator[None]:
    """Ocupa n lugares de un backend (para tests y diagnóstico)."""
    folder = paths.state_dir / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    locks = [FileLock(str(folder / f"{backend}-{i}.lock")) for i in range(n)]
    for lock in locks:
        lock.acquire(timeout=5)
    try:
        yield
    finally:
        for lock in locks:
            lock.release()


# --- API -------------------------------------------------------------------------------------

def _resolve(paths: Paths, agent: str) -> tuple[mf.DeployedAgent | None, dict | None,
                                                 Backend | None, RunResult | None]:
    manifest = mf.load(paths)
    entry = mf.deployed_agent(manifest, agent)
    if entry is None:
        return None, None, None, RunResult("ERROR", detail=(
            f"el agente {agent!r} no está desplegado (¿está deshabilitado o falta correr "
            f"`orquesta generate`?)"))
    try:
        backends = load_backends(paths)
    except ConfigError as exc:
        return None, None, None, RunResult("ERROR", detail="backends.yaml inválido: "
                                           + "; ".join(exc.errors))
    backend = backends.backends.get(entry.backend)
    if backend is None:
        return None, None, None, RunResult("ERROR", detail=f"backends.yaml no define {entry.backend!r}")
    return entry, manifest, backend, None


def run(req: RunRequest, paths: Paths) -> RunResult:
    t0 = time.monotonic()
    entry, manifest, backend, err = _resolve(paths, req.agent)
    if err:
        return err
    if backend.native:
        return RunResult("ERROR", detail=f"{req.agent} es un subagente nativo de Claude Code: no "
                                         f"pasa por el runner")
    cwd = Path(req.cwd).expanduser().resolve()
    if not cwd.is_dir():
        return RunResult("ERROR", detail=f"no existe el directorio {cwd}")
    off = find_orquesta_off(cwd)
    if off:
        return RunResult("ORQUESTA_OFF", detail=f"existe {off}: en este proyecto no se delega")
    if not req.prompt.strip():
        return RunResult("ERROR", detail="el pedido está vacío")

    meta: dict = {"agent": req.agent, "backend": entry.backend, "mode": entry.mode,
                  "isolation": entry.isolation}
    work_cwd, wt = cwd, None
    if entry.mode == "write" and entry.isolation == "worktree":
        root = worktrees.git_toplevel(cwd)
        if root is None:
            return RunResult("NO_GIT", meta=meta, detail=(
                f"{cwd} no es un repo git: preguntale al usuario si lo inicializa o si el agente "
                f"puede trabajar sin worktree"))
        if not req.task:
            return RunResult("ERROR", meta=meta, detail="los agentes con worktree necesitan --task <slug>")
        try:
            wt = worktrees.create(paths, repo=root, agent=req.agent, task=req.task)
        except worktrees.WorktreeError as exc:
            return RunResult("ERROR", meta=meta, detail=str(exc))
        work_cwd = wt.path / cwd.relative_to(root)
        meta.update(worktree=str(wt.path), branch=wt.branch)

    prompt = compose_prompt(entry, req.prompt, wt)
    limit = mf.max_parallel(manifest, entry.backend)
    with acquire_slot(paths, entry.backend, limit, req.wait_s) as slot:
        if slot is None:
            result = RunResult("OCUPADO", meta=meta, detail=(
                f"los {limit} lugares de {entry.backend} están ocupados (max_parallel): esperá o "
                f"hacelo vos"))
        else:
            spec = backend.modes.get(entry.mode)
            cmd = build_command(backend, mode=entry.mode, model=entry.backend_model, cwd=work_cwd,
                                prompt=prompt, state_dir=paths.state_dir)
            meta["warnings"] = cmd.warnings
            before = tree_state(work_cwd) if entry.mode == "read-only" else None
            result = _execute(cmd, backend, work_cwd, spec.timeout_s if spec else 540)
            if before is not None:
                after = tree_state(work_cwd)
                if after is not None and after.digest != before.digest:
                    changed = sorted(after.entries ^ before.entries) or ["(archivos ya modificados "
                                                                         "cambiaron de contenido)"]
                    result = RunResult("VIOLACION_READ_ONLY", output=result.output, detail=(
                        "el árbol cambió durante una corrida read-only (¿el backend u otro "
                        "proceso?): " + ", ".join(e[3:] if e[2:3] == " " else e for e in changed)))
    if wt is not None:
        try:
            sha = worktrees.commit_all(wt, f"orquesta({req.agent}): {req.task}")
            meta.update(commit=sha or "", diffstat=worktrees.diffstat(wt) if sha else "")
            if not sha:
                meta.setdefault("warnings", []).append("el backend no dejó cambios en el worktree")
        except worktrees.WorktreeError as exc:
            meta.setdefault("warnings", []).append(f"no se pudo commitear en el worktree: {exc}")
    result.meta = {**meta, **result.meta}
    result.latency_s = round(time.monotonic() - t0, 1)
    return result


def _ping(paths: Paths, backend_name: str, backend: Backend, model: str | None) -> RunResult:
    t0 = time.monotonic()
    spec = backend.ping
    model = spec.model or model
    cwd = paths.state_dir / "ping"
    cwd.mkdir(parents=True, exist_ok=True)
    mode = "read-only" if "read-only" in backend.modes else None
    cmd = build_command(backend, mode=mode, model=model, cwd=cwd, prompt=spec.prompt,
                        state_dir=paths.state_dir, extra_flags=spec.extra_flags)
    result = _execute(cmd, backend, cwd, spec.timeout_s)
    if result.status == "OK" and spec.expect.lower() not in result.output.lower():
        result = RunResult("FALLO_BACKEND", output=result.output,
                           detail=f"la respuesta no contiene {spec.expect!r}")
    result.meta = {"backend": backend_name, "model": model or "default", "warnings": cmd.warnings}
    result.latency_s = round(time.monotonic() - t0, 1)
    return result


def ping_backend(paths: Paths, backend_name: str) -> RunResult:
    """Ping mínimo a un backend, sin pasar por un agente (lo usa doctor)."""
    try:
        backend = load_backends(paths).backends.get(backend_name)
    except ConfigError as exc:
        return RunResult("ERROR", detail="backends.yaml inválido: " + "; ".join(exc.errors))
    if backend is None:
        return RunResult("ERROR", detail=f"backends.yaml no define {backend_name!r}")
    return _ping(paths, backend_name, backend, None)


def ping(paths: Paths, agent: str) -> RunResult:
    """Ping real por el backend del agente (botón "Test" de la UI)."""
    entry, _, backend, err = _resolve(paths, agent)
    if err:
        return err
    if backend.native:
        model = entry.wrapper_model if entry.wrapper_model != "inherit" else None
    else:
        model = entry.backend_model
    result = _ping(paths, entry.backend, backend, model)
    result.meta["agent"] = agent
    return result


def format_result(r: RunResult) -> str:
    lines: list[str] = []
    if r.status != "OK":
        lines.append(f"ESTADO: {r.status} — {r.detail}" if r.detail else f"ESTADO: {r.status}")
    if r.output:
        lines += (["", r.output] if lines else [r.output])
    m = r.meta or {}
    lines += ["", "── orquesta run ──"]
    if m.get("agent"):
        lines.append(f"agente: {m['agent']} ({m.get('backend')} · {m.get('mode', 'ping')} · "
                     f"{m.get('isolation', '-')})")
    lines.append(f"estado: {r.status} · {r.latency_s} s")
    for key, label in (("worktree", "worktree"), ("branch", "rama"), ("commit", "commit")):
        if m.get(key):
            lines.append(f"{label}: {m[key]}")
    if m.get("diffstat"):
        lines += ["diffstat:", *("  " + ln for ln in m["diffstat"].splitlines())]
    lines += [f"aviso: {w}" for w in m.get("warnings") or []]
    if r.status != "OK" and r.stderr_tail:
        lines += ["stderr (últimas líneas):", *("  " + ln for ln in r.stderr_tail.splitlines())]
    return "\n".join(lines)
