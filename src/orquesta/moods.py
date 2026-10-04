"""Edición de moods sobre el registro crudo (dict o CommentedMap de ruamel).

Reglas: nombres válidos y únicos, descripción no vacía, y el `default_mood` no se puede borrar.
"""
from __future__ import annotations

import re
from typing import Any

_MOOD_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")


class MoodError(ValueError):
    pass


def _orch(data: Any) -> tuple[Any, Any]:
    orch = data.get("orchestrator") if hasattr(data, "get") else None
    moods = orch.get("moods") if hasattr(orch, "get") else None
    if moods is None or not hasattr(moods, "items"):
        raise MoodError("el registro no tiene orchestrator.moods")
    return orch, moods


def _check_name(name: str) -> None:
    if not isinstance(name, str) or not _MOOD_RE.fullmatch(name):
        raise MoodError(f"nombre de mood {name!r} inválido: usá minúsculas, números, - o _")


def _check_desc(desc: str) -> str:
    if not isinstance(desc, str) or not desc.strip():
        raise MoodError("la descripción del mood no puede estar vacía")
    return desc.strip()


def add_mood(data: Any, name: str, description: str) -> None:
    _, moods = _orch(data)
    _check_name(name)
    if name in moods:
        raise MoodError(f"ya existe un mood {name!r}")
    moods[name] = _check_desc(description)


def update_mood(data: Any, name: str, description: str) -> None:
    _, moods = _orch(data)
    if name not in moods:
        raise MoodError(f"no existe el mood {name!r}")
    moods[name] = _check_desc(description)


def rename_mood(data: Any, old: str, new: str) -> None:
    orch, moods = _orch(data)
    if old not in moods:
        raise MoodError(f"no existe el mood {old!r}")
    _check_name(new)
    if new != old and new in moods:
        raise MoodError(f"ya existe un mood {new!r}")
    items = list(moods.items())
    moods.clear()
    for key, value in items:
        moods[new if key == old else key] = value
    if orch.get("default_mood") == old:
        orch["default_mood"] = new


def delete_mood(data: Any, name: str) -> None:
    orch, moods = _orch(data)
    if name not in moods:
        raise MoodError(f"no existe el mood {name!r}")
    if orch.get("default_mood") == name:
        raise MoodError(f"no se puede borrar {name!r}: es el mood default. Elegí otro default primero.")
    del moods[name]


def set_default(data: Any, name: str) -> None:
    orch, moods = _orch(data)
    if name not in moods:
        raise MoodError(f"no existe el mood {name!r} (disponibles: {', '.join(moods)})")
    orch["default_mood"] = name
