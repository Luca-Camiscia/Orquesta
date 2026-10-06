"""Guardrails deterministas.

- Agentes `protected`: cualquier cambio, borrado o renombre requiere confirmación explícita.
- Escaladas: read-only → write, o permissionMode elevado, requieren confirmación.
- Altas, bajas y cambios de prompt requieren confirmación.
- Secretos: nada que parezca un token o una clave se escribe en archivos generados.

La línea base es lo que está desplegado (manifiesto), así que commitear a mano no saltea el guard.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Iterable

from orquesta.catalog import ELEVATED_PERMISSION_MODES
from orquesta.render import effective_tools
from orquesta.schema import Agent, Baseline, Registry


@dataclass(frozen=True)
class Violation:
    agent: str
    kind: str  # protected | escalation | permission_mode | plugins | alta | baja | prompt
    detail: str
    title: str | None = None

    def __str__(self) -> str:
        return (f"{self.agent}: {self.detail}. Requiere confirmación explícita del usuario "
                f"(--confirm {self.agent}).")


# Campos agregados al esquema después del primer deploy: en su valor por defecto no entran al hash,
# así un agente que no cambió no aparece como "cambio protegido" tras una actualización de orquesta.
_LATER_FIELDS = {"plugins": [], "avoid_when": [], "backend_effort": None}


def entry_hash(agent: Agent, manual_text: str | None = None) -> str:
    payload = agent.model_dump(mode="json")
    for key, default in _LATER_FIELDS.items():
        if payload.get(key) == default:
            payload.pop(key, None)
    if manual_text is not None:
        payload["__manual_md__"] = manual_text
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _permission_mode(agent: Agent) -> str | None:
    value = agent.extra_frontmatter.get("permissionMode")
    return value if isinstance(value, str) else None


def prompt_hash(agent: Agent, manual_text: str | None = None) -> str:
    text = agent.prompt
    if agent.manual:
        # Separar los componentes evita ocultar cambios moviendo texto de uno al otro.
        text = json.dumps([agent.prompt, manual_text or ""], ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _frontmatter_hash(fields: dict) -> str:
    raw = json.dumps(fields, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _role_hash(agent: Agent) -> str:
    return hashlib.sha256(agent.role.encode("utf-8")).hexdigest()


_WRITE_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "MultiEdit", "Bash"})


def _tool_names(value: str | list[str]) -> set[str]:
    # Los argumentos de Agent(a,b) y Bash(...) no separan tools.
    items = re.split(r",(?![^()]*\))", value) if isinstance(value, str) else value
    return {item.strip().split("(", 1)[0] for item in items}


def baseline_from_registry(reg: Registry,
                           manual_texts: dict[str, str] | None = None) -> dict[str, Baseline]:
    manual_texts = manual_texts or {}
    return {a.name: Baseline(entry_hash(a, manual_texts.get(a.name)), a.mode, a.protected,
                             _permission_mode(a), tuple(a.plugins), a.enabled,
                             prompt_hash(a, manual_texts.get(a.name)),
                             tools_effective=deepcopy(effective_tools(a)),
                             isolation_effective=a.effective_isolation,
                             extra_frontmatter_dict=deepcopy(a.extra_frontmatter or {}),
                             extra_frontmatter_sha256=_frontmatter_hash(a.extra_frontmatter or {}),
                             role_hash=_role_hash(a))
            for a in reg.agents}


def baseline_from_manifest(manifest: dict) -> dict[str, Baseline]:
    # "guard" incluye TODOS los agentes del registro aplicado (también los deshabilitados).
    out = {}
    for name, e in (manifest.get("guard") or manifest.get("agents") or {}).items():
        # Los manifiestos anteriores solo desplegaban .md para agentes habilitados.
        enabled = e.get("enabled", name in manifest["agents"] if "agents" in manifest else True)
        out[name] = Baseline(e.get("entry_sha256", ""), e.get("mode", "read-only"),
                             bool(e.get("protected")), e.get("permission_mode"),
                             tuple(e.get("plugins") or ()),
                             enabled,
                             e.get("prompt_sha256"),
                             tools_effective=e.get("tools_effective"),
                             isolation_effective=e.get("isolation_effective"),
                             extra_frontmatter_dict=e.get("extra_frontmatter_dict"),
                             extra_frontmatter_sha256=e.get("extra_frontmatter_sha256"),
                             role_hash=e.get("role_hash"))
    return out


def check(baseline: dict[str, Baseline] | None, new: Registry,
          manual_texts: dict[str, str] | None = None) -> list[Violation]:
    if baseline is None:
        return []
    manual_texts = manual_texts or {}
    new_by_name = {a.name: a for a in new.agents}
    out: list[Violation] = []
    for name, base in baseline.items():
        agent = new_by_name.get(name)
        if not base.protected:
            if agent is None:
                out.append(Violation(name, "baja", "se elimina el agente (si es un renombre, el nombre nuevo sale como alta)"))
            elif base.enabled and not agent.enabled:
                out.append(Violation(name, "baja", "se deshabilita el agente"))
            continue
        if agent is None:
            out.append(Violation(name, "protected", "se elimina o se renombra un agente protegido"))
        elif entry_hash(agent, manual_texts.get(name)) != base.entry_sha256:
            out.append(Violation(name, "protected", "cambia la definición de un agente protegido"))
    for agent in new.agents:
        base = baseline.get(agent.name)
        # Un agente deshabilitado no se despliega: la alta se confirma cuando se habilita.
        if agent.enabled and (base is None or not base.enabled):
            out.append(Violation(agent.name, "alta", "se agrega un agente" if base is None else "se habilita el agente"))
        if base and not base.protected and (base.enabled or agent.enabled):
            if base.prompt_sha256 is not None:
                if prompt_hash(agent, manual_texts.get(agent.name)) != base.prompt_sha256:
                    out.append(Violation(agent.name, "prompt",
                                         "cambia el prompt o el .md manual (o se activa o desactiva `manual`)"))
            elif base.entry_sha256 and entry_hash(agent, manual_texts.get(agent.name)) != base.entry_sha256:
                out.append(Violation(agent.name, "prompt",
                                     "cambió algo del agente desde el último deploy y el manifiesto es de una "
                                     "versión anterior (sin hash de prompt): confirmá una sola vez"))
        if base and base.mode == "read-only" and agent.mode == "write":
            out.append(Violation(agent.name, "escalation", "pasa de read-only a write"))
        if base:
            if base.tools_effective is not None:
                before = _tool_names(base.tools_effective)
                after = _tool_names(effective_tools(agent))
                if "*" not in before:
                    added_tools = sorted((after - before) & _WRITE_TOOLS)
                    if "*" in after:
                        added_tools = ["* (todas las tools)"]
                    if added_tools:
                        out.append(Violation(agent.name, "escalation",
                                             f"se agregan tools de escritura o acceso a todas las tools "
                                             f"({', '.join(added_tools)})", "tools"))
            if (base.isolation_effective == "worktree" and agent.mode == "write"
                    and agent.effective_isolation == "none"):
                out.append(Violation(agent.name, "escalation",
                                     "isolation pasa de worktree a none: escribe directo en el proyecto",
                                     "isolation"))
            if base.extra_frontmatter_dict is not None:
                before_extra = base.extra_frontmatter_dict
                after_extra = agent.extra_frontmatter or {}
                before_hash = base.extra_frontmatter_sha256 or _frontmatter_hash(before_extra)
                if before_hash != _frontmatter_hash(after_extra):
                    keys = sorted(k for k in before_extra.keys() | after_extra.keys()
                                  if k != "permissionMode" and (k not in before_extra
                                      or k not in after_extra or before_extra[k] != after_extra[k]))
                    if keys:
                        out.append(Violation(agent.name, "escalation",
                                             f"cambian claves o valores de extra_frontmatter ({', '.join(keys)})",
                                             "extra_frontmatter"))
            if (not base.protected and base.role_hash is not None
                    and base.role_hash != _role_hash(agent)):
                out.append(Violation(agent.name, "escalation",
                                     "cambia role, que forma parte del prompt del agente en el .md", "role"))
            added = sorted(set(agent.plugins) - set(base.plugins))
            if added:
                out.append(Violation(agent.name, "plugins", f"se le agregan plugins ({', '.join(added)})"))
        pm = _permission_mode(agent)
        if pm in ELEVATED_PERMISSION_MODES and (base is None or base.permission_mode != pm):
            out.append(Violation(agent.name, "permission_mode", f"permissionMode elevado ({pm})"))
    return out


def pending(violations: Iterable[Violation], confirmations: Iterable[str]) -> list[Violation]:
    confirmed = set(confirmations)
    return [v for v in violations if v.agent not in confirmed]


SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("clave de OpenAI/Anthropic", re.compile(r"(?<![A-Za-z0-9])sk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("API key de Google", re.compile(r"AIza[0-9A-Za-z_\-]{35}")),
    ("token de GitHub", re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{36,}")),
    ("clave privada", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("token de Slack", re.compile(r"(?<![A-Za-z0-9])xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("access key de AWS", re.compile(r"(?<![A-Z0-9])AKIA[0-9A-Z]{16}(?![A-Z0-9])")),
    ("JWT", re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("token OAuth de Google", re.compile(r"(?<![A-Za-z0-9])ya29\.[0-9A-Za-z_\-]{20,}")),
]


def scan_secrets(text: str) -> list[str]:
    """Tipos de secreto encontrados en el texto (vacío si no hay nada sospechoso)."""
    return [label for label, rx in SECRET_PATTERNS if rx.search(text)]
