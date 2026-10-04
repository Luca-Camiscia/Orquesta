"""Carga de orquesta.yaml y backends.yaml desde disco (o desde git HEAD)."""
from __future__ import annotations

import subprocess
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from orquesta.paths import Paths
from orquesta.schema import (BackendsConfig, ConfigError, Registry, check_registry, parse_backends,
                             parse_registry)


def parse_yaml_text(text: str, label: str) -> Any:
    try:
        return YAML(typ="safe", pure=True).load(text)
    except YAMLError as exc:
        raise ConfigError([f"{label}: YAML inválido: {exc}"]) from None


def _read(path, label: str) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError([f"no existe {label} ({path})"]) from None
    return parse_yaml_text(text, label)


def load_registry(paths: Paths) -> Registry:
    try:
        return parse_registry(_read(paths.registry, "orquesta.yaml"))
    except ConfigError as exc:
        raise ConfigError([e if e.startswith("orquesta.yaml") else f"orquesta.yaml: {e}"
                           for e in exc.errors]) from None


def load_backends(paths: Paths) -> BackendsConfig:
    return parse_backends(_read(paths.backends, "backends.yaml"))


def load_all(paths: Paths) -> tuple[Registry, BackendsConfig]:
    """Registro y backends válidos, o ConfigError con TODOS los errores juntos."""
    errors: list[str] = []
    reg = backends = None
    try:
        reg = load_registry(paths)
    except ConfigError as exc:
        errors += exc.errors
    try:
        backends = load_backends(paths)
    except ConfigError as exc:
        errors += exc.errors
    if reg is not None and backends is not None:
        errors += [f"orquesta.yaml: {e}" for e in check_registry(reg, backends)]
    if errors:
        raise ConfigError(errors)
    return reg, backends


def registry_at_head(paths: Paths) -> Registry | None:
    """orquesta.yaml tal como está en el último commit (None si no hay o no parsea)."""
    proc = subprocess.run(["git", "show", "HEAD:orquesta.yaml"], cwd=paths.home,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        return parse_registry(parse_yaml_text(proc.stdout, "orquesta.yaml@HEAD"))
    except ConfigError:
        return None


def manual_path(paths: Paths, name: str):
    return paths.manual_dir / f"{name}.md"


def read_manual(paths: Paths, name: str) -> str | None:
    p = manual_path(paths, name)
    return p.read_text(encoding="utf-8") if p.is_file() else None
