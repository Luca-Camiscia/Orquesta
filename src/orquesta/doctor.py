"""orquesta doctor: chequeos de salud deterministas del ecosistema (sin LLM).

Cada chequeo devuelve ok | warn | fail, con mensaje y sugerencia. Doctor nunca instala, actualiza ni
loguea nada: solo sugiere el comando exacto. Los chequeos que gastan cuota son "live" (ping de agy,
init de Claude Code): se cachean 10 minutos y se pueden saltear con live=False. La auth también se
cachea 10 minutos, y la última versión publicada, 24 horas.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from ruamel.yaml import YAML

from orquesta import catalog, manifest as mf, worktrees
from orquesta.claude_md import extract_inner, MarkerError
from orquesta.config import load_all, load_backends
from orquesta.generate import build_plan
from orquesta.paths import Paths
from orquesta.schema import ConfigError

TTL_AUTH = 600
TTL_LIVE = 600
TTL_LATEST = 86400
GENERATE_HINT = ("Corré `orquesta generate --dry-run`, revisá el diff y aplicá con "
                 "`orquesta generate -m \"...\"` (o llamá a that_it_guy).")
UPDATE_HINTS = {
    "claude": "`claude update`",
    "codex": "`npm install -g @openai/codex@latest`",
    "agy": "`agy update`",
}
LOGIN_HINTS = {
    "codex": "Corré `! codex login` (cuenta ChatGPT). El login lo hacés vos.",
    "agy": "Corré `! agy` y hacé el sign-in con Google. El login lo hacés vos.",
}


@dataclass
class Check:
    id: str
    name: str
    status: str  # ok | warn | fail
    message: str
    suggestion: str = ""
    cached: bool = False


@dataclass
class Report:
    checks: list[Check]
    generated_at: str

    @property
    def overall(self) -> str:
        statuses = {c.status for c in self.checks}
        return "fail" if "fail" in statuses else "warn" if "warn" in statuses else "ok"

    def to_json(self) -> dict:
        return {"overall": self.overall, "generated_at": self.generated_at,
                "checks": [asdict(c) for c in self.checks]}

    def render(self) -> str:
        icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}
        lines = [f"orquesta doctor · {self.generated_at}", ""]
        for c in self.checks:
            lines.append(f"{icon[c.status]} {c.name}: {c.message}" + ("  (cache)" if c.cached else ""))
            if c.suggestion and c.status != "ok":
                lines.append(f"     → {c.suggestion}")
        n = {s: sum(c.status == s for c in self.checks) for s in ("ok", "warn", "fail")}
        lines += ["", f"Resumen: {n['ok']} ok · {n['warn']} warn · {n['fail']} fail → "
                      f"{self.overall.upper()}"]
        return "\n".join(lines)


class Env:
    """Dependencias externas de doctor (los tests las reemplazan)."""

    platform = sys.platform

    def run(self, cmd: list[str], timeout: float = 60, cwd: Path | None = None) -> tuple[int, str, str]:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
                               stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            return 127, "", f"no encuentro {cmd[0]!r} en el PATH"
        except subprocess.TimeoutExpired:
            return 124, "", f"{cmd[0]} no respondió en {timeout:g} s"
        return p.returncode, p.stdout, p.stderr

    def which_all(self, name: str) -> list[str]:
        seen, out = set(), []
        for d in os.environ.get("PATH", "").split(os.pathsep):
            p = Path(d) / name
            if p.is_file() and os.access(p, os.X_OK):
                real = os.path.realpath(p)
                if real not in seen:
                    seen.add(real)
                    out.append(str(p))
        return out

    def http_get(self, url: str, timeout: float = 5) -> str:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (URLs fijas)
            return resp.read().decode()

    def claude_init(self) -> dict | None:
        cmd = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", "haiku",
               "Reply OK"]
        code, out, _ = self.run(cmd, timeout=120, cwd=Path(tempfile.gettempdir()))
        for line in out.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") == "system" and d.get("subtype") == "init":
                return {"agents": d.get("agents", []), "tools": d.get("tools", [])}
        return None

    def ping_backend(self, paths: Paths, backend: str):
        from orquesta.runner import ping_backend
        return ping_backend(paths, backend)

    def now(self) -> float:
        return time.time()


class _Cache:
    def __init__(self, path: Path, now: float, use_cache: bool):
        self.path, self.now, self.use = path, now, use_cache
        try:
            self.data = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def get_or(self, key: str, ttl: float | None, compute: Callable[[], dict]) -> tuple[dict, bool]:
        hit = self.data.get(key)
        if self.use and hit and (ttl is None or self.now - hit["ts"] < ttl):
            return hit["value"], True
        value = compute()
        self.data[key] = {"ts": self.now, "value": value}
        return value, False

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))


@dataclass
class _Ctx:
    paths: Paths
    deps: Env
    cache: _Cache
    reg: object = None
    backends: object = None
    plan: object = None
    extra: dict = field(default_factory=dict)


def _ver(text: str) -> str | None:
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return m.group(0) if m else None


def _vt(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))


def _sha(text: str | None) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:8]


# --- chequeos --------------------------------------------------------------------------------

def _registro(ctx: _Ctx) -> list[Check]:
    try:
        ctx.backends = load_backends(ctx.paths)
    except ConfigError:
        ctx.backends = None
    try:
        ctx.reg, ctx.backends = load_all(ctx.paths)
    except ConfigError as exc:
        errs = exc.errors
        msg = "; ".join(errs[:4]) + (f" (y {len(errs) - 4} más)" if len(errs) > 4 else "")
        return [Check("registro", "Registro", "fail", msg,
                      "Corregí orquesta.yaml o backends.yaml; `orquesta generate --dry-run` muestra "
                      "todos los errores.")]
    n = len(ctx.reg.agents)
    return [Check("registro", "Registro", "ok",
                  f"orquesta.yaml ({n} agentes, {len(ctx.reg.enabled_agents())} habilitados) y "
                  f"backends.yaml válidos")]


def _sync(ctx: _Ctx) -> list[Check]:
    if ctx.reg is None:
        msg = "no se puede verificar: el registro tiene errores"
        return [Check("agentes", "Agentes desplegados", "warn", msg, "Arreglá el registro primero."),
                Check("claude-md", "Bloque de CLAUDE.md", "warn", msg, "Arreglá el registro primero.")]
    paths = ctx.paths
    plan = ctx.plan = build_plan(paths)
    manifest = mf.load(paths)
    registry_text = paths.registry.read_text(encoding="utf-8")
    unapplied = manifest is None or manifest.get("registry_sha256") != mf.sha256(registry_text)

    md = [c for c in plan.changes if c.path.parent == paths.agents_dir and c.path.suffix == ".md"]
    missing = [c.path.name for c in md if c.kind == "crear"]
    extra = [c.path.name for c in md if c.kind == "borrar"]
    changed = [f"{c.path.name} (esperado {_sha(c.new)} ≠ encontrado {_sha(c.old)})"
               for c in md if c.kind == "modificar"]
    other = any(c.path == paths.manifest for c in plan.changes)
    parts, status = [], "warn"
    if unapplied:
        parts.append("orquesta.yaml tiene cambios sin aplicar")
    if missing:
        parts.append(("por crear: " if unapplied else "faltan: ") + ", ".join(missing))
        if not unapplied:
            status = "fail"
    if changed:
        parts.append("desincronizados: " + ", ".join(changed))
    if extra:
        parts.append("sobran: " + ", ".join(extra))
    if other and not parts:
        parts.append("el manifiesto de deploy no coincide con lo desplegado")
    if plan.violations:
        parts.append("esperan confirmación explícita: " + ", ".join(v.agent for v in plan.violations))
    if parts:
        agentes = Check("agentes", "Agentes desplegados", status, "; ".join(parts), GENERATE_HINT)
    else:
        n = len(ctx.reg.enabled_agents())
        agentes = Check("agentes", "Agentes desplegados", "ok",
                        f"{n} agentes sincronizados con orquesta.yaml (hash OK)")

    marker = next((e for e in plan.errors if "marcador" in e), None)
    block_change = next((c for c in plan.changes if c.path == paths.claude_md), None)
    if marker:
        cmd = Check("claude-md", "Bloque de CLAUDE.md", "fail", marker,
                    "Arreglá los marcadores a mano; orquesta no toca el archivo hasta que estén bien.")
    elif block_change is None:
        cmd = Check("claude-md", "Bloque de CLAUDE.md", "ok", "bloque presente y sincronizado")
    else:
        try:
            had = block_change.old is not None and extract_inner(block_change.old) is not None
        except MarkerError:
            had = False
        if not had:
            cmd = Check("claude-md", "Bloque de CLAUDE.md", "fail",
                        "falta el bloque de orquesta en ~/.claude/CLAUDE.md", GENERATE_HINT)
        else:
            why = "por cambios sin aplicar" if unapplied else "editado a mano o desincronizado"
            cmd = Check("claude-md", "Bloque de CLAUDE.md", "warn", f"el bloque está desactualizado "
                        f"({why})", GENERATE_HINT)
    return [agentes, cmd]


def _latest(ctx: _Ctx, name: str, spec) -> str | None:
    def compute() -> dict:
        if spec is None:
            return {"v": None}
        if spec.npm:
            code, out, _ = ctx.deps.run(["npm", "view", spec.npm, "version"], 30)
            return {"v": _ver(out) if code == 0 else None}
        if spec.cmd and spec.pattern:
            code, out, _ = ctx.deps.run(list(spec.cmd), 30)
            m = re.search(spec.pattern, out or "", re.M)
            return {"v": m.group(1) if code == 0 and m else None}
        return {"v": None}
    value, _ = ctx.cache.get_or(f"latest:{name}", TTL_LATEST, compute)
    return value["v"]


def _http_latest(ctx: _Ctx, key: str, url: str, pick: Callable[[list], str | None]) -> str | None:
    def compute() -> dict:
        try:
            return {"v": pick(json.loads(ctx.deps.http_get(url)))}
        except Exception:  # sin red o formato inesperado: no es un error del ecosistema
            return {"v": None}
    value, _ = ctx.cache.get_or(key, TTL_LATEST, compute)
    return value["v"]


def _versions(ctx: _Ctx) -> list[Check]:
    out: list[Check] = []
    backends = ctx.backends.backends if ctx.backends else {}
    for name, b in backends.items():
        if not b.version_cmd:
            continue
        cid, title = f"version-{name}", f"Versión de {name}"
        code, stdout, stderr = ctx.deps.run(list(b.version_cmd), 20)
        installed = _ver(stdout) if code == 0 else None
        if installed is None:
            out.append(Check(cid, title, "fail", f"{name} no responde: {(stderr or stdout).strip()[:120]}",
                             f"Instalalo o revisá el PATH ({UPDATE_HINTS.get(name, '')})."))
            continue
        latest = _latest(ctx, name, b.latest_version)
        if latest and _vt(latest) > _vt(installed):
            out.append(Check(cid, title, "warn", f"{name} {installed} → hay versión nueva {latest}",
                             f"Actualizalo vos con {UPDATE_HINTS.get(name, 'el instalador oficial')}; "
                             f"doctor no instala nada."))
        else:
            note = "al día" if latest else "no pude chequear si hay versión nueva"
            out.append(Check(cid, title, "ok", f"{name} {installed} ({note})"))

    code, stdout, _ = ctx.deps.run(["node", "--version"], 20)
    node = _ver(stdout) if code == 0 else None
    if node is None:
        out.append(Check("version-node", "Versión de node", "fail", "node no está instalado",
                         "Instalá Node >= 18.18 (por ejemplo con nvm)."))
    elif _vt(node) < (18, 18, 0):
        out.append(Check("version-node", "Versión de node", "fail", f"node {node} < 18.18",
                         "Actualizá Node (nvm install --lts)."))
    else:
        major = _vt(node)[0]
        latest = _http_latest(ctx, f"latest:node{major}", "https://nodejs.org/dist/index.json",
                              lambda rows: next((_ver(r["version"]) for r in rows
                                                 if _vt(_ver(r["version"]))[0] == major), None))
        if latest and _vt(latest) > _vt(node):
            out.append(Check("version-node", "Versión de node", "warn",
                             f"node {node} → hay un parche más nuevo de Node {major}: {latest}",
                             f"Actualizalo vos (nvm install {major})."))
        else:
            out.append(Check("version-node", "Versión de node", "ok", f"node {node} (>= 18.18)"))

    py = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info < (3, 11):
        out.append(Check("version-python", "Versión de python", "fail", f"python {py} < 3.11",
                         "Usá Python >= 3.11 para orquesta."))
    else:
        cycle = f"{sys.version_info[0]}.{sys.version_info[1]}"
        latest = _http_latest(ctx, f"latest:python{cycle}", "https://endoflife.date/api/python.json",
                              lambda rows: next((r["latest"] for r in rows if r["cycle"] == cycle), None))
        if latest and _vt(latest) > _vt(py):
            out.append(Check("version-python", "Versión de python", "warn",
                             f"python {py} → hay un parche más nuevo de {cycle}: {latest}",
                             "Actualizalo vos; doctor no instala nada."))
        else:
            out.append(Check("version-python", "Versión de python", "ok", f"python {py} (>= 3.11)"))
    return out


def _binarios(ctx: _Ctx) -> list[Check]:
    problems, snap = [], False
    for name in ("claude", "codex", "agy"):
        found = ctx.deps.which_all(name)
        if len(found) > 1:
            problems.append(f"{name} tiene {len(found)} binarios en el PATH ({', '.join(found)}); "
                            f"gana {found[0]}")
        if name == "codex" and any(p.startswith("/snap/") for p in found):
            snap = True
            problems.append("hay un snap de codex de terceros (no es de OpenAI)")
    if not problems:
        return [Check("binarios", "Binarios en el PATH", "ok", "un solo binario oficial por herramienta")]
    hint = ("Sacá el snap con `! sudo snap remove --purge codex` (lo corrés vos)." if snap
            else "Dejá un solo binario por herramienta en el PATH.")
    return [Check("binarios", "Binarios en el PATH", "warn", "; ".join(problems), hint)]


def _auth(ctx: _Ctx) -> list[Check]:
    out: list[Check] = []
    backends = ctx.backends.backends if ctx.backends else {}
    for name in ("codex", "agy"):
        b = backends.get(name)
        if b is None or b.auth_check is None:
            continue

        def compute(b=b, name=name) -> dict:
            code, stdout, stderr = ctx.deps.run(list(b.auth_check.cmd), 45)
            ok = code == 0 and b.auth_check.ok_pattern.lower() in (stdout + stderr).lower()
            if ok:
                return {"status": "ok", "message": f"{name}: sesión activa"}
            detail = (stderr or stdout).strip().splitlines()[-1:] or ["sin detalle"]
            return {"status": "fail", "message": f"{name}: sin sesión o vencida ({detail[0][:120]})"}
        value, cached = ctx.cache.get_or(f"auth:{name}", TTL_AUTH, compute)
        out.append(Check(f"auth-{name}", f"Auth de {name}", value["status"], value["message"],
                         LOGIN_HINTS.get(name, "") if value["status"] != "ok" else "", cached))
    return out


def _agy_stdout(ctx: _Ctx) -> list[Check]:
    if not ctx.backends or "agy" not in ctx.backends.backends:
        return []

    def compute() -> dict:
        r = ctx.deps.ping_backend(ctx.paths, "agy")
        if r.status == "OK":
            return {"status": "ok", "message": f"agy responde por stdout sin TTY (PONG en {r.latency_s} s)"}
        if r.status == "RATE_LIMIT":
            return {"status": "warn", "message": f"no se pudo verificar: cuota de agy agotada ({r.detail})"}
        if "vacía" in r.detail:
            return {"status": "fail", "message": "agy devolvió stdout vacío sin TTY: volvió el bug"}
        return {"status": "fail", "message": f"el ping de agy falló: {r.status} — {r.detail}"}
    value, cached = ctx.cache.get_or("live:agy-stdout", TTL_LIVE, compute)
    hint = ("" if value["status"] == "ok" else
            "Probá `python scripts/smoke_headless.py --only agy --fallbacks` y actualizá backends.yaml "
            "con la variante que funcione (o llamá a that_it_guy).")
    return [Check("agy-stdout", "agy sin TTY (backends.yaml)", value["status"], value["message"],
                  hint, cached)]


def _claude_live(ctx: _Ctx) -> list[Check]:
    init, cached = ctx.cache.get_or("live:claude-init", TTL_LIVE,
                                    lambda: {"init": ctx.deps.claude_init()})
    init = init["init"]
    if not init:
        msg = "no pude leer el evento init de `claude -p`"
        return [Check("claude-agentes", "Claude Code carga los agentes", "warn", msg,
                      "Revisá que `claude -p` funcione.", cached)]
    loaded = set(init.get("agents") or [])
    deployed = sorted(((mf.load(ctx.paths) or {}).get("agents") or {}).keys())
    missing = [a for a in deployed if a not in loaded]
    if missing:
        agentes = Check("claude-agentes", "Claude Code carga los agentes", "fail",
                        "Claude Code no cargó: " + ", ".join(missing),
                        "Reiniciá la sesión; si sigue, revisá el frontmatter con `orquesta generate "
                        "--dry-run` (o llamá a that_it_guy).", cached)
    else:
        agentes = Check("claude-agentes", "Claude Code carga los agentes", "ok",
                        f"carga los {len(deployed)} agentes de orquesta", cached=cached)

    live = {t for t in init.get("tools") or [] if not t.startswith("mcp__")}
    new = sorted(live - catalog.KNOWN_TOOLS)
    gone = sorted(catalog.BUILTIN_TOOLS - live)
    used = {t for a in (ctx.reg.enabled_agents() if ctx.reg else []) for t in a.tools}
    broken = sorted(t for t in used if t in catalog.BUILTIN_TOOLS and t not in live)
    if broken:
        tools = Check("catalogo-tools", "Catálogo de tools", "fail",
                      "agentes usan tools que Claude Code ya no tiene: " + ", ".join(broken),
                      "Actualizá las tools en orquesta.yaml y src/orquesta/catalog.py (that_it_guy).",
                      cached)
    elif new or gone:
        parts = ([f"nuevas: {', '.join(new)}"] if new else []) + \
                ([f"ya no existen: {', '.join(gone)}"] if gone else [])
        tools = Check("catalogo-tools", "Catálogo de tools", "warn",
                      "el catálogo quedó desactualizado (" + "; ".join(parts) + ")",
                      "Actualizá src/orquesta/catalog.py (pedíselo a that_it_guy).", cached)
    else:
        tools = Check("catalogo-tools", "Catálogo de tools", "ok",
                      f"coincide con Claude Code ({len(live)} tools built-in)", cached=cached)
    return [agentes, tools]


def _sandbox(ctx: _Ctx) -> list[Check]:
    backends = ctx.backends.backends if ctx.backends else {}
    needed = [n for n, b in backends.items()
              if "linux" in b.os_sandbox and any(m.os_sandbox for m in b.modes.values())]
    title = "Sandbox de SO (bwrap)"
    if not needed or not ctx.deps.platform.startswith("linux"):
        return [Check("sandbox", title, "ok", "no hace falta en esta plataforma")]
    if not ctx.deps.which_all("bwrap"):
        return [Check("sandbox", title, "fail", f"falta bwrap y lo necesita: {', '.join(needed)}",
                      "Instalá bubblewrap: `! sudo apt install bubblewrap` (lo corrés vos).")]
    code, _, err = ctx.deps.run(["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "true"], 15)
    if code != 0:
        return [Check("sandbox", title, "fail", f"bwrap no funciona: {err.strip()[:150]}",
                      "¿AppArmor restringe los user namespaces? Revisá "
                      "`sysctl kernel.apparmor_restrict_unprivileged_userns`.")]
    return [Check("sandbox", title, "ok", f"bwrap funciona (read-only garantizado para {', '.join(needed)})")]


def _audits(paths: Paths) -> list[dict]:
    out = []
    if not paths.audits_dir.is_dir():
        return out
    for f in sorted(paths.audits_dir.glob("*.md")):
        m = re.match(r"---\n(.*?)\n---\n", f.read_text(encoding="utf-8"), re.S)
        if m:
            try:
                data = YAML(typ="safe", pure=True).load(m.group(1))
            except Exception:
                continue
            if isinstance(data, dict) and data.get("plugin"):
                out.append({**data, "commit": str(data.get("commit", "")), "file": f.name})
    return out


def _plugins(ctx: _Ctx) -> list[Check]:
    try:
        installed = json.loads((ctx.paths.plugins_dir / "installed_plugins.json").read_text())
        installed = installed.get("plugins", {})
    except (FileNotFoundError, json.JSONDecodeError):
        installed = {}
    audits = _audits(ctx.paths)
    out, official = [], []
    for pid, entries in sorted(installed.items()):
        e = (entries or [{}])[0]
        sha, version = str(e.get("gitCommitSha") or ""), e.get("version", "?")
        mine = [a for a in audits if a["plugin"] == pid]
        cid, title = f"plugin-{pid}", f"Plugin {pid}"
        if mine:
            match = next((a for a in mine if a["commit"] == sha), None)
            if match and match.get("verdict") == "no-instalar":
                out.append(Check(cid, title, "fail", f"está instalado, pero su auditoría "
                                 f"({match['file']}) dice no instalar",
                                 f"Desinstalalo: `claude plugin uninstall {pid}` (o re-auditá)."))
            elif match:
                out.append(Check(cid, title, "ok", f"{version}: commit {sha[:7]} = auditado"))
            else:
                audited = ", ".join(a["commit"][:7] for a in mine)
                out.append(Check(cid, title, "warn", f"{version}: commit instalado {sha[:7] or '?'} ≠ "
                                 f"auditado {audited}",
                                 "Auditá el diff entre ambos commits antes de seguir usándolo "
                                 "(pedíselo a that_it_guy)."))
        elif pid.endswith("@claude-plugins-official"):
            official.append(pid.split("@")[0])
        else:
            out.append(Check(cid, title, "warn", f"{version}: sin auditoría",
                             "Auditalo (audits/<repo>@<sha>.md) o desinstalalo."))
    msg = (f"{len(official)} plugins oficiales de Anthropic: {', '.join(official)}" if official
           else "no hay plugins oficiales instalados")
    return [*out, Check("plugins-oficiales", "Plugins oficiales", "ok", msg)]


def _orquesta_path(ctx: _Ctx) -> list[Check]:
    if ctx.deps.which_all("orquesta"):
        return [Check("orquesta-path", "Comando orquesta", "ok", "`orquesta` está en el PATH")]
    return [Check("orquesta-path", "Comando orquesta", "warn",
                  "el comando `orquesta` no está en el PATH (los agentes externos lo necesitan)",
                  "`uv tool install -e ~/orquesta` y después `uv tool update-shell`.")]


def _git(ctx: _Ctx) -> list[Check]:
    code, out, err = ctx.deps.run(["git", "status", "--porcelain"], 20, cwd=ctx.paths.home)
    if code != 0:
        return [Check("git", "Repo de orquesta", "fail", f"git falló: {err.strip()[:120]}",
                      "¿~/orquesta sigue siendo un repo git?")]
    files = [ln[3:] for ln in out.splitlines() if ln.strip()]
    if not files:
        return [Check("git", "Repo de orquesta", "ok", "repo limpio")]
    shown = ", ".join(files[:8]) + (f" (y {len(files) - 8} más)" if len(files) > 8 else "")
    return [Check("git", "Repo de orquesta", "warn", f"cambios sin commitear: {shown}",
                  "Commitealos o descartalos; si es orquesta.yaml sin aplicar, corré "
                  "`orquesta generate`.")]


def _tests(ctx: _Ctx) -> list[Check]:
    home = ctx.paths.home
    _, head, _ = ctx.deps.run(["git", "rev-parse", "HEAD"], 20, cwd=home)
    _, status, _ = ctx.deps.run(["git", "status", "--porcelain"], 20, cwd=home)
    _, diff, _ = ctx.deps.run(["git", "diff"], 20, cwd=home)
    sig = hashlib.sha256((head + status + diff).encode()).hexdigest()[:16]

    def compute() -> dict:
        # sin -q: ya viene en addopts (con -qq pytest no imprime el resumen)
        code, out, err = ctx.deps.run([sys.executable, "-m", "pytest", "-x", "-p",
                                       "no:cacheprovider"], 900, cwd=home)
        lines = (out + err).strip().splitlines() or ["sin salida"]
        summary = next((ln for ln in reversed(lines)
                        if re.search(r"\d+ (passed|failed|error)", ln)), lines[-1])
        return {"status": "ok" if code == 0 else "fail", "message": summary.strip("= ")}
    value, cached = ctx.cache.get_or(f"tests:{sig}", None, compute)
    hint = "" if value["status"] == "ok" else "Corré `uv run pytest` en ~/orquesta y arreglá lo que falle."
    return [Check("tests", "Tests de orquesta", value["status"], value["message"], hint, cached)]


def _worktrees(ctx: _Ctx) -> list[Check]:
    items = worktrees.list_worktrees(ctx.paths)
    orphans = worktrees.orphans(ctx.paths)
    if orphans:
        return [Check("worktrees", "Worktrees orquesta/*", "warn",
                      "; ".join(f"{o.branch}: {o.reason}" for o in orphans),
                      "`orquesta wt list` para verlos; `orquesta wt clean <rama>` o `orquesta wt prune`.")]
    return [Check("worktrees", "Worktrees orquesta/*", "ok",
                  f"{len(items)} worktree(s) abiertos, ninguno huérfano")]


# (función, es live, prefijos de los ids que produce). _registro corre siempre: carga el registro y
# los backends que usan los demás chequeos.
CHECKS: list[tuple[Callable[[_Ctx], list[Check]], bool, tuple[str, ...]]] = [
    (_registro, False, ("registro",)),
    (_sync, False, ("agentes", "claude-md")),
    (_versions, False, ("version-",)),
    (_binarios, False, ("binarios",)),
    (_auth, False, ("auth-",)),
    (_agy_stdout, True, ("agy-stdout",)),
    (_claude_live, True, ("claude-agentes", "catalogo-tools")),
    (_sandbox, False, ("sandbox",)),
    (_plugins, False, ("plugin",)),
    (_orquesta_path, False, ("orquesta-path",)),
    (_git, False, ("git",)),
    (_tests, False, ("tests",)),
    (_worktrees, False, ("worktrees",)),
]


def _wanted(only: str | None, prefixes: tuple[str, ...]) -> bool:
    return not only or any(p.startswith(only) or only.startswith(p) for p in prefixes)


def run_doctor(paths: Paths, *, deps: Env | None = None, use_cache: bool = True, live: bool = True,
               only: str | None = None) -> Report:
    deps = deps or Env()
    cache = _Cache(paths.state_dir / "doctor-cache.json", deps.now(), use_cache)
    ctx = _Ctx(paths, deps, cache)
    checks: list[Check] = []
    for fn, is_live, prefixes in CHECKS:
        if (is_live and not live) or (fn is not _registro and not _wanted(only, prefixes)):
            continue
        checks.extend(fn(ctx))
    cache.save()
    if only:
        checks = [c for c in checks if c.id == only or c.id.startswith(only)]
    return Report(checks, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
