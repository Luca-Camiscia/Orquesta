"""Tests para proyectos del chat:
- Conversaciones sin proyecto (directorio propio en sandbox, prohibición de agentes write).
- Proyecto del ecosistema ("orquesta", siempre presente primero, inborrable e inmodificable).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from helpers import git
from orquesta import chat
from orquesta.generate import apply_plan, build_plan
from orquesta.ui.app import create_app


class FakeExec:
    def __init__(self):
        self.calls = []

    def __call__(self, paths, project, participant, prompt):
        self.calls.append((participant, prompt))
        return chat.Reply(f"respuesta de {participant}")


@pytest.fixture
def store(env):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    return chat.Store(env)


@pytest.fixture
def proyecto(tmp_path):
    p = tmp_path / "proyecto"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    (p / "a.txt").write_text("hola\n")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "init")
    return p


# 1. Conversaciones sin proyecto
def test_conversacion_sin_proyecto_creacion_y_directorio(store, env):
    conv = store.create_conversation(None, "Charla libre", "individual", ["codex-critico"])
    assert conv["project_id"] is None
    expected_dir = env.home / "sandbox" / "sin-proyecto" / conv["id"]
    assert expected_dir.is_dir()

    # Helpers de proyecto para la conversación
    proj = store.get_project_for_conversation(conv)
    assert proj["id"] is None
    assert proj["name"] == "Sin proyecto"
    assert proj["dir"] == str(expected_dir.resolve())
    assert proj["directory"] == str(expected_dir.resolve())

    # get_project con None también funciona
    proj_none = store.get_project(conv["project_id"])
    assert proj_none["id"] is None
    assert proj_none["name"] == "Sin proyecto"

    # Listado por project_id=None y project_id="sin-proyecto"
    sin_proy = store.list_conversations(project_id=None)
    assert any(c["id"] == conv["id"] for c in sin_proy)

    sin_proy_str = store.list_conversations(project_id="sin-proyecto")
    assert any(c["id"] == conv["id"] for c in sin_proy_str)

    # El listado global también la incluye
    assert any(c["id"] == conv["id"] for c in store.list_conversations())


def test_conversacion_sin_proyecto_turn_y_export(store):
    conv = store.create_conversation(None, "Sin proy", "individual", ["codex-critico"])
    fake = FakeExec()
    chat.run_turn(store, conv["id"], "¿cómo estás?", executor=fake)

    c = store.get_conversation(conv["id"])
    assert len(c["messages"]) == 2
    assert c["messages"][0]["text"] == "¿cómo estás?"
    assert c["messages"][1]["text"] == "respuesta de codex-critico"
    assert "Sin proyecto" in fake.calls[0][1]

    md = chat.export_markdown(store, conv["id"])
    assert "# Sin proy" in md
    assert "«Sin proyecto»" in md


def test_rechazo_agente_write_sin_proyecto_al_crear(store):
    # codex-forja tiene mode "write"
    with pytest.raises(chat.ChatError, match="escritura"):
        store.create_conversation(None, "Write fail", "individual", ["codex-forja"])

    with pytest.raises(chat.ChatError, match="escritura"):
        store.create_conversation(None, "Reunion fail", "reunion", ["codex-critico", "codex-forja"])


def test_rechazo_agente_write_sin_proyecto_al_agregar_participantes(store):
    conv = store.create_conversation(None, "Libre", "reunion", ["codex-critico", "gemini-lector"])
    with pytest.raises(chat.ChatError, match="escritura"):
        store.set_participants(conv["id"], ["codex-critico", "gemini-lector", "codex-forja"])

    # Participantes se mantienen intactos
    c = store.get_conversation(conv["id"])
    assert c["participants"] == ["codex-critico", "gemini-lector"]


# 2. Proyecto del ecosistema
def test_proyecto_sistema_siempre_presente_primero(store, env, proyecto):
    # Sin proyectos de usuario
    projects = store.list_projects()
    assert len(projects) >= 1
    eco = projects[0]
    assert eco["id"] == "orquesta"
    assert eco["name"] == "Ecosistema orquesta"
    assert eco["dir"] == str(env.home.resolve())
    assert eco["directory"] == str(env.home.resolve())
    assert eco["sistema"] is True

    # get_project("orquesta") funciona
    p = store.get_project("orquesta")
    assert p["name"] == "Ecosistema orquesta"
    assert p["sistema"] is True

    # Al crear un proyecto de usuario, el ecosistema sigue primero
    store.create_project("MiApp", str(proyecto))
    again = store.list_projects()
    assert again[0]["id"] == "orquesta"
    assert again[1]["name"] == "MiApp"

    # No está guardado en proyectos.json
    f = store._projects_file()
    if f.exists():
        raw = json.loads(f.read_text(encoding="utf-8"))
        assert not any(x.get("id") == "orquesta" for x in raw)


def test_proyecto_sistema_inborrable_e_inmodificable(store):
    with pytest.raises(chat.ChatError, match="sistema"):
        store.delete_project("orquesta")

    with pytest.raises(chat.ChatError, match="sistema"):
        store.update_project("orquesta", name="Otro")

    with pytest.raises(chat.ChatError, match="sistema"):
        store.update_project("orquesta", directory="/tmp")

    with pytest.raises(chat.ChatError, match="sistema"):
        store.rename_project("orquesta", "Otro")

    with pytest.raises(chat.ChatError, match="sistema"):
        store.change_project_directory("orquesta", "/tmp")

    with pytest.raises(chat.ChatError, match="reservado"):
        store.create_project("Ecosistema orquesta", str(store.paths.home))


def test_proyecto_sistema_permite_agentes_write(store):
    # En el proyecto orquesta sí pueden participar agentes de escritura
    conv = store.create_conversation("orquesta", "Orquesta dev", "individual", ["codex-forja"])
    assert conv["project_id"] == "orquesta"
    assert conv["participants"] == ["codex-forja"]

    # Y sumar agentes write en reunión también funciona
    reunion = store.create_conversation("orquesta", "Orquesta reunion", "reunion", ["codex-critico"])
    store.set_participants(reunion["id"], ["codex-critico", "codex-forja"])
    c = store.get_conversation(reunion["id"])
    assert "codex-forja" in c["participants"]


# 3. Endpoints API
def test_api_proyectos_solo_lectura(env, proyecto):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    c = TestClient(create_app(env, port=8787, token="t", run_tests=None),
                   base_url="http://127.0.0.1:8787", headers={"X-Orquesta-Token": "t"})
    r = c.get("/api/chat/projects").json()
    assert r["projects"][0]["id"] == "orquesta" and r["projects"][0]["sistema"] is True
    assert c.delete("/api/chat/projects/orquesta").status_code in (404, 405)
    assert c.post("/api/chat/conversations", json={"project_id": None}).status_code in (404, 405)


