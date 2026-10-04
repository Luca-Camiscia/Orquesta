"""Chat: proyectos, conversaciones individuales y reuniones (rondas y moderadas), worktrees mergeables."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from helpers import git
from orquesta import chat
from orquesta.generate import apply_plan, build_plan
from orquesta.ui.app import create_app


class FakeExec:
    """Ejecutor falso: responde según participante; el moderador sigue un guion."""

    def __init__(self, moderador=None):
        self.calls = []
        self.moderador = list(moderador or [])

    def __call__(self, paths, project, participant, prompt):
        self.calls.append((participant, prompt))
        if participant == "orquestador" and self.moderador:
            return chat.Reply(self.moderador.pop(0))
        return chat.Reply(f"respuesta de {participant}")


@pytest.fixture
def proyecto(tmp_path):
    p = tmp_path / "proyecto"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    (p / "a.txt").write_text("hola\n")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "init")
    return p


@pytest.fixture
def store(env):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    return chat.Store(env)


def test_proyectos_y_conversaciones_persisten(store, proyecto, env):
    pr = store.create_project("Empanadas", str(proyecto))
    conv = store.create_conversation(pr["id"], "Charla", "individual", ["codex-critico"])
    again = chat.Store(env)
    assert again.list_projects()[0]["name"] == "Empanadas"
    assert again.get_conversation(conv["id"])["participants"] == ["codex-critico"]


def test_proyecto_con_directorio_inexistente_falla(store):
    with pytest.raises(chat.ChatError):
        store.create_project("X", "/no/existe")


def test_participante_desconocido_falla(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    with pytest.raises(chat.ChatError, match="fantasma"):
        store.create_conversation(pr["id"], "c", "individual", ["fantasma"])


def test_individual_responde_el_agente(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "c", "individual", ["codex-critico"])
    fake = FakeExec()
    chat.run_turn(store, conv["id"], "¿qué opinás?", executor=fake)
    msgs = store.get_conversation(conv["id"])["messages"]
    assert [(m["author"], m["status"]) for m in msgs] == [("usuario", "done"), ("codex-critico", "done")]
    assert msgs[1]["text"] == "respuesta de codex-critico"
    assert "¿qué opinás?" in fake.calls[0][1]


def test_ronda_cada_uno_ve_lo_anterior(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "r", "reunion", ["gemini-lector", "codex-critico"], mode="rondas")
    fake = FakeExec()
    chat.run_turn(store, conv["id"], "debatan", executor=fake)
    assert [c[0] for c in fake.calls] == ["gemini-lector", "codex-critico"]
    assert "respuesta de gemini-lector" in fake.calls[1][1]


def test_mencion_le_habla_a_uno_solo(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "r", "reunion", ["gemini-lector", "codex-critico"], mode="rondas")
    fake = FakeExec()
    chat.run_turn(store, conv["id"], "@codex-critico vos qué decís", executor=fake)
    assert [c[0] for c in fake.calls] == ["codex-critico"]


def test_reunion_moderada_sigue_al_moderador_y_cierra(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "m", "reunion", ["gemini-lector", "codex-critico"], mode="moderada")
    fake = FakeExec(["PREGUNTAR @codex-critico: ¿hay riesgos?", "PREGUNTAR @gemini-lector: ¿qué leíste?",
                     "SINTESIS: conclusión final"])
    chat.run_turn(store, conv["id"], "decidan", executor=fake)
    autores = [m["author"] for m in store.get_conversation(conv["id"])["messages"]]
    assert autores == ["usuario", "codex-critico", "gemini-lector", "orquestador"]
    assert store.get_conversation(conv["id"])["messages"][-1]["text"] == "conclusión final"
    assert "¿hay riesgos?" in fake.calls[1][1]


def test_moderacion_tiene_tope(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "m", "reunion", ["codex-critico"], mode="moderada")
    fake = FakeExec(["PREGUNTAR @codex-critico: x"] * 10 + ["SINTESIS: fin"])
    chat.run_turn(store, conv["id"], "go", executor=fake)
    n_agent = sum(1 for c in fake.calls if c[0] == "codex-critico")
    assert n_agent == chat.MAX_MODERATED_STEPS


def test_error_de_un_agente_queda_en_el_mensaje(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "c", "individual", ["codex-critico"])

    def boom(*a):
        return chat.Reply("", error="ESTADO: RATE_LIMIT — cuota agotada")
    chat.run_turn(store, conv["id"], "hola", executor=boom)
    m = store.get_conversation(conv["id"])["messages"][-1]
    assert m["status"] == "error" and "RATE_LIMIT" in m["text"]


def test_ejecutor_real_corre_codex_por_el_runner(store, proyecto, env):
    pr = store.create_project("P", str(proyecto))
    r = chat.default_executor(env, pr, "codex-critico", "hola")
    assert r.error is None and r.text.startswith("PONG")


def test_ejecutor_real_write_crea_worktree_y_se_mergea(store, proyecto, env):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])
    chat.run_turn(store, conv["id"], "WRITE:nuevo.txt", executor=chat.default_executor)
    c = store.get_conversation(conv["id"])
    wt = c["worktrees"][0]
    assert wt["status"] == "abierto" and wt["branch"].startswith("orquesta/codex-forja/")
    assert not (proyecto / "nuevo.txt").exists()
    chat.merge_worktree(store, conv["id"], wt["branch"])
    assert (proyecto / "nuevo.txt").exists()
    assert store.get_conversation(conv["id"])["worktrees"][0]["status"] == "mergeado"


def test_descartar_worktree(store, proyecto, env):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])
    chat.run_turn(store, conv["id"], "WRITE:x.txt", executor=chat.default_executor)
    br = store.get_conversation(conv["id"])["worktrees"][0]["branch"]
    chat.discard_worktree(store, conv["id"], br)
    assert store.get_conversation(conv["id"])["worktrees"][0]["status"] == "descartado"
    assert git(proyecto, "branch", "--list", br) == ""


def test_api_chat_flujo(env, proyecto):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    fake = FakeExec()
    app = create_app(env, port=8787, token="t", run_tests=None, chat_executor=fake)
    c = TestClient(app, base_url="http://127.0.0.1:8787", headers={"X-Orquesta-Token": "t"})
    assert "orquestador" in c.get("/api/chat/participants").json()["participants"]
    pr = c.post("/api/chat/projects", json={"name": "P", "dir": str(proyecto)}).json()
    conv = c.post("/api/chat/conversations", json={"project_id": pr["id"], "title": "t", "kind": "individual",
                                                   "participants": ["codex-critico"]}).json()
    assert c.post(f"/api/chat/conversations/{conv['id']}/messages", json={"text": "hola"}).status_code == 202
    for _ in range(50):
        msgs = c.get(f"/api/chat/conversations/{conv['id']}").json()["messages"]
        if len(msgs) == 2 and msgs[1]["status"] == "done":
            break
        time.sleep(0.05)
    assert msgs[1]["text"] == "respuesta de codex-critico"
    lista = c.get(f"/api/chat/conversations?project={pr['id']}").json()["conversations"]
    assert lista[0]["id"] == conv["id"]


@pytest.mark.parametrize("crudo,limpio", [
    ("[mood: ejecutivo]\n\nEl riesgo es X.", "El riesgo es X."),
    ("  [mood: debate]\nHola", "Hola"),
    ("Sin tag [mood: x] en el medio", "Sin tag [mood: x] en el medio"),
])
def test_se_saca_el_tag_de_mood_del_orquestador(crudo, limpio):
    assert chat.strip_mood_tag(crudo) == limpio


# --- tanda UI 1: cancelar, estado, exportar ------------------------------------------------

def test_procs_cancel_mata_el_grupo_de_procesos():
    import threading
    from orquesta import procs
    res = {}
    t = threading.Thread(target=lambda: res.update(r=procs.run(
        ["bash", "-c", "sleep 30 & sleep 30; echo fin"], key="k1", timeout=60)))
    t.start()
    for _ in range(50):
        if procs.active("k1"):
            break
        time.sleep(0.05)
    t0 = time.monotonic()
    assert procs.cancel("k1") >= 1
    t.join(10)
    assert not t.is_alive() and time.monotonic() - t0 < 5
    assert res["r"].cancelled is True


def test_cancelar_un_turno_marca_el_mensaje_y_corta_la_ronda(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "r", "reunion", ["gemini-lector", "codex-critico"], mode="rondas")

    def lento(paths, project, who, prompt):
        chat.request_cancel(conv["id"])  # el usuario aprieta Detener durante el primer turno
        return chat.Reply("", error=chat.CANCELLED)
    chat.run_turn(store, conv["id"], "hola", executor=lento)
    msgs = store.get_conversation(conv["id"])["messages"]
    assert [m["author"] for m in msgs] == ["usuario", "gemini-lector"]
    assert msgs[1]["status"] == "interrupted"


def test_estado_de_la_conversacion(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "c", "individual", ["codex-critico"])
    assert store.list_conversations()[0]["status"] == "nueva"
    chat.run_turn(store, conv["id"], "hola", executor=FakeExec())
    assert store.list_conversations()[0]["status"] == "lista"
    store.update(conv["id"], lambda c: c["worktrees"].append({"agent": "x", "branch": "b", "status": "abierto"}))
    assert store.list_conversations()[0]["status"] == "esperando-revision"
    store.update(conv["id"], lambda c: c.update(busy=True))
    assert store.list_conversations()[0]["status"] == "respondiendo"


def test_exportar_a_markdown(store, proyecto):
    pr = store.create_project("Empanadas", str(proyecto))
    conv = store.create_conversation(pr["id"], "Charla", "individual", ["codex-critico"])
    chat.run_turn(store, conv["id"], "¿qué opinás?", executor=FakeExec())
    md = chat.export_markdown(store, conv["id"])
    assert md.startswith("# Charla")
    assert "Empanadas" in md and "## usuario" in md and "## codex-critico" in md
    assert "respuesta de codex-critico" in md


def test_api_cancelar_y_exportar(env, proyecto):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    app = create_app(env, port=8787, token="t", run_tests=None, chat_executor=FakeExec())
    c = TestClient(app, base_url="http://127.0.0.1:8787", headers={"X-Orquesta-Token": "t"})
    pr = c.post("/api/chat/projects", json={"name": "P", "dir": str(proyecto)}).json()
    conv = c.post("/api/chat/conversations", json={"project_id": pr["id"], "title": "Exp", "kind": "individual",
                                                   "participants": ["codex-critico"]}).json()
    assert c.post(f"/api/chat/conversations/{conv['id']}/cancel").json()["ok"] is True
    r = c.get(f"/api/chat/conversations/{conv['id']}/export.md")
    assert r.status_code == 200 and r.text.startswith("# Exp")
    assert "attachment" in r.headers["content-disposition"]
