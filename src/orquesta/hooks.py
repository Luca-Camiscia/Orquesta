"""Hooks del plugin de Claude Code (`orquesta hook …`).

- `session-start`: inyecta las reglas del orquestador (`context/orquestador.md` del plugin) como
  `additionalContext`. Las sesiones de un agente (`--agent orquesta:<n>`, con `agent_type` en el stdin)
  no las reciben, y con `.orquesta-off` se inyecta solo la línea de orquestación desactivada.
- `pre-bash`: devuelve `permissionDecision: "ask"` ante comandos que confirman acciones de orquesta
  (`--confirm`, `--forzar`) o que parecen esquivar la CLI. Es un aviso para un modelo que se equivoca,
  no un sandbox: la confirmación real la lee la CLI de /dev/tty (docs/plugin-plan.md, decisión 3).
  En `claude -p` el "ask" equivale a denegar (docs/headless.md).

Los dos son tolerantes: un stdin inválido o cualquier error no produce traceback, sale 0 sin decidir nada.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from orquesta.paths import Paths

# Comandos de orquesta que confirman algo que el usuario tiene que ver. El flag puede venir entre comillas,
# pegado a `;`, `&&`, `|` o `)`, o abreviado (la CLI ya no acepta abreviaturas, pero el aviso no cuesta).
_FLAG = r"(?<![\w-])--(?:conf(?:i(?:rm?)?)?|forz(?:ar?)?)(?![\w-])"
_CONFIRMA = re.compile(
    rf"\borquesta\b[^\n]*?{_FLAG}"
    r"|\borquesta\b[^\n]*?\bplugins\s+(?:install|uninstall)\b[^\n]*?(?<![\w-])--yes(?![\w-])"
    r"|\borquesta\b[^\n]*?\bwt\s+clean\b[^\n]*?(?<![\w-])--force(?![\w-])")
# Formas de llamar a la lógica de confirmación sin pasar por el binario de siempre. Se buscan solo
# junto a lo que confirma algo, para no frenar diagnósticos (`python -c "from orquesta import modelos"`).
_ACCIONES = r"(?:merge|merge_sha|descartar|discard|cerrar_si_vigente|pedir_confirmacion|leer_tty|apply_plan|set_fase|migrar)"
_EVASION = re.compile(
    rf"(?:-m\s+orquesta\b|\b(?:from|import)\s+orquesta\b)[\s\S]*?\b{_ACCIONES}\b"  # cruza líneas (heredocs)
    r"|\.venv/bin/orquesta\s+(?:merge|descartar|migrar|generate|wt\s+clean)\b"
    r"|(?:^|[\s;&|(])(?:script|unbuffer|expect)\s[^\n]*\borquesta\b"
)
_RAZON = ("Este comando confirma una acción de orquesta (merge, descarte, generate --confirm, migrar) o "
          "la llama por un camino raro. Las confirmaciones las corre el usuario en su terminal: devolvele "
          "el comando exacto que dio la CLI en datos.comando y no lo corras vos.")


def _leer_stdin(stdin) -> dict:
    try:
        data = json.loads(stdin.read() or "{}")
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def session_start(paths: Paths, data: dict) -> dict | None:
    """Salida del hook SessionStart, o None si no hay que inyectar nada."""
    if data.get("agent_type"):
        return None  # sesión de un agente: las reglas son del orquestador
    from orquesta import runner
    from orquesta.render import DISABLED_LINE
    cwd = Path(str(data.get("cwd") or Path.cwd()))
    if runner.find_orquesta_off(cwd) is not None:
        texto = DISABLED_LINE
    else:
        try:
            texto = (paths.plugin_dir / "context" / "orquestador.md").read_text(encoding="utf-8")
        except OSError:
            return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": texto}}


def pre_bash(data: dict) -> dict | None:
    """Salida del hook PreToolUse sobre Bash, o None si el comando pasa sin decisión."""
    if data.get("tool_name") not in (None, "Bash"):
        return None
    comando = (data.get("tool_input") or {}).get("command")
    if not isinstance(comando, str):
        return None
    comando = comando.replace("\\\n", " ")  # continuaciones de línea con `\`
    if not (_CONFIRMA.search(comando) or _EVASION.search(comando)):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                                   "permissionDecisionReason": _RAZON}}


def run(evento: str, paths: Paths, stdin=None, stdout=None) -> int:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        data = _leer_stdin(stdin)
        salida = session_start(paths, data) if evento == "session-start" else pre_bash(data)
        if salida is not None:
            print(json.dumps(salida, ensure_ascii=False), file=stdout)
    except Exception:  # noqa: BLE001 — un hook roto no puede trabar la sesión de Claude Code
        pass
    return 0
