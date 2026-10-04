"""Plugins de Claude Code: qué hay instalado, asignación por agente y gestión global auditada.

En Claude Code los plugins son globales a la sesión. Lo que se asigna por agente es lo que traen:
sus skills (frontmatter `skills`, nombres `plugin:skill`) y sus servidores MCP (patrones
`mcp__plugin_<plugin>_<servidor>__*` en `tools`). Instalar exige una auditoría con veredicto ok del
commit EXACTO del repo remoto; desinstalar se niega si algún agente lo usa.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from orquesta.paths import Paths

Runner = Callable[..., tuple[int, str, str]]


class PluginError(Exception):
    pass


@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    sha: str
    install_path: Path
    skills: list[str] = field(default_factory=list)
    mcp_tools: list[str] = field(default_factory=list)


def _mcp_servers(root: Path) -> list[str]:
    out: list[str] = []
    for f in (root / ".mcp.json", root / ".claude-plugin" / "plugin.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            continue
        servers = data.get("mcpServers", data if f.name == ".mcp.json" else {})
        if isinstance(servers, dict):
            out += [s for s in servers if s not in out]
    return out


def installed(paths: Paths) -> dict[str, PluginInfo]:
    try:
        data = json.loads((paths.plugins_dir / "installed_plugins.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    out = {}
    for pid, entries in (data.get("plugins") or {}).items():
        e = (entries or [{}])[0]
        root = Path(e.get("installPath") or "/nonexistent")
        name = pid.split("@")[0]
        skills = sorted(f"{name}:{s.parent.name}" for s in root.glob("skills/*/SKILL.md"))
        mcp = [f"mcp__plugin_{name}_{s}__*" for s in _mcp_servers(root)]
        out[pid] = PluginInfo(pid, name, str(e.get("version", "?")), str(e.get("gitCommitSha") or ""),
                              root, skills, mcp)
    return out


def check_agents(reg, inst: dict[str, PluginInfo]) -> list[str]:
    errs = []
    for a in reg.agents:
        if not a.plugins:
            continue
        if a.is_external:
            errs.append(f"agente {a.name!r}: plugins solo aplica a agentes claude (los wrappers de "
                        f"{a.backend} no usan plugins de Claude Code)")
            continue
        for pid in a.plugins:
            if pid not in inst:
                errs.append(f"agente {a.name!r}: el plugin {pid!r} no está instalado "
                            f"(`orquesta plugins list` muestra los disponibles)")
    return errs


def users(reg, pid: str) -> list[str]:
    return [a.name for a in reg.agents if pid in a.plugins]


def _run(cmd: list[str], timeout: float = 300) -> tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return p.returncode, p.stdout, p.stderr


def _audits(paths: Paths) -> list[dict]:
    from orquesta.doctor import _audits as audits  # mismo parser que usa doctor
    return audits(paths)


def install(paths: Paths, repo: str, plugin_id: str, run: Runner | None = None) -> PluginInfo:
    """Instala un plugin SOLO si el commit actual del repo tiene auditoría con veredicto ok."""
    run = run or _run
    code, out, err = run(["git", "ls-remote", f"https://github.com/{repo}", "HEAD"], 60)
    if code != 0 or not out.strip():
        raise PluginError(f"no pude leer el commit actual de {repo}: {err.strip()[:200]}")
    remote = out.split()[0]
    audit = next((a for a in _audits(paths) if a["plugin"] == plugin_id and a["commit"] == remote), None)
    if audit is None:
        raise PluginError(f"falta la auditoría de {plugin_id} para el commit {remote[:12]}: auditalo "
                          f"primero (audits/<repo>@{remote}.md, que that-it-guy puede hacer) y volvé a "
                          f"intentar")
    if audit.get("verdict") != "ok":
        raise PluginError(f"la auditoría de {plugin_id}@{remote[:12]} dice {audit.get('verdict')!r}: "
                          f"no se instala")
    marketplace = plugin_id.split("@", 1)[1] if "@" in plugin_id else ""
    known = paths.plugins_dir / "known_marketplaces.json"
    try:
        known_names = set(json.loads(known.read_text()))
    except (FileNotFoundError, ValueError):
        known_names = set()
    if marketplace not in known_names:
        code, _, err = run(["claude", "plugin", "marketplace", "add", repo])
        if code != 0:
            raise PluginError(f"`claude plugin marketplace add {repo}` falló: {err.strip()[:200]}")
    code, _, err = run(["claude", "plugin", "install", plugin_id])
    if code != 0:
        raise PluginError(f"`claude plugin install {plugin_id}` falló: {err.strip()[:200]}")
    info = installed(paths).get(plugin_id)
    if info is None or info.sha != remote:
        run(["claude", "plugin", "uninstall", plugin_id])
        got = info.sha[:12] if info else "?"
        raise PluginError(f"el commit instalado ({got}) no coincide con el auditado ({remote[:12]}): "
                          f"lo desinstalé")
    return info


def uninstall(paths: Paths, plugin_id: str, run: Runner | None = None) -> None:
    run = run or _run
    from orquesta.config import load_registry
    if plugin_id not in installed(paths):
        raise PluginError(f"{plugin_id} no está instalado")
    using = users(load_registry(paths), plugin_id)
    if using:
        raise PluginError(f"{plugin_id} lo usan estos agentes: {', '.join(using)}. Sacáselo primero "
                          f"(y aplicá) antes de desinstalarlo.")
    code, _, err = run(["claude", "plugin", "uninstall", plugin_id])
    if code != 0:
        raise PluginError(f"`claude plugin uninstall {plugin_id}` falló: {err.strip()[:200]}")
