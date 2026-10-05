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
    # ve lo anterior, pero anonimizado (sesgo de identidad, arXiv 2510.07517)
    assert "respuesta de Participante" in fake.calls[1][1] and "gemini-lector" not in fake.calls[1][1].split("Historial")[1]


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
    chat.merge_worktree(store, conv["id"], wt["branch"], force=True)  # el fake no da veredicto
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


# --- tanda 3: modos de reunión con evidencia -----------------------------------------------

class FakeJuez(FakeExec):
    def __call__(self, paths, project, participant, prompt):
        self.calls.append((participant, prompt))
        if participant in ("claude-juez", "orquestador"):
            return chat.Reply("Decisión: X.\nRESULTADO: OK\nCONFIANZA: 80")
        return chat.Reply(f"respuesta de {participant}\nRESULTADO: OK\nCONFIANZA: 60")


def _conv(store, proyecto, mode, participants=("gemini-lector", "codex-critico")):
    pr = store.create_project("P", str(proyecto))
    return store.create_conversation(pr["id"], "m", "reunion", list(participants), mode=mode)


def test_modo_critica_ciega_luego_critica_anonima_y_juez(store, proyecto):
    conv = _conv(store, proyecto, "critica")
    fake = FakeJuez()
    chat.run_turn(store, conv["id"], "¿conviene X?", executor=fake)
    ronda0 = fake.calls[:2]
    assert {c[0] for c in ronda0} == {"gemini-lector", "codex-critico"}
    assert all("respuesta de" not in c[1] for c in ronda0)          # a ciegas
    assert "DESACUERDO" in fake.calls[2][1]                     # detector de desacuerdo (iMAD)
    critica = [c for c in fake.calls[3:5]]
    assert all("Respuesta A" in c[1] and "TU respuesta" in c[1] for c in critica)
    assert all("gemini-lector" not in c[1].split("Respuestas de los demás")[1] for c in critica)  # anónimo
    assert fake.calls[-1][0] == "orquestador"                       # sin claude-juez desplegado: modera el orquestador
    msgs = store.get_conversation(conv["id"])["messages"]
    assert msgs[-1]["meta"]["rol"] == "veredicto"
    assert [m["meta"].get("ronda") for m in msgs[1:5]] == [0, 0, 1, 1]


def test_modo_posturas_asigna_y_rota(store, proyecto):
    conv = _conv(store, proyecto, "posturas", ("gemini-lector", "codex-critico", "orquestador"))
    fake = FakeJuez()
    chat.run_turn(store, conv["id"], "¿migramos a Y?", executor=fake)
    posturas = [m["meta"].get("postura") for m in store.get_conversation(conv["id"])["messages"][1:4]]
    assert posturas == ["a favor", "en contra", "neutral"]
    assert "Tu postura asignada: a favor" in fake.calls[0][1]


def test_modo_seccionado_reparte_aspectos(store, proyecto):
    conv = _conv(store, proyecto, "seccionado")
    fake = FakeJuez()
    chat.run_turn(store, conv["id"], "Revisá el módulo. Aspectos: seguridad; performance", executor=fake)
    prompts = {c[0]: c[1] for c in fake.calls[:2]}
    assert "Tu aspecto: seguridad" in prompts["gemini-lector"]
    assert "Tu aspecto: performance" in prompts["codex-critico"]


def test_la_confianza_del_contrato_queda_en_el_mensaje(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "c", "individual", ["codex-critico"])
    chat.run_turn(store, conv["id"], "hola", executor=FakeJuez())
    assert store.get_conversation(conv["id"])["messages"][-1]["meta"]["contrato"]["confianza"] == 60


# --- tanda 4: auto-review, conflictos, tokens, tablero ------------------------------------

class FakeReview(FakeExec):
    def __init__(self, veredicto="APROBAR"):
        super().__init__()
        self.veredicto = veredicto

    def __call__(self, paths, project, participant, prompt):
        self.calls.append((participant, prompt))
        if participant == "codex-forja":
            (Path(project["dir"])).joinpath("x.txt")
            r = chat.default_executor(paths, project, participant, "WRITE:revisado.txt")
            return r
        return chat.Reply(f"Revisé.\nVeredicto: {self.veredicto}\nRESULTADO: OK\nCONFIANZA: 80")


from pathlib import Path  # noqa: E402


def test_auto_review_de_otra_familia_y_bloqueo_del_merge(store, proyecto, env):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])
    fake = FakeReview("RECHAZAR")
    chat.run_turn(store, conv["id"], "hacelo", executor=fake)
    c = store.get_conversation(conv["id"])
    revisor, prompt = fake.calls[-1]
    assert revisor == "gemini-lector"                      # codex escribió → revisa agy
    assert "revisado.txt" in prompt                        # le llega el diff
    wt = c["worktrees"][0]
    assert wt["review"]["veredicto"] == "RECHAZAR"
    with pytest.raises(chat.NeedsForce):
        chat.merge_worktree(store, conv["id"], wt["branch"])
    chat.merge_worktree(store, conv["id"], wt["branch"], force=True)
    assert (proyecto / "revisado.txt").exists()


def test_auto_review_aprobado_permite_mergear(store, proyecto, env):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])
    chat.run_turn(store, conv["id"], "hacelo", executor=FakeReview("APROBAR"))
    br = store.get_conversation(conv["id"])["worktrees"][0]["branch"]
    chat.merge_worktree(store, conv["id"], br)
    assert (proyecto / "revisado.txt").exists()


def test_conflictos_entre_ramas_y_con_main(env, proyecto):
    from orquesta import worktrees
    a = worktrees.create(env, repo=proyecto, agent="codex-forja", task="uno")
    b = worktrees.create(env, repo=proyecto, agent="codex-forja", task="dos")
    (a.path / "a.txt").write_text("versión A\n")
    worktrees.commit_all(a, "A")
    (b.path / "a.txt").write_text("versión B\n")
    worktrees.commit_all(b, "B")
    info = worktrees.conflicts(env, a.branch)
    assert info["merge_conflict"] is False and b.branch in info["overlaps"]
    (proyecto / "a.txt").write_text("cambio en main\n")
    import subprocess
    subprocess.run(["git", "commit", "-qam", "main"], cwd=proyecto, check=True)
    assert worktrees.conflicts(env, a.branch)["merge_conflict"] is True


def test_tokens_de_agy_quedan_en_meta(env, tmp_path):
    from orquesta import runner
    apply_plan(build_plan(env), "deploy", run_tests=None)
    r = runner.run(runner.RunRequest(agent="gemini-lector", cwd=tmp_path, prompt="hola"), env)
    assert "usage" in r.meta


# --- tanda 5: búsqueda --------------------------------------------------------------------

def test_busqueda_en_todas_las_conversaciones(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    a = store.create_conversation(pr["id"], "Ventas", "individual", ["codex-critico"])
    b = store.create_conversation(pr["id"], "Otra", "individual", ["codex-critico"])
    chat.run_turn(store, a["id"], "hablemos de la humita", executor=FakeExec())
    chat.run_turn(store, b["id"], "nada que ver", executor=FakeExec())
    res = chat.search(store, "HUMITA")
    assert [r["conversation_id"] for r in res] == [a["id"]]
    assert "humita" in res[0]["snippet"].lower() and res[0]["title"] == "Ventas"
    assert chat.search(store, "  ") == []
