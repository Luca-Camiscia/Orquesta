"""Reglas respaldadas por papers: anonimato en todas las reuniones, debate adaptativo, review con evidencia
ejecutable y ciega al autor, calibración de confianza, contexto acotado."""
from __future__ import annotations

import pytest

from helpers import git
from orquesta import calibration, chat
from orquesta.generate import apply_plan, build_plan


class Fake:
    def __init__(self, respuestas=None):
        self.calls, self.respuestas = [], respuestas or {}

    def __call__(self, paths, project, who, prompt):
        self.calls.append((who, prompt))
        r = self.respuestas.get(who)
        if callable(r):
            return r(prompt)
        return chat.Reply(r or f"opino como {who}\nRESULTADO: OK\nCONFIANZA: 70")


@pytest.fixture
def proyecto(tmp_path):
    p = tmp_path / "proyecto"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    (p / "a.py").write_text("x = 1\n")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "init")
    return p


@pytest.fixture
def store(env):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    return chat.Store(env)


def reunion(store, proyecto, mode, ps=("gemini-lector", "codex-critico")):
    pr = store.create_project("P", str(proyecto))
    return store.create_conversation(pr["id"], "r", "reunion", list(ps), mode=mode)


def test_rondas_anonimas_el_segundo_no_ve_nombres(store, proyecto):
    conv = reunion(store, proyecto, "rondas")
    fake = Fake()
    chat.run_turn(store, conv["id"], "¿qué hacemos?", executor=fake)
    segundo = fake.calls[1][1]
    assert "gemini-lector" not in segundo.split("Historial")[1]
    assert "Participante" in segundo


def test_critica_cierra_temprano_si_hay_consenso(store, proyecto):
    conv = reunion(store, proyecto, "critica")
    fake = Fake({"orquestador": "DESACUERDO: NO"})
    chat.run_turn(store, conv["id"], "¿2+2?", executor=fake)
    quienes = [c[0] for c in fake.calls]
    assert quienes.count("gemini-lector") == 1 and quienes.count("codex-critico") == 1  # sin ronda de crítica
    msgs = store.get_conversation(conv["id"])["messages"]
    assert msgs[-1]["meta"]["rol"] == "veredicto"


def test_critica_debate_si_hay_desacuerdo(store, proyecto):
    conv = reunion(store, proyecto, "critica")
    fake = Fake({"orquestador": lambda p: chat.Reply("DESACUERDO: SI" if "DESACUERDO" in p else "veredicto")})
    chat.run_turn(store, conv["id"], "¿A o B?", executor=fake)
    assert [c[0] for c in fake.calls].count("codex-critico") == 2


def test_review_ciega_con_tests_del_proyecto(store, proyecto, env):
    (proyecto / ".orquesta").mkdir()
    (proyecto / ".orquesta" / "proyecto.toml").write_text('[worktree]\ntests = "test -f nuevo.txt"\n')
    git(proyecto, "add", "-A")
    git(proyecto, "commit", "-qm", "config")
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])
    fake = Fake({"codex-forja": lambda p: chat.default_executor(env, pr, "codex-forja", "WRITE:nuevo.txt"),
                 "gemini-lector": "Veredicto: APROBAR\nRESULTADO: OK\nCONFIANZA: 90"})
    chat.run_turn(store, conv["id"], "creá nuevo.txt", executor=fake)
    wt = store.get_conversation(conv["id"])["worktrees"][0]
    assert wt["tests"]["ok"] is True
    prompt_review = fake.calls[-1][1]
    assert "codex-forja" not in prompt_review and "codex" not in prompt_review.lower().split("```diff")[0]
    assert "tests del proyecto: PASARON" in prompt_review


def test_merge_bloqueado_si_fallan_los_tests(store, proyecto, env):
    (proyecto / ".orquesta").mkdir()
    (proyecto / ".orquesta" / "proyecto.toml").write_text('[worktree]\ntests = "exit 1"\n')
    git(proyecto, "add", "-A")
    git(proyecto, "commit", "-qm", "config")
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])
    fake = Fake({"codex-forja": lambda p: chat.default_executor(env, pr, "codex-forja", "WRITE:n.txt"),
                 "gemini-lector": "Veredicto: APROBAR"})
    chat.run_turn(store, conv["id"], "x", executor=fake)
    wt = store.get_conversation(conv["id"])["worktrees"][0]
    with pytest.raises(chat.NeedsForce, match="tests"):
        chat.merge_worktree(store, conv["id"], wt["branch"])


def test_calibracion_registra_confianza_vs_resultado(env):
    for conf, ok in [(90, True), (90, False), (95, False), (60, True), (40, False)]:
        calibration.record(env, backend="codex", agent="x", confianza=conf, acierto=ok, fuente="tests")
    t = calibration.table(env)["codex"]
    alta = next(b for b in t if b["rango"] == "80-100")
    assert alta["n"] == 3 and alta["acierto"] == pytest.approx(1 / 3, abs=0.01)


def test_contexto_acotado_en_conversaciones_largas(store, proyecto):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "c", "individual", ["codex-critico"])
    fake = Fake()
    for i in range(12):
        chat.run_turn(store, conv["id"], f"mensaje número {i}", executor=fake)
    ultimo = fake.calls[-1][1]
    assert "mensaje número 11" in ultimo and "mensaje número 0" not in ultimo
    assert "omitidos" in ultimo


def test_juez_de_una_familia_que_no_participa(store, proyecto, env):
    # el registro de prueba no tiene claude-juez; con un claude participando, juzga otra familia
    assert chat._judge_for(store, ["orquestador", "gemini-lector"]) == "codex-critico"
    assert chat._judge_for(store, ["gemini-lector", "codex-critico"]) == "orquestador"


def test_confianza_baja_del_autor_pide_confirmacion(store, proyecto, env):
    pr = store.create_project("P", str(proyecto))
    conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])

    def autor(p):
        r = chat.default_executor(env, pr, "codex-forja", "WRITE:z.txt")
        r.meta["contrato"] = {"resultado": "OK", "confianza": 60}
        return r
    fake = Fake({"codex-forja": autor, "gemini-lector": "Veredicto: APROBAR"})
    chat.run_turn(store, conv["id"], "x", executor=fake)
    wt = store.get_conversation(conv["id"])["worktrees"][0]
    with pytest.raises(chat.NeedsForce, match="confianza"):
        chat.merge_worktree(store, conv["id"], wt["branch"])
