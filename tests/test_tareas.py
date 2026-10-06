"""Identidad por repo y rama, persistencia y actualizaciones del registro."""
import json
from concurrent.futures import ThreadPoolExecutor

import pytest


def test_registro_actualizar_y_listar(env, tmp_path):
    from orquesta import tareas
    repo = str(tmp_path.resolve())
    a = tareas.registrar("proyecto", {"repo": repo, "rama": "feature", "agente": "codex"}, paths=env)
    assert a["status"] == "abierto" and a["sha_revisado"] is None
    assert a["tests_ok"] is None
    tareas.registrar("proyecto", {"repo": repo, "rama": "feature", "tests_cmd": "true"}, paths=env)
    tareas.actualizar("proyecto", "feature", {"veredicto": "APROBAR"}, repo=repo, paths=env)
    assert tareas.obtener("proyecto", "feature", paths=env)["agente"] == "codex"
    assert tareas.listar("proyecto", "abierto", paths=env)[0]["veredicto"] == "APROBAR"
    tareas.actualizar("proyecto", "feature", {"status": "descartado"}, paths=env)
    assert tareas.listar("proyecto", "abierto", paths=env) == []
    assert len(json.loads((env.state_dir / "proyecto" / "tareas.json").read_text())) == 1


def test_registro_ambiguedad_y_repo_normalizado(env, tmp_path):
    from orquesta import tareas
    for repo in (tmp_path / "a", tmp_path / "b"):
        tareas.registrar("p", {"repo": str(repo), "rama": "feature"}, paths=env)
    with pytest.raises(ValueError, match="varios repos"):
        tareas.obtener("p", "feature", paths=env)
    tareas.actualizar("p", "feature", {"tests_ok": True}, repo=str(tmp_path / "a" / "."), paths=env)
    assert tareas.obtener("p", "feature", repo=str(tmp_path / "a"), paths=env)["tests_ok"] is True
    assert tareas.obtener("p", "ausente", paths=env) is None
    with pytest.raises(ValueError, match="no hay"):
        tareas.actualizar("p", "ausente", {}, paths=env)


def test_registro_concurrente_no_pierde_tareas(env, tmp_path):
    from orquesta import tareas

    def registrar(n):
        tareas.registrar("p", {"repo": str(tmp_path), "rama": f"tarea-{n}"}, paths=env)

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(registrar, range(16)))
    assert len(tareas.listar("p", paths=env)) == 16
