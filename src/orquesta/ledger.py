"""Registro determinista de progreso por proyecto y slug de tarea."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from orquesta.paths import Paths, default_paths

TOPE_INTENTOS = 6
_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _archivo(project_id: str, paths: Paths | None) -> Path:
    if not project_id or Path(project_id).name != project_id or project_id in (".", ".."):
        raise ValueError("id de proyecto inválido")
    return (paths or default_paths()).state_dir / project_id / "ledger.json"


def _validar_tarea(tarea: str) -> None:
    if not isinstance(tarea, str) or not _SLUG.fullmatch(tarea):
        raise ValueError("tarea inválida: usá un slug de hasta 64 caracteres (a-z, 0-9, punto, guion o guion bajo)")


def _lock(archivo: Path) -> FileLock:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(archivo.with_suffix(".lock")), timeout=30)


def _entrada_valida(entrada) -> bool:
    return (
        isinstance(entrada, dict)
        and isinstance(entrada.get("tarea"), str)
        and _SLUG.fullmatch(entrada["tarea"]) is not None
        and isinstance(entrada.get("cerrada"), bool)
        and isinstance(entrada.get("intentos"), list)
        and (not entrada["cerrada"] or isinstance(entrada.get("motivo"), str))
        and all(
            isinstance(i, dict) and type(i.get("n")) is int and i["n"] == n
            and isinstance(i.get("progreso"), bool)
            and all(isinstance(i.get(k), str) for k in ("nota", "ts"))
            for n, i in enumerate(entrada["intentos"], 1)
        )
    )


def _leer(archivo: Path) -> list[dict]:
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        raise ValueError(f"{archivo} no es JSON válido: {exc}") from None
    if (not isinstance(datos, list) or not all(_entrada_valida(r) for r in datos)
            or len({r["tarea"] for r in datos}) != len(datos)):  # una tarea repetida quedaría ignorada
        raise ValueError(f"{archivo} no tiene la forma esperada (lista de tareas con intentos y cierre)")
    return datos


def _guardar(archivo: Path, registros: list[dict]) -> None:
    temporal = archivo.with_suffix(".tmp")
    temporal.write_text(json.dumps(registros, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporal.replace(archivo)


def estado(entrada: dict) -> dict:
    sin_progreso = 0
    for intento in reversed(entrada["intentos"]):
        if intento["progreso"]:
            break
        sin_progreso += 1
    intentos = len(entrada["intentos"])
    accion = "seguir"
    if intentos >= TOPE_INTENTOS or sin_progreso >= 4:
        accion = "escalar"
    elif sin_progreso >= 2:
        accion = "replanificar"
    return {"tarea": entrada["tarea"], "intentos": intentos, "sin_progreso": sin_progreso,
            "cerrada": entrada["cerrada"], "accion": accion}


def anotar(project_id: str, tarea: str, *, progreso: bool, nota: str = "",
           paths: Paths | None = None) -> dict:
    _validar_tarea(tarea)
    if not isinstance(progreso, bool) or not isinstance(nota, str):
        raise ValueError("progreso debe ser booleano y nota debe ser texto")
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        registros = _leer(archivo)
        entrada = next((r for r in registros if r["tarea"] == tarea), None)
        if entrada is None:
            entrada = {"tarea": tarea, "intentos": [], "cerrada": False}
            registros.append(entrada)
        if entrada["cerrada"]:
            raise ValueError(f"la tarea '{tarea}' está cerrada")
        entrada["intentos"].append({"n": len(entrada["intentos"]) + 1, "progreso": progreso,
                                    "nota": nota[:500], "ts": datetime.now(timezone.utc).isoformat()})
        _guardar(archivo, registros)
        return estado(entrada)


def cerrar(project_id: str, tarea: str, *, motivo: str, paths: Paths | None = None) -> dict:
    _validar_tarea(tarea)
    if not isinstance(motivo, str):
        raise ValueError("motivo debe ser texto")
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        registros = _leer(archivo)
        entrada = next((r for r in registros if r["tarea"] == tarea), None)
        if entrada is None:
            raise ValueError(f"no hay ninguna tarea registrada con el slug '{tarea}'")
        entrada.update(cerrada=True, motivo=motivo)
        _guardar(archivo, registros)
        return estado(entrada)


def listar(project_id: str, paths: Paths | None = None) -> list[dict]:
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        return [estado(r) for r in _leer(archivo)]
