"""Manifiesto del deploy: qué está desplegado en ~/.claude/agents/orquesta/ y con qué configuración.

Lo escribe `orquesta generate` junto con los .md. Lo leen el runner (usa la configuración APLICADA,
no la que esté a medio editar en orquesta.yaml), el guard (línea base) y doctor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from orquesta import migracion
from orquesta.paths import Paths


class ManifestError(Exception):
    """Manifiesto ausente o corrupto en la fase actual."""


@dataclass(frozen=True)
class DeployedAgent:
    name: str
    file: str
    sha256: str
    backend: str
    mode: str
    isolation: str
    backend_model: str | None
    wrapper_model: str
    backend_prompt: str
    manual: bool
    protected: bool
    permission_mode: str | None
    entry_sha256: str
    backend_effort: str | None = None


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def dumps(data: dict) -> str:
    return json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load(paths: Paths) -> dict | None:
    """Carga el manifiesto de la fase actual, sin fallback.

    Devuelve None solo en legacy sin archivo (primer deploy).
    Raises: ManifestError si está corrupto o falta en plugin.
    """
    try:
        fase = migracion.fase(paths)
    except ValueError as exc:
        raise ManifestError(f"no se puede saber la fase de deploy: {exc}") from exc
    path = paths.legacy_manifest if fase == "legacy" else paths.plugin_manifest
    error = f"manifiesto de la fase {fase} ausente o corrupto: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        if fase == "legacy":
            return None
        raise ManifestError(error) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ManifestError(error) from exc
    if not isinstance(data, dict) or not isinstance(data.get("agents", {}), dict):
        raise ManifestError(error)
    return data


def deployed_agent(manifest: dict | None, name: str) -> DeployedAgent | None:
    entry = (manifest or {}).get("agents", {}).get(name)
    if not entry:
        return None
    fields = DeployedAgent.__dataclass_fields__
    return DeployedAgent(name=name, **{k: entry.get(k) for k in fields if k != "name"})


def max_parallel(manifest: dict | None, backend: str) -> int:
    return int(((manifest or {}).get("limits", {}).get("max_parallel", {}) or {}).get(backend, 1))
