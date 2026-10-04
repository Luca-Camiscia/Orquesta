"""Qué acepta Claude Code en la definición de un subagente.

Verificado el 2026-10-04 contra Claude Code 2.1.289:
- tools: evento `init` de `claude -p --output-format stream-json --verbose`, más las tools que
  solo existen en sesiones interactivas.
- frontmatter: https://code.claude.com/docs/en/sub-agents

Claude Code ignora en silencio los campos y tools desconocidos, así que orquesta los rechaza antes.
`orquesta doctor` compara este catálogo contra el `init` en vivo y avisa si quedó desactualizado.
"""
from __future__ import annotations

import difflib
import re
from typing import Any

CLAUDE_CODE_VERSION = "2.1.289"

# Tools del evento init en modo headless (`claude -p`). Glob y Grep no existen en esta versión.
BUILTIN_TOOLS = frozenset({
    "Task", "Bash", "CronCreate", "CronDelete", "CronList", "DesignSync", "Edit",
    "EnterWorktree", "ExitWorktree", "ListAgents", "ListMcpResourcesTool", "LSP", "Monitor",
    "NotebookEdit", "PushNotification", "Read", "ReadMcpResourceDirTool", "ReadMcpResourceTool",
    "RemoteTrigger", "ReportFindings", "ScheduleWakeup", "SendMessage", "Skill", "TaskCreate",
    "TaskGet", "TaskList", "TaskStop", "TaskUpdate", "ToolSearch", "WebFetch", "WebSearch", "Write",
})
# Tools que aparecen solo en sesiones interactivas (Agent es el alias de Task).
INTERACTIVE_TOOLS = frozenset({
    "Agent", "AskUserQuestion", "EnterPlanMode", "ExitPlanMode", "Artifact", "ArtifactComments",
    "ArtifactData", "EndConversation", "SendFeedback",
})
KNOWN_TOOLS = BUILTIN_TOOLS | INTERACTIVE_TOOLS
WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit"})

_MCP_RE = re.compile(r"mcp__[A-Za-z0-9_\-]+(?:__(?:[A-Za-z0-9_\-]+|\*))?")
_SPECIFIER_RE = re.compile(r"(?:Agent|Task)\([^()]*\)")


def is_known_tool(tool: str) -> bool:
    return (tool in KNOWN_TOOLS or bool(_MCP_RE.fullmatch(tool))
            or bool(_SPECIFIER_RE.fullmatch(tool)))


def suggest_tool(tool: str) -> str | None:
    match = difflib.get_close_matches(tool, sorted(KNOWN_TOOLS), n=1, cutoff=0.6)
    return match[0] if match else None


# Modelos para el campo `model` del subagente.
MODEL_ALIASES = frozenset({"haiku", "sonnet", "opus", "fable", "inherit"})
_MODEL_ID_RE = re.compile(r"claude-[a-z0-9][a-z0-9.\-]*")


def is_valid_model(model: str) -> bool:
    return model in MODEL_ALIASES or bool(_MODEL_ID_RE.fullmatch(model))


# Frontmatter de subagentes.
FRONTMATTER_FIELDS = frozenset({
    "name", "description", "tools", "disallowedTools", "model", "permissionMode", "maxTurns",
    "skills", "mcpServers", "hooks", "memory", "background", "omitClaudeMd", "effort",
    "isolation", "color", "initialPrompt", "experimental",
})
GENERATED_FIELDS = ("name", "description", "tools", "model")
# `isolation` tiene semántica propia en orquesta (rama orquesta/<agente>/<tarea>).
RESERVED_EXTRA = frozenset(GENERATED_FIELDS) | {"isolation"}
EXTRA_ALLOWED = FRONTMATTER_FIELDS - RESERVED_EXTRA

ENUMS: dict[str, frozenset[str]] = {
    "permissionMode": frozenset({"default", "acceptEdits", "auto", "dontAsk",
                                 "bypassPermissions", "plan", "manual"}),
    "effort": frozenset({"low", "medium", "high", "xhigh", "max"}),
    "memory": frozenset({"user", "project", "local"}),
    "color": frozenset({"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}),
}
ELEVATED_PERMISSION_MODES = frozenset({"acceptEdits", "auto", "dontAsk", "bypassPermissions"})
_BOOL_FIELDS = frozenset({"background", "omitClaudeMd"})


def check_frontmatter_field(who: str, key: str, value: Any) -> list[str]:
    """Errores de un campo de `extra_frontmatter` (lista vacía si está bien)."""
    if key in RESERVED_EXTRA:
        return [f"{who}: extra_frontmatter.{key} lo genera orquesta; usá el campo {key!r} del agente"]
    if key not in EXTRA_ALLOWED:
        by_lower = {f.lower(): f for f in FRONTMATTER_FIELDS}
        guess = by_lower.get(key.lower()) or next(
            iter(difflib.get_close_matches(key, sorted(FRONTMATTER_FIELDS), n=1)), None)
        hint = f" (¿quisiste decir {guess!r}?)" if guess else ""
        return [f"{who}: campo de frontmatter desconocido {key!r}{hint}; Claude Code lo ignoraría "
                f"en silencio"]
    if key in ENUMS and value not in ENUMS[key]:
        return [f"{who}: {key}={value!r} inválido (opciones: {', '.join(sorted(ENUMS[key]))})"]
    if key in _BOOL_FIELDS and not isinstance(value, bool):
        return [f"{who}: {key} tiene que ser true o false"]
    if key == "maxTurns" and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
        return [f"{who}: maxTurns tiene que ser un entero >= 1"]
    if key == "skills" and not (isinstance(value, list) and all(isinstance(s, str) for s in value)):
        return [f"{who}: skills tiene que ser una lista de nombres"]
    if key in ("hooks", "experimental") and not isinstance(value, dict):
        return [f"{who}: {key} tiene que ser un objeto"]
    if key == "mcpServers" and not isinstance(value, (list, dict)):
        return [f"{who}: mcpServers tiene que ser una lista o un objeto"]
    if key == "initialPrompt" and not isinstance(value, str):
        return [f"{who}: initialPrompt tiene que ser texto"]
    if key == "disallowedTools":
        items = [t.strip() for t in value.split(",")] if isinstance(value, str) else value
        if not isinstance(items, list):
            return [f"{who}: disallowedTools tiene que ser una lista o texto separado por comas"]
        bad = [t for t in items if not (is_known_tool(t) or t == "mcp__*"
                                        or re.fullmatch(r"\w+\(.*\)", str(t)))]
        if bad:
            return [f"{who}: disallowedTools tiene tools desconocidas: {', '.join(map(str, bad))}"]
    return []
