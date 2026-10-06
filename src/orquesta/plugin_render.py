"""Contenido determinista del marketplace local de Claude Code, sin escribir en disco."""
from __future__ import annotations

import json
import shlex
from typing import Any

from orquesta import render
from orquesta.schema import Registry, parse_backends


_COMMANDS = {
    "plan": (
        "Armar o retomar el plan del proyecto",
        "Usá orquesta:claude-interrogador y orquesta:claude-arquitecto para crear o retomar "
        "el plan. Escribilo en `.orquesta/plan.md` y pedí un único OK antes de ejecutar.",
    ),
    "reunion": (
        "Consultar varias perspectivas",
        "Armá el brief y corré `orquesta reunion` con `run_in_background`. "
        "Evitá un juez si participan las tres familias; verificá el resultado.",
    ),
    "rrhh": (
        "Consultar Recursos Humanos",
        "Consultá en silencio `orquesta rrhh triage --json` y usá el sobre JSON "
        "para decidir el siguiente paso. Las confirmaciones las corre el usuario.",
    ),
    "tareas": (
        "Consultar el estado de las tareas",
        "Corré `orquesta tareas --json` y resumí tareas, ramas y pendientes. "
        "Si hace falta confirmación, devolvé el comando exacto para la segunda terminal.",
    ),
    "mood": (
        "Cambiar el mood actual",
        "Aplicá el mood pedido: silencioso, ejecutivo, transparente, debate o docente. "
        "Cambia solo la presentación; mantené la verificación y los frenos.",
    ),
}

_SKILL = """---
name: orquesta-cli
description: Interpretar la salida JSON de orquesta y sus confirmaciones humanas
---

# Contrato JSON de orquesta

Usá `--json` cuando esté disponible. La CLI devuelve un sobre JSON en una sola
línea, con Unicode sin escapar y estas claves siempre presentes:

| Clave | Contenido |
|---|---|
| `version` | Versión del contrato: `1`. |
| `comando` | Comando que produjo la respuesta. |
| `estado` | Estado de la tabla siguiente. |
| `generacion` | Identificador de generación, o `null` si no corresponde. |
| `datos` | Resultado; por defecto `{}`. |
| `errores` | Errores; por defecto `[]`. |
| `avisos` | Avisos; por defecto `[]`. |

| Estado | Qué hacer | Exit code |
|---|---|---|
| `OK` | Interpretá el resultado y verificálo. | `0` |
| `NECESITO_CONFIRMACION` | Devolvé el comando sugerido para el usuario. | `3` |
| `BLOQUEADO` | Informá el bloqueo y frená. | `3` |
| `RATE_LIMIT` | Avisá que el backend alcanzó su límite. | `1` |
| `ORQUESTA_OFF` | Respetá la delegación desactivada del proyecto. | `1` |
| `OCUPADO` | Informá que el recurso está ocupado. | `1` |
| `ERROR` | Informá el error y frená. | `1` |

Si `generacion` no es `null` y no coincide con la del contexto, pedí reinicio.

## Confirmaciones humanas

Las confirmaciones (`--confirm`, `--forzar`) las corre el usuario en una segunda
terminal con el comando exacto que devuelve la CLI. El modelo nunca las corre
por su cuenta ni intenta saltearlas: se leen desde `/dev/tty`, requieren el texto
exacto y no hay flags ni variables de entorno que las salteen. Sin TTY, la CLI
devuelve `NECESITO_CONFIRMACION` con el comando sugerido. `!` tampoco tiene TTY.
"""

_RULES = """### Reglas de orquestación del plugin

- Plan: escribí `.orquesta/plan.md` y pedí un único OK después de presentarlo.
- Después del OK, avanzá con autonomía y frenos: detenete ante un merge a main,
  escaladas de permisos, altas o bajas de agentes, cambios de prompt de agentes,
  fallas o una review rechazada.
- Confirmaciones: el usuario las corre en una segunda terminal con el comando
  exacto que devuelve la CLI. Nunca corras `--confirm` ni `--forzar` por tu cuenta.
- Reuniones: usá `run_in_background` y evitá un juez si participan las tres familias.
- Generación: si la generación del sobre JSON no coincide con este contexto,
  pedí reinicio antes de seguir.
- RR. HH.: consultá en silencio con `orquesta rrhh triage --json`.
"""


def _json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def render_plugin(reg: Registry, backends: dict[str, dict], ctx: dict[str, Any]) -> dict[str, str]:
    """Devuelve rutas relativas al marketplace y contenido; ctx requiere paths y generacion.

    Solo lee los manuales, la instalación de plugins y la existencia del binario,
    igual que el render legacy. No genera hashes ni instala o escribe archivos.
    """
    render_ctx = render.RenderContext(
        paths=ctx["paths"], backends=parse_backends(backends), agent_prefix="orquesta:",
        agent_names=tuple(a.name for a in reg.agents),
    )
    binary = render_ctx.paths.home / ".venv/bin/orquesta"
    command = shlex.quote(str(binary.resolve())) if binary.is_file() else "orquesta"
    hooks = {
        event: [{"matcher": matcher, "hooks": [
            {"type": "command", "command": f"{command} hook {subcommand}"},
        ]}]
        for event, matcher, subcommand in [
            ("SessionStart", "startup|resume|clear|compact", "session-start"),
            ("PreToolUse", "Bash", "pre-bash"),
        ]
    }
    files = {
        ".claude-plugin/marketplace.json": _json({
            "name": "orquesta-local", "owner": {"name": "orquesta"},
            "plugins": [{"name": "orquesta", "source": "./orquesta"}],
        }),
        "orquesta/.claude-plugin/plugin.json": _json({
            "name": "orquesta", "version": reg.version,
            "description": "Orquesta: ecosistema multi-modelo integrado",
        }),
        "orquesta/skills/orquesta-cli/SKILL.md": _SKILL,
        "orquesta/hooks/hooks.json": _json({"hooks": hooks}),
        "orquesta/context/orquestador.md": (
            f"Generación: {ctx['generacion']}\n\n"
            + render.render_block_inner(reg, render_ctx) + "\n\n" + _RULES
        ),
    }
    for name, (description, body) in _COMMANDS.items():
        files[f"orquesta/commands/{name}.md"] = (
            render.render_frontmatter({"description": description}) + "\n" + body + "\n"
        )
    for agent in reg.enabled_agents():
        files[f"orquesta/agents/{agent.name}.md"] = (
            render.copy_agent_manual(agent, render_ctx) if agent.manual
            else render.render_agent_md(agent, render_ctx)
        )
    return files
