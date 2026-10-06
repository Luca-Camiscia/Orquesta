"""Reuniones desde terminal: contrato JSON, persistencia y guardrails compartidos."""
import io
import json

import pytest
from fastapi.testclient import TestClient

from helpers import git
from orquesta import chat, cli, manifest, runner
from orquesta.generate import apply_plan, build_plan
from orquesta.ui.app import create_app


@pytest.fixture
def reunion_env(env, monkeypatch, tmp_path):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.setattr("sys.stdin", io.StringIO("¿Qué conviene?\nCon evidencia."))
    proyecto = tmp_path / "proyecto"
    proyecto.mkdir()
    return env, proyecto


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def __call__(self, paths, project, who, prompt):
        self.calls.append((who, prompt))
        if "DESACUERDO:" in prompt:
            return chat.Reply("DESACUERDO: SI")
        if "moderás" in prompt:
            return chat.Reply("SINTESIS: decisión final\nRESULTADO: OK\nCONFIANZA: 85")
        return chat.Reply(f"Respuesta de {who}, comparada con gemini-lector y codex-critico.\n"
                          "RESULTADO: OK\nCONFIANZA: 85")


def invocar(proyecto, capsys, modo="critica", participantes="gemini-lector,codex-critico", extra=()):
    code = cli.main(["reunion", "--modo", modo, "--participantes", participantes,
                     "--cwd", str(proyecto), *extra])
    salida = capsys.readouterr()
    assert salida.err == ""
    assert len(salida.out.splitlines()) == 1
    sobre = json.loads(salida.out)
    assert set(sobre) == {"version", "comando", "estado", "generacion", "datos", "errores", "avisos"}
    assert sobre["version"] == 1 and sobre["comando"] == "reunion"
    return code, sobre


@pytest.mark.parametrize("modo", chat.MODES)
def test_reunion_modos_persisten_y_emiten_sobre(reunion_env, monkeypatch, capsys, modo):
    env, proyecto = reunion_env
    fake = FakeExecutor()
    monkeypatch.setattr(chat, "default_executor", fake)
    code, sobre = invocar(proyecto, capsys, modo)
    assert code == 0 and sobre["estado"] == "OK" and sobre["errores"] == []
    datos = sobre["datos"]
    assert datos["modo"] == modo and datos["cwd"] == str(proyecto.resolve())
    assert datos["titulo"]
    conv = chat.Store(env).get_conversation(datos["conversation_id"])
    assert conv["origen"] == "terminal" and conv["kind"] == "reunion"
    assert conv["messages"][0]["text"] == "¿Qué conviene?\nCon evidencia."
    assert all(set(m) == {"autor", "texto", "confianza"} for m in datos["mensajes"])
    assert any(m["confianza"] == 85 for m in datos["mensajes"])
    for m in datos["mensajes"]:
        assert m["autor"] == "usuario" or m["autor"].startswith("Participante ")
        assert "gemini-lector" not in m["texto"] and "codex-critico" not in m["texto"]
    if modo == "critica":
        assert datos["veredicto"] and datos["juez"] == "orquestador"
        for who, prompt in fake.calls:
            if who == "orquestador":
                continue
            otro = "codex-critico" if who == "gemini-lector" else "gemini-lector"
            assert otro not in prompt


def test_orquesta_off_en_ancestro_frena_sin_crear_conversacion(reunion_env, monkeypatch, capsys):
    env, proyecto = reunion_env
    git(proyecto, "init", "-q", "-b", "main")
    (proyecto / ".orquesta-off").touch()
    subdir = proyecto / "subdir"
    subdir.mkdir()
    monkeypatch.setattr(chat, "default_executor", lambda *a: pytest.fail("no debe ejecutar"))
    code, sobre = invocar(subdir, capsys)
    assert code == 1 and sobre["estado"] == "ORQUESTA_OFF"
    assert ".orquesta-off" in sobre["datos"]["error"] and sobre["errores"]
    assert not (env.home / "conversaciones").exists()


def test_tres_familias_avisa_sin_fallar(reunion_env, monkeypatch, capsys):
    _, proyecto = reunion_env
    monkeypatch.setattr(chat, "default_executor", FakeExecutor())
    code, sobre = invocar(proyecto, capsys, participantes="orquestador,codex-critico,gemini-lector")
    assert code == 0 and sobre["estado"] == "OK"
    assert "el juez (orquestador) es de una familia que participa" in sobre["datos"]["aviso"]
    assert sobre["avisos"] == [sobre["datos"]["aviso"]]


