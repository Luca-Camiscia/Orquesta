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
from dataclasses import dataclass
from typing import Iterable

from orquesta.catalog import ELEVATED_PERMISSION_MODES
from orquesta.schema import Agent, Registry


@dataclass(frozen=True)
class Baseline:
    entry_sha256: str
    mode: str
    protected: bool
    permission_mode: str | None
    plugins: tuple = ()
    enabled: bool = True
    prompt_sha256: str | None = None


@dataclass(frozen=True)
class Violation:
    agent: str
    kind: str  # protected | escalation | permission_mode | plugins | alta | baja | prompt
    detail: str

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


def baseline_from_registry(reg: Registry,
                           manual_texts: dict[str, str] | None = None) -> dict[str, Baseline]:
    manual_texts = manual_texts or {}
    return {a.name: Baseline(entry_hash(a, manual_texts.get(a.name)), a.mode, a.protected,
                             _permission_mode(a), tuple(a.plugins), a.enabled,
                             prompt_hash(a, manual_texts.get(a.name)))
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
                             e.get("prompt_sha256"))
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
            if agent is None or (base.enabled and not agent.enabled):
                out.append(Violation(name, "baja", "se elimina o se deshabilita un agente"))
            continue
        if agent is None:
            out.append(Violation(name, "protected", "se elimina o se renombra un agente protegido"))
        elif entry_hash(agent, manual_texts.get(name)) != base.entry_sha256:
            out.append(Violation(name, "protected", "cambia la definición de un agente protegido"))
    for agent in new.agents:
        base = baseline.get(agent.name)
        if base is None or (not base.enabled and agent.enabled):
            out.append(Violation(agent.name, "alta", "se agrega o se habilita un agente"))
        if base and not base.protected:
            if base.prompt_sha256 is not None:
                if prompt_hash(agent, manual_texts.get(agent.name)) != base.prompt_sha256:
                    out.append(Violation(agent.name, "prompt", "cambia el prompt de un agente"))
            elif base.entry_sha256 and entry_hash(agent, manual_texts.get(agent.name)) != base.entry_sha256:
                out.append(Violation(agent.name, "prompt", "cambia la definición con línea base ausente"))
        if base and base.mode == "read-only" and agent.mode == "write":
            out.append(Violation(agent.name, "escalation", "pasa de read-only a write"))
        if base:
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
