"""Registro de tareas por proyecto; identidad = repo absoluto + rama."""
from __future__ import annotations

import json
from pathlib import Path

from filelock import FileLock

from orquesta.paths import Paths, default_paths

DEFAULTS = {
    "agente": None, "base_sha": None, "destino": None, "sha_revisado": None,
    "tests_sha": None, "tests_ok": None, "tests_cmd": None, "confianza_autor": None,
    "veredicto": None, "revisor": None, "status": "abierto",
}


def _archivo(project_id: str, paths: Paths | None) -> Path:
    if not project_id or Path(project_id).name != project_id or project_id in (".", ".."):
        raise ValueError("id de proyecto inválido")
    return (paths or default_paths()).state_dir / project_id / "tareas.json"


def _lock(archivo: Path) -> FileLock:
    archivo.parent.mkdir(parents=True, exist_ok=True)
    return FileLock(str(archivo.with_suffix(".lock")), timeout=30)


def _leer(archivo: Path) -> list[dict]:
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as exc:
        raise ValueError(f"{archivo} no es JSON válido: {exc}") from None
    if not isinstance(datos, list) or not all(
            isinstance(r, dict) and all(isinstance(r.get(k), str) for k in ("rama", "repo", "status"))
            for r in datos):
        raise ValueError(f"{archivo} no tiene la forma esperada (lista de tareas con rama, repo y status)")
    return datos


def _guardar(archivo: Path, registros: list[dict]) -> None:
    temporal = archivo.with_suffix(".tmp")
    temporal.write_text(json.dumps(registros, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporal.replace(archivo)


def _buscar(registros: list[dict], rama: str, repo: str | None) -> dict | None:
    resolved = str(Path(repo).resolve()) if repo is not None else None
    encontrados = [r for r in registros if r["rama"] == rama
                   and (resolved is None or r["repo"] == resolved)]
    if len(encontrados) > 1:
        repos = "\n".join(sorted(r["repo"] for r in encontrados))
        raise ValueError(f"la rama '{rama}' existe en varios repos registrados:\n{repos}\n"
                         "Pasá `--repo <ruta>` para desambiguar")
    return encontrados[0] if encontrados else None


def registrar(project_id: str, tareas: dict, *, paths: Paths | None = None) -> dict:
    archivo = _archivo(project_id, paths)
    datos = {**tareas, "repo": str(Path(tareas["repo"]).resolve())}
    with _lock(archivo):
        registros = _leer(archivo)
        registro = _buscar(registros, datos["rama"], datos["repo"])
        if registro is None:
            registro = dict(DEFAULTS)
            registros.append(registro)
        registro.update(datos)
        _guardar(archivo, registros)
        return dict(registro)


def obtener(project_id: str, rama: str, repo: str | None = None, *,
            paths: Paths | None = None) -> dict | None:
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        return _buscar(_leer(archivo), rama, repo)


def actualizar(project_id: str, rama: str, campos: dict, repo: str | None = None, *,
               paths: Paths | None = None) -> dict:
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        registros = _leer(archivo)
        registro = _buscar(registros, rama, repo)
        if registro is None:
            raise ValueError(f"no hay ninguna tarea registrada con la rama {rama}")
        if "repo" in campos or "rama" in campos:
            raise ValueError("la identidad de la tarea no se puede actualizar")
        registro.update(campos)
        _guardar(archivo, registros)
        return dict(registro)


def listar(project_id: str, filtro_status: str | None = None, *,
           paths: Paths | None = None) -> list[dict]:
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        return [r for r in _leer(archivo) if filtro_status is None or r["status"] == filtro_status]


def a_registro_review(tarea: dict) -> dict:
    """Adapta un registro de tarea a formato para revision.razones_bloqueo.
    
    Entrada: registro de tareas.json con keys rama, tests_ok, tests_cmd, confianza_autor, veredicto
    Salida: dict con keys branch, tests, confianza_autor, review.veredicto
    """
    tests = None
    if tarea.get("tests_ok") is not None:
        tests = {"ok": tarea["tests_ok"], "cmd": tarea.get("tests_cmd", "")}
    return {
        "branch": tarea.get("rama", ""),
        "tests": tests,
        "confianza_autor": tarea.get("confianza_autor"),  # ya en 0-100
        "review": {"veredicto": tarea.get("veredicto")},  # tal cual parsear_veredicto
        **({"diff": tarea["diff"]} if "diff" in tarea else {}),
        **({"diff_error": True} if tarea.get("diff_error") else {}),
    }


def cerrar_si_vigente(project_id: str, rama: str, repo: str, esperado: dict, accion, *,
                      status: str, paths: Paths | None = None):
    """Bajo el lock de tareas.json: si la tarea sigue igual que `esperado`, corre `accion()` y marca `status`.

    Una review concurrente toma el mismo lock para invalidar la evidencia, así que no puede colarse entre
    la última verificación y el merge. Devuelve lo que devuelve `accion`, o None si la tarea cambió.
    """
    archivo = _archivo(project_id, paths)
    with _lock(archivo):
        registros = _leer(archivo)
        registro = _buscar(registros, rama, repo)
        if registro is None or any(registro.get(k) != v for k, v in esperado.items()):
            return None
        resultado = accion()
        registro["status"] = status
        _guardar(archivo, registros)
        return resultado