def test_juez_de_otra_familia_no_avisa(reunion_env, monkeypatch, capsys):
    _, proyecto = reunion_env
    monkeypatch.setattr(chat, "default_executor", FakeExecutor())
    _, sobre = invocar(proyecto, capsys)
    assert not sobre["datos"].get("aviso") and sobre["avisos"] == []


def test_aspectos_y_titulo_llegan_a_la_conversacion(reunion_env, monkeypatch, capsys):
    env, proyecto = reunion_env
    fake = FakeExecutor()
    monkeypatch.setattr(chat, "default_executor", fake)
    _, sobre = invocar(proyecto, capsys, "seccionado", extra=("--aspectos", "seguridad; costos", "--titulo", "Diseño"))
    datos = sobre["datos"]
    assert datos["titulo"] == "Diseño"
    conv = chat.Store(env).get_conversation(datos["conversation_id"])
    assert conv["aspectos"] == ["seguridad", "costos"]
    assert any("Tu aspecto: seguridad." in p for _, p in fake.calls)
    assert any("Tu aspecto: costos." in p for _, p in fake.calls)


def test_cwd_default_se_resuelve(reunion_env, monkeypatch, capsys):
    _, proyecto = reunion_env
    monkeypatch.chdir(proyecto)
    monkeypatch.setattr(chat, "default_executor", FakeExecutor())
    assert cli.main(["reunion", "--modo", "rondas", "--participantes", "codex-critico"]) == 0
    assert json.loads(capsys.readouterr().out)["datos"]["cwd"] == str(proyecto.resolve())


@pytest.mark.parametrize("error,estado", [("ESTADO: OCUPADO — sin slot", "OCUPADO"),
                                          ("ESTADO: ERROR — backend falló", "ERROR")])
def test_error_de_executor_sale_en_sobre(reunion_env, monkeypatch, capsys, error, estado):
    _, proyecto = reunion_env
    monkeypatch.setattr(chat, "default_executor", lambda *a: chat.Reply("", error=error))
    code, sobre = invocar(proyecto, capsys, "moderada")
    assert code == 1 and sobre["estado"] == estado
    assert error in sobre["datos"]["error"] and error in sobre["errores"]


@pytest.mark.parametrize("modo", ["rondas", "critica", "seccionado"])
def test_manifest_error_en_executor_sin_traceback(reunion_env, monkeypatch, capsys, modo):
    _, proyecto = reunion_env

    def roto(*a):
        raise manifest.ManifestError("manifiesto inválido")

    monkeypatch.setattr(chat, "default_executor", roto)
    code, sobre = invocar(proyecto, capsys, modo)
    assert code == 1 and sobre["estado"] == "ERROR"
    assert "manifiesto inválido" in sobre["datos"]["error"]


def test_manifest_error_antes_de_ejecutar(reunion_env, monkeypatch, capsys):
    env, proyecto = reunion_env
    env.manifest.write_text("{roto")
    monkeypatch.setattr(chat, "default_executor", lambda *a: pytest.fail("no debe ejecutar"))
    code, sobre = invocar(proyecto, capsys)
    assert code == 1 and sobre["estado"] == "ERROR" and sobre["errores"]


@pytest.mark.parametrize("who", ["orquestador", "mantenedor"])
@pytest.mark.parametrize("limit", [1, 3])
def test_claude_ocupado_respeta_max_parallel(reunion_env, monkeypatch, who, limit):
    env, proyecto = reunion_env
    data = manifest.load(env)
    data["limits"]["max_parallel"]["claude"] = limit
    env.manifest.write_text(json.dumps(data))
    monkeypatch.setattr(chat, "CLAUDE_SLOT_WAIT_S", 0, raising=False)
    monkeypatch.setattr(chat, "_claude", lambda *a: pytest.fail("slot ocupado: no debe ejecutar"))
    with runner.hold_slots(env, "claude", limit):
        reply = chat.default_executor(env, {"dir": str(proyecto)}, who, "hola")
    assert reply.error and "ESTADO: OCUPADO" in reply.error


def test_claude_mantiene_slot_y_lo_libera(reunion_env, monkeypatch):
    env, proyecto = reunion_env
    data = manifest.load(env)
    data["limits"]["max_parallel"]["claude"] = 1
    env.manifest.write_text(json.dumps(data))

    def claude(*a):
        with runner.acquire_slot(env, "claude", 1, 0) as slot:
            assert slot is None
        return chat.Reply("respuesta")

    monkeypatch.setattr(chat, "_claude", claude)
    assert chat.default_executor(env, {"dir": str(proyecto)}, "orquestador", "hola").text == "respuesta"
    with runner.acquire_slot(env, "claude", 1, 0) as slot:
        assert slot == 0


