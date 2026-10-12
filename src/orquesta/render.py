"""Render de los .md de agentes y del bloque de CLAUDE.md.

Todo es determinista (mismo registro → mismos bytes): doctor compara hashes y el dry-run muestra
diffs limpios.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from orquesta.paths import Paths
from orquesta.schema import Agent, Backend, BackendsConfig, Registry

NOTICE = ("<!-- Generado por orquesta: no editar a mano. Fuente: {registry} "
          "(editalo y corré `orquesta generate`). -->")
DISABLED_LINE = ("Orquestación desactivada: no delegues automáticamente a los agentes de orquesta; "
                 "usalos solo si el usuario lo pide explícitamente.")
RUNNER_STATES = ("RATE_LIMIT, OCUPADO, ORQUESTA_OFF, BLOQUEADO, NO_GIT, VIOLACION_READ_ONLY, "
                 "VIOLACION_CHECKOUT, TIMEOUT, "
                 "FALLO_BACKEND, ERROR")


@dataclass(frozen=True)
class RenderContext:
    paths: Paths
    backends: BackendsConfig
    agent_prefix: str = ""
    agent_names: tuple[str, ...] = ()

    def reference_agents(self, text: str) -> str:
        """Prefija referencias de delegación; conserva namespaces, rutas y comandos CLI."""
        if not self.agent_prefix or not self.agent_names:
            return text
        names = "|".join(re.escape(name) for name in sorted(self.agent_names, key=len, reverse=True))
        pattern = (r"(?P<cli>\borquesta\s+(?:run|edit|show|rrhh|wt\s+new)\b[^`\n]*)|"
                   rf"(?P<agent>(?<![\w:/.-])(?:{names})(?![\w:/-]|\.[\w]))")
        return re.sub(pattern, lambda m: m[0] if m["cli"] else self.agent_prefix + m[0], text)

    def substitute(self, text: str) -> str:
        """Reemplaza los placeholders de rutas que pueden usar los prompts."""
        p = self.paths
        text = self.reference_agents(text)
        for key, value in {"{orquesta_home}": p.home, "{agents_dir}": p.agents_dir,
                           "{claude_md}": p.claude_md, "{registry}": p.registry,
                           "{plugin_dir}": p.plugin_dir}.items():
            text = text.replace(key, str(value))
        return text

    def notice(self) -> str:
        return NOTICE.format(registry=self.paths.registry)


# --- frontmatter -----------------------------------------------------------------------------

_PLAIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9 ,._()/+-]*")
_YAML_WORDS = {"true", "false", "yes", "no", "on", "off", "null", "y", "n"}


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        if (_PLAIN_RE.fullmatch(value) and value.lower() not in _YAML_WORDS
                and not value.endswith(" ")):
            return value
        return json.dumps(value, ensure_ascii=False)
    # dicts y listas: JSON en estilo flow, que es YAML válido
    return json.dumps(value, ensure_ascii=False)


def render_frontmatter(fields: dict[str, Any]) -> str:
    return "---\n" + "".join(f"{k}: {_scalar(v)}\n" for k, v in fields.items()) + "---\n"


def _tools_value(tools: str | list[str] | None) -> str | list[str]:
    if not tools:
        return "*"
    if isinstance(tools, str):
        return tools
    return tools if any("," in t for t in tools) else ", ".join(tools)


def effective_tools(agent: Agent) -> str | list[str]:
    """Valor de tools del frontmatter; extra_frontmatter tiene la última palabra."""
    return (agent.extra_frontmatter["tools"] if "tools" in agent.extra_frontmatter
            else _tools_value(agent.tools))


def effective_description(agent: Agent, backend: Backend | None) -> str:
    if agent.description.strip():
        return " ".join(agent.description.split())
    parts = []
    if agent.route_when:
        cases = "; ".join(r.strip().rstrip(".") for r in agent.route_when)
        parts.append(f"Usalo PROACTIVAMENTE cuando {cases}.")
    else:
        parts.append("Usalo PROACTIVAMENTE para su rol.")
    parts.append(agent.role.strip().rstrip(".") + ".")
    if agent.is_external and backend is not None:
        if agent.mode == "read-only":
            how = "solo lectura"
        elif agent.effective_isolation == "worktree":
            how = "escribe en un git worktree aislado"
        else:
            how = "escribe en el proyecto"
        parts.append(f"Delega en {backend.label}: {how}.")
    if agent.avoid_when:
        parts.append("NO usar si " + "; ".join(a.strip().rstrip(".") for a in agent.avoid_when) + ".")
    return " ".join(parts)


# --- cuerpos ---------------------------------------------------------------------------------

def _quote(text: str) -> list[str]:
    return [f"> {line}" if line.strip() else ">" for line in text.strip().splitlines()]


def _external_body(agent: Agent, backend: Backend, ctx: RenderContext) -> str:
    wt = agent.effective_isolation == "worktree"
    task = ' --task "<slug-corto-de-la-tarea>"' if wt else ""
    if agent.mode == "read-only":
        mode_rule = ("- El runner corre el backend en solo lectura (sandbox del sistema operativo cuando "
                     "está disponible) y reporta cualquier escritura como violación.")
    elif wt:
        mode_rule = (f"- El runner crea un `git worktree` en la rama `orquesta/{agent.name}/<tarea>`, "
                     f"corre el backend ahí adentro y commitea en esa rama. Nunca mergees: el "
                     f"orquestador revisa el diff y decide.")
    else:
        mode_rule = "- El backend escribe directo en el proyecto (sin worktree)."
    lines = [
        ctx.notice(),
        "",
        f"# {agent.label}",
        "",
        f"Sos un **wrapper**: no resolvés la tarea vos. Tu único trabajo es pasársela a "
        f"**{backend.label}** con `orquesta run` y devolverle el resultado al orquestador.",
        "",
        f"- **Rol:** {ctx.reference_agents(agent.role.strip())}",
        f"- **Backend:** `{agent.backend}` · modelo: {agent.backend_model or 'el default del CLI'}"
        f" · modo: **{agent.mode}** · aislamiento: **{agent.effective_isolation}**",
        "",
        "## Cómo ejecutar",
        "",
        "1. Armá el pedido COMPLETO para el backend, que no ve esta conversación: objetivo, rutas "
        "absolutas, contexto relevante, restricciones y criterios de aceptación.",
        "2. Hacé UNA sola llamada a Bash, con `timeout: 600000`:",
        "",
        "```bash",
        f'orquesta run {agent.name} --cwd "<ruta absoluta del proyecto>"{task} '
        f"<<'ORQUESTA_PROMPT'",
        "<pedido completo>",
        "ORQUESTA_PROMPT",
        "```",
        "",
        "3. Respondé con la salida del backend tal cual, sin reescribirla, y al final el bloque de "
        "metadatos del runner" + (" (rama, commit y diffstat)." if wt else "."),
        "",
        "## Reglas",
        "",
        "- No edites archivos ni corras otros comandos: solo `orquesta run`.",
        mode_rule,
        f"- Si la salida empieza con `ESTADO: <código>` ({RUNNER_STATES}), devolvela tal cual y "
        f"frená: decide el orquestador.",
        "- No reintentes por tu cuenta.",
    ]
    if agent.prompt.strip():
        lines += ["", "## Instrucciones que el runner le antepone al backend", "",
                  *_quote(ctx.substitute(agent.prompt))]
    return "\n".join(lines) + "\n"


def _claude_body(agent: Agent, ctx: RenderContext) -> str:
    lines = [ctx.notice(), "", ctx.substitute(agent.prompt).strip()]
    if agent.mode == "write" and agent.effective_isolation == "worktree":
        lines += [
            "",
            "## Aislamiento (worktree)",
            "",
            f"Antes de editar, creá tu worktree con `orquesta wt new --agent {agent.name} "
            f"--task <slug> --repo <ruta del proyecto>` y trabajá SOLO dentro del directorio que "
            f"imprime (rama `orquesta/{agent.name}/<slug>`). Al terminar, commiteá en esa rama y "
            f"reportá la rama y el diffstat. Nunca mergees: eso lo hace el orquestador.",
        ]
    return "\n".join(lines) + "\n"


def render_agent_md(agent: Agent, ctx: RenderContext) -> str:
    backend = ctx.backends.backends.get(agent.backend)
    fields: dict[str, Any] = {
        "name": agent.name,
        "description": ctx.reference_agents(effective_description(agent, backend)),
        "tools": effective_tools(agent),
        "model": agent.effective_wrapper_model,
    }
    for key in sorted(agent.extra_frontmatter):
        fields[key] = agent.extra_frontmatter[key]
    if agent.plugins and not agent.is_external:
        from orquesta.plugins import installed
        inst = installed(ctx.paths)
        skills = list(fields.get("skills") or [])
        tools = list(agent.tools)
        for pid in agent.plugins:
            info = inst.get(pid)
            if info:
                skills += [s for s in info.skills if s not in skills]
                tools += [t for t in info.mcp_tools if t not in tools]
        fields["tools"] = _tools_value(tools)
        if skills:
            fields["skills"] = skills
    if "tools" in agent.extra_frontmatter:
        fields["tools"] = agent.extra_frontmatter["tools"]
    if agent.is_external and backend is not None:
        body = _external_body(agent, backend, ctx)
    else:
        body = _claude_body(agent, ctx)
    return render_frontmatter(fields) + "\n" + body


def copy_agent_manual(agent: Agent, ctx: RenderContext) -> str:
    """Lee un agente manual sin regenerarlo ni cambiar sus bytes UTF-8."""
    from orquesta.config import read_manual
    from orquesta.schema import ConfigError

    text = read_manual(ctx.paths, agent.name)
    if text is None:
        raise ConfigError([f"agente {agent.name!r}: es manual pero no existe manual/{agent.name}.md"])
    return text


# --- bloque de CLAUDE.md ---------------------------------------------------------------------

def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def render_block_inner(reg: Registry, ctx: RenderContext) -> str:
    o = reg.orchestrator
    if not o.enabled:
        return DISABLED_LINE
    agents = reg.enabled_agents()
    limits = ", ".join(f"{k} {v}" for k, v in reg.limits.max_parallel.items()) or "sin límites"
    lines = [
        ctx.notice(),
        "## Orquesta: ecosistema multi-modelo",
        "",
        f"Estado: **activa** · Mood por defecto: **{o.default_mood}** · Agentes habilitados: "
        f"{len(agents)}",
        "",
        "### Moods (solo cambian la presentación)",
        "",
        "| Mood | Qué mostrar |",
        "|---|---|",
        *[f"| `{name}` | {_cell(desc)} |" for name, desc in o.moods.items()],
        "",
        f"- Mood activo al arrancar: `{o.default_mood}`. Si el usuario escribe `mood: <nombre>`, "
        f"aplicá ese mood desde ese mensaje hasta que lo cambie. Si el nombre no existe, decíselo, "
        f"listá los disponibles ({', '.join(o.moods)}) y seguí con el mood actual.",
    ]
    if o.show_mood_tag:
        lines.append("- La primera línea de cada respuesta es `[mood: <nombre>]`, con el mood activo.")
    lines += [
        "- Los moods cambian SOLO la presentación: nunca relajan verificación, review cruzado ni "
        "guardrails. En `silencioso` se verifica todo igual, solo que no se narra.",
        "",
        "### Ruteo",
        "",
        "| Agente | Backend | Modo | Aislamiento | Cuándo usarlo |",
        "|---|---|---|---|---|",
    ]
    for a in agents:
        when = "; ".join(_cell(r) for r in a.route_when) or _cell(a.role)
        if a.avoid_when:
            when += ". NO usar si " + "; ".join(_cell(x) for x in a.avoid_when)
        lines.append(f"| `{ctx.agent_prefix}{a.name}` | {a.backend} | {a.mode} | "
                     f"{a.effective_isolation} | {ctx.reference_agents(when)} |")
    lines += [
        "",
        "Los agentes de plugins (p. ej. `codex:codex-rescue`) no forman parte de este ruteo: "
        "usalos solo si el usuario lo pide explícitamente.",
        "",
        "### Reglas de orquestación",
        "",
        "- Sos el orquestador: planificás, partís tareas, delegás y verificás.",
        "- Nunca aceptes output delegado sin verificarlo (tests, leer el diff, correr el código).",
        "- Cambios no triviales: review cruzado (si escribió un agente codex, revisa uno agy o vos).",
        "- Al delegar mandá contexto completo: el delegado no ve esta conversación.",
        "- Agentes write con `isolation: worktree` trabajan en un `git worktree` en la rama "
        "`orquesta/<agente>/<tarea>`; vos revisás el diff y mergeás. Si el proyecto no es repo "
        "git, preguntale al usuario.",
        f"- Respetá `limits.max_parallel` por backend: {limits}.",
        "- Si un delegado choca con rate limit (`ESTADO: RATE_LIMIT`), avisale al usuario y seguí vos.",
        "- Si el proyecto tiene un archivo `.orquesta-off` en la raíz, no delegues en ese proyecto.",
        "- Aplicá el mood activo a la presentación de cada respuesta.",
        "- Si un subagente devuelve `NECESITO CONFIRMACIÓN`, preguntale al usuario y re-invocalo con "
        "su respuesta. Nunca confirmes por tu cuenta.",
        "- Verificá con evidencia ejecutable (tests del proyecto, reproducir el bug), no con el texto del "
        "delegado: que algo pase los tests tampoco prueba que esté bien (arXiv 2503.15223).",
        "- Para decisiones con varias opiniones: primero respuestas independientes; debatí solo si hay "
        "desacuerdo, en una ronda, con respuestas anónimas (arXiv 2508.17536, 2511.11306, 2510.07517).",
        "- Al delegar mandá un brief (objetivo, archivos, restricciones, criterio de aceptación), no el "
        "historial de la conversación (arXiv 2505.06120).",
        "- La CONFIANZA que declaran los agentes está inflada: usala como umbral de escalado (menos de 75 "
        "→ verificá más), nunca como peso de voto.",
        "- Si tenés que elegir entre propuestas de distintos modelos, que decida un juez de otra familia que "
        "la de los autores, o los tests (sesgo de autopreferencia, arXiv 2404.13076).",
        "- Cascada: si la tarea es secuencial o la resuelve un solo agente, usá uno solo. Varios agentes o una "
        "reunión, solo para subtareas paralelizables, o si falló la verificación o la CONFIANZA es baja "
        "(arXiv 2512.08296, 2505.18286).",
        "- Una tarea se cierra con una señal externa (tests en el SHA revisado, un comando reproducible o el "
        "usuario), nunca por el RESULTADO o la CONFIANZA que declara el delegado (arXiv 2607.25152).",
        "- En el brief citá textual el pedido original del usuario, además de tu resumen, y delegá sin "
        "intermediarios: cada salto pierde información (arXiv 2603.26993).",
        "- Anotá cada ronda de una tarea delegada con `orquesta ledger anotar <tarea> --progreso` o "
        "`--sin-progreso`. Si el sobre avisa estancamiento, replanificá con un brief nuevo, sin historial; "
        "si devuelve BLOQUEADO, frená y escalá al usuario (Magentic-One, arXiv 2411.04468; arXiv 2607.01641).",
        "- Si la respuesta de un wrapper parece recortada o resumida, leé el archivo de `salida completa:` "
        "del bloque `orquesta run`: es la salida original del backend.",
    ]
    return "\n".join(lines)
