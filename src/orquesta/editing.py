"""Edición de orquesta.yaml preservando comentarios y formato (ruamel en modo round-trip).

Cada edición corre dentro de `session(paths, expected_hash)`: toma el lock, verifica que el archivo
no haya cambiado desde que el cliente lo leyó (hash optimista), aplica el cambio, valida el registro
completo y recién ahí escribe (atómico). Los agentes protegidos piden confirmación explícita.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from filelock import FileLock
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import LiteralScalarString

from orquesta.config import load_backends, manual_path
from orquesta.paths import Paths
from orquesta.schema import ConfigError, check_registry, parse_registry

FIELD_ORDER = ["name", "display_name", "enabled", "backend", "wrapper_model", "backend_model",
               "role", "route_when", "description", "tools", "mode", "isolation", "protected",
               "manual", "extra_frontmatter", "prompt"]
ALWAYS = {"name", "enabled", "backend", "role", "tools", "mode"}
IMPLICIT = {"enabled": True, "mode": "read-only"}  # lo que vale un campo ALWAYS si el YAML no lo tiene
DEFAULTS: dict[str, Any] = {"display_name": None, "wrapper_model": None, "backend_model": None,
                            "description": "", "isolation": None, "protected": False,
                            "manual": False, "extra_frontmatter": {}, "route_when": [], "prompt": ""}
_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class Conflict(Exception):
    pass


class EditError(Exception):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


class NeedsConfirmation(Exception):
    pass


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def read(paths: Paths) -> tuple[Any, str]:
    text = paths.registry.read_text(encoding="utf-8")
    return _yaml().load(text), sha(text)


def to_plain(doc: Any) -> Any:
    return json.loads(json.dumps(doc, default=str))


def dumps(doc: Any) -> str:
    buf = io.StringIO()
    _yaml().dump(doc, buf)
    return buf.getvalue()


def validate(paths: Paths, doc: Any) -> list[str]:
    try:
        reg = parse_registry(to_plain(doc))
        backends = load_backends(paths)
    except ConfigError as exc:
        return exc.errors
    return check_registry(reg, backends)


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def session(paths: Paths, expected_hash: str | None) -> Iterator[Any]:
    """Carga el registro, deja editarlo y lo guarda validado. Devuelve el doc para editar."""
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(paths.state_dir / "registry.lock"), timeout=30):
        doc, current = read(paths)
        if expected_hash != current:
            raise Conflict("orquesta.yaml cambió desde que lo cargaste (¿that-it-guy u otra pestaña?). "
                           "Recargá para ver la versión actual.")
        before = to_plain(doc)
        yield doc
        if to_plain(doc) == before:
            return  # sin cambios lógicos: no se reescribe (ni se re-indenta) el archivo
        errors = validate(paths, doc)
        if errors:
            raise EditError(errors)
        _write_atomic(paths.registry, dumps(doc))


def current_hash(paths: Paths) -> str:
    return sha(paths.registry.read_text(encoding="utf-8"))


# --- agentes ---------------------------------------------------------------------------------

def _agents(doc: Any) -> Any:
    if doc.get("agents") is None:
        doc["agents"] = CommentedSeq()
    return doc["agents"]


def find(doc: Any, name: str) -> int | None:
    return next((i for i, a in enumerate(_agents(doc)) if a.get("name") == name), None)


def _require_confirm(entry: Any, name: str, confirm: str | None, what: str) -> None:
    if entry is not None and entry.get("protected") and confirm != name:
        raise NeedsConfirmation(f"{name} está protegido: {what} requiere confirmación explícita "
                                f"(escribí su nombre para confirmar).")


def _yaml_value(key: str, value: Any) -> Any:
    if isinstance(value, str) and "\n" in value:
        return LiteralScalarString(value)
    if isinstance(value, list):
        seq = CommentedSeq(value)
        if key == "tools":
            seq.fa.set_flow_style()
        return seq
    if isinstance(value, dict):
        return CommentedMap(value)
    return value


def _is_default(key: str, value: Any) -> bool:
    return key not in ALWAYS and key in DEFAULTS and value in (DEFAULTS[key], None, "", [], {})


def _set_ordered(entry: Any, key: str, value: Any) -> None:
    if key in entry:
        entry[key] = value
        return
    pos = sum(1 for k in entry if k in FIELD_ORDER and FIELD_ORDER.index(k) < FIELD_ORDER.index(key))
    entry.insert(pos, key, value)


def upsert_agent(doc: Any, name: str, data: dict, confirm: str | None = None) -> None:
    unknown = sorted(set(data) - set(FIELD_ORDER))
    if unknown:
        raise EditError([f"campos desconocidos en el agente: {', '.join(unknown)}"])
    if data.get("name") != name:
        raise EditError(["para cambiarle el nombre a un agente usá «Renombrar»"])
    agents = _agents(doc)
    idx = find(doc, name)
    if idx is None:
        entry = CommentedMap()
        for key in FIELD_ORDER:
            if key in data and not _is_default(key, data[key]):
                entry[key] = _yaml_value(key, data[key])
        agents.append(entry)
        return
    entry = agents[idx]
    if "tools" in data and "tools" in entry and sorted(data["tools"]) == sorted(entry["tools"]):
        data = {**data, "tools": list(entry["tools"])}  # mismo conjunto: se respeta el orden original
    # Fase 1: cambios semánticos (un default implícito que el YAML no escribe no cuenta).
    changes = []
    for key in FIELD_ORDER:
        if key not in data:
            continue
        value = data[key]
        if key in entry:
            if entry[key] == value:
                continue
        elif value == IMPLICIT.get(key, DEFAULTS.get(key)) or _is_default(key, value):
            continue
        changes.append((key, value))
    if not changes:
        return  # nada cambió: ni siquiera pide confirmación
    # Fase 2: confirmar si está protegido y recién ahí tocar el documento.
    _require_confirm(entry, name, confirm, "editarlo")
    for key, value in changes:
        if _is_default(key, value):
            if key in entry:
                del entry[key]
        else:
            _set_ordered(entry, key, _yaml_value(key, value))


def delete_agent(doc: Any, name: str, confirm: str | None = None) -> None:
    idx = find(doc, name)
    if idx is None:
        raise EditError([f"no existe el agente {name!r}"])
    _require_confirm(_agents(doc)[idx], name, confirm, "eliminarlo")
    del _agents(doc)[idx]


def rename_agent(doc: Any, paths: Paths, old: str, new: str, confirm: str | None = None) -> None:
    idx = find(doc, old)
    if idx is None:
        raise EditError([f"no existe el agente {old!r}"])
    if not _NAME_RE.fullmatch(new or "") or len(new) > 64:
        raise EditError([f"name {new!r} inválido: solo minúsculas, números y guiones simples"])
    if find(doc, new) is not None:
        raise EditError([f"ya existe un agente {new!r}"])
    entry = _agents(doc)[idx]
    _require_confirm(entry, old, confirm, "renombrarlo")
    entry["name"] = new
    src, dst = manual_path(paths, old), manual_path(paths, new)
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)


def duplicate_agent(doc: Any, name: str) -> str:
    idx = find(doc, name)
    if idx is None:
        raise EditError([f"no existe el agente {name!r}"])
    base, n = f"{name}-copia", 1
    new = base
    while find(doc, new) is not None:
        n += 1
        new = f"{base}-{n}"
    entry = copy.deepcopy(_agents(doc)[idx])
    entry["name"] = new
    _set_ordered(entry, "enabled", False)
    for key in ("protected", "manual"):
        if key in entry:
            del entry[key]
    _agents(doc).insert(idx + 1, entry)
    return new


def save_manual(doc: Any, paths: Paths, name: str, content: str, confirm: str | None = None) -> None:
    idx = find(doc, name)
    if idx is None:
        raise EditError([f"no existe el agente {name!r}"])
    entry = _agents(doc)[idx]
    _require_confirm(entry, name, confirm, "editar su .md")
    m = re.match(r"---\n(.*?)\n---\n", content, re.S)
    if not m:
        raise EditError(["el .md tiene que empezar con frontmatter (--- ... ---)"])
    try:
        fm = YAML(typ="safe", pure=True).load(m.group(1)) or {}
    except Exception as exc:  # frontmatter roto: se lo decimos tal cual
        raise EditError([f"el frontmatter no es YAML válido: {exc}"]) from None
    if not isinstance(fm, dict) or fm.get("name") != name:
        raise EditError([f"el frontmatter tiene que tener `name: {name}` (si no, Claude Code lo "
                         f"cargaría con otro nombre)"])
    path = manual_path(paths, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, content)
    _set_ordered(entry, "manual", True)


def unset_manual(doc: Any, name: str, confirm: str | None = None) -> None:
    idx = find(doc, name)
    if idx is None:
        raise EditError([f"no existe el agente {name!r}"])
    entry = _agents(doc)[idx]
    _require_confirm(entry, name, confirm, "cambiarle el modo manual")
    if "manual" in entry:
        del entry["manual"]


def set_orchestrator(doc: Any, fields: dict) -> None:
    orch = doc["orchestrator"]
    for key in ("enabled", "show_mood_tag", "default_mood"):
        if key in fields and fields[key] is not None:
            orch[key] = fields[key]