def test_project_for_dir_reusa_y_persiste(reunion_env):
    env, proyecto = reunion_env
    store = chat.Store(env)
    pr = store.project_for_dir(str(proyecto))
    assert pr["name"] == "proyecto" and pr["dir"] == str(proyecto)
    assert store.project_for_dir(str(proyecto))["id"] == pr["id"]
    assert chat.Store(env).project_for_dir(str(proyecto))["id"] == pr["id"]
    assert len(store.list_projects()) == 2


def test_project_for_dir_reusa_proyecto_de_ui(reunion_env):
    env, proyecto = reunion_env
    store = chat.Store(env)
    pr = store.create_project("Nombre elegido", str(proyecto))
    assert store.project_for_dir(str(proyecto))["id"] == pr["id"]
    assert len(store.list_projects()) == 2


@pytest.mark.parametrize("dirname", ["orquesta", "otro/"])
def test_project_for_dir_reusa_con_nombre_reservado_o_barra_final(reunion_env, dirname):
    env, proyecto = reunion_env
    directory = str(proyecto / dirname) + ("/" if dirname.endswith("/") else "")
    (proyecto / dirname).mkdir()
    store = chat.Store(env)
    pr = store.project_for_dir(directory)
    assert pr["dir"] == directory and pr["name"] == dirname.rstrip("/")
    assert store.project_for_dir(directory)["id"] == pr["id"]
    assert len(store.list_projects()) == 2


def test_juez_claude_tambien_requiere_slot(reunion_env, monkeypatch, capsys):
    env, proyecto = reunion_env
    data = manifest.load(env)
    data["limits"]["max_parallel"]["claude"] = 1
    env.manifest.write_text(json.dumps(data))
    monkeypatch.setattr(chat, "CLAUDE_SLOT_WAIT_S", 0)
    monkeypatch.setattr(chat, "_claude", lambda *a: pytest.fail("el juez no tiene slot"))
    real = chat.default_executor

    def executor(paths, project, who, prompt):
        if who == "orquestador":
            return real(paths, project, who, prompt)
        return chat.Reply("RESULTADO: OK\nCONFIANZA: 80")

    monkeypatch.setattr(chat, "default_executor", executor)
    with runner.hold_slots(env, "claude", 1):
        code, sobre = invocar(proyecto, capsys)
    assert code == 1 and sobre["estado"] == "OCUPADO"
    assert sobre["datos"]["juez"] == "orquestador" and "OCUPADO" in sobre["datos"]["error"]


def test_pregunta_del_moderador_anonimiza_al_otro_participante(reunion_env, monkeypatch, capsys):
    _, proyecto = reunion_env
    prompts = []
    preguntas = iter(["PREGUNTAR @codex-critico: revisá lo que dijo gemini-lector", "SINTESIS: fin"])

    def executor(paths, project, who, prompt):
        if who == "orquestador":
            return chat.Reply(next(preguntas))
        prompts.append(prompt)
        return chat.Reply("opinión")

    monkeypatch.setattr(chat, "default_executor", executor)
    code, sobre = invocar(proyecto, capsys, "moderada")
    assert code == 0 and sobre["estado"] == "OK"
    assert len(prompts) == 1 and "gemini-lector" not in prompts[0]


def test_origen_default_none(reunion_env):
    env, proyecto = reunion_env
    store = chat.Store(env)
    project = store.create_project("P", str(proyecto))
    conv = store.create_conversation(project["id"], None, "reunion", ["codex-critico"])
    assert store.get_conversation(conv["id"])["origen"] is None


def test_origen_ui_y_chats_viejos_conservan_formato(reunion_env):
    env, proyecto = reunion_env
    store = chat.Store(env)
    pr = store.create_project("P", str(proyecto))
    app = create_app(env, port=8787, token="t", run_tests=None, chat_executor=FakeExecutor())
    client = TestClient(app, base_url="http://127.0.0.1:8787", headers={"X-Orquesta-Token": "t"})
    conv = client.post("/api/chat/conversations", json={"project_id": pr["id"], "participants": ["codex-critico"]}).json()
    assert conv["origen"] == "ui"
    del conv["origen"]
    store.save(conv)
    chat.run_turn(store, conv["id"], "hola", executor=FakeExecutor())
    assert "origen" not in store.get_conversation(conv["id"])
