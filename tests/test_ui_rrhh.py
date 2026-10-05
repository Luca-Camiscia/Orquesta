"""Endpoints RR. HH.: evidencia local, token, validación y participantes."""
from fastapi.testclient import TestClient

from orquesta import modelos
from orquesta.chat import Store
from orquesta.ui.app import create_app

TOKEN = "rrhh-test"


def client(env):
    return TestClient(create_app(env, token=TOKEN, run_tests=None), base_url="http://127.0.0.1:8787",
                      headers={"X-Orquesta-Token": TOKEN})


def test_endpoints_vacios_protegidos_y_sin_cli(env, monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError("RR. HH. no debe ejecutar descubrimiento ni backends")
    monkeypatch.setattr(modelos.subprocess, "run", prohibido)
    c = client(env)
    for path in ("/api/rrhh/evaluacion", "/api/rrhh/catalogo"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["x-frame-options"] == "DENY"
        assert c.get(path, headers={"Host": "evil.example"}).status_code == 400
        assert c.get(path, headers={"Origin": "http://evil.example"}).status_code == 403
    assert all(a["nota"] is None for a in c.get("/api/rrhh/evaluacion").json()["agentes"])
    r = c.post("/api/rrhh/triage", json={"texto": "fix typo"})
    assert r.status_code == 200 and r.json()["recomendados"][0]["name"] == "codex-forja"
    assert c.post("/api/rrhh/triage", json={"texto": "fix"},
                  headers={"X-Orquesta-Token": "incorrecto"}).status_code == 403
    for payload in ({}, {"texto": "  "}, {"texto": 12}):
        assert c.post("/api/rrhh/triage", json=payload).status_code == 422


def test_sugerencias_y_agregar_conv_individual(env, monkeypatch):
    # Participantes desplegados, sin generar ni ejecutar agentes.
    monkeypatch.setattr(Store, "participants", lambda self: ["codex-forja", "gemini-lector"])
    c = client(env)
    pr = c.post("/api/chat/projects", json={"name": "p", "dir": str(env.home)}).json()
    conv = c.post("/api/chat/conversations", json={"project_id": pr["id"],
                  "participants": ["codex-forja"]}).json()
    Store(env).update(conv["id"], lambda v: v["messages"].append(
        {"author": "usuario", "text": "leer repo entero", "meta": {}}))
    r = c.get(f'/api/rrhh/sugerencias/{conv["id"]}')
    assert r.status_code == 200 and r.json()["sugerencias"][0]["name"] == "gemini-lector"
    r = c.post(f'/api/chat/conversations/{conv["id"]}/participants',
               json={"participants": ["codex-forja", "gemini-lector"]})
    assert r.status_code == 200 and r.json()["kind"] == "reunion"
    assert c.get(f'/api/rrhh/sugerencias/{conv["id"]}').json()["sugerencias"] == []
    assert c.get("/api/rrhh/sugerencias/invalid").status_code == 422
