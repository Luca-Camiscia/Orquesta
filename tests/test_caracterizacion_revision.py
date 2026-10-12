"""Caracterización previa al refactor: estos tests fijan el comportamiento actual."""
from __future__ import annotations

import pytest

from helpers import dump_yaml, git, registry_dict
from orquesta import chat, revision as reglas_revision, worktrees
from orquesta.generate import apply_plan, build_plan
from test_chat import proyecto, store  # noqa: F401  (fixtures compartidas)
from test_reuniones_evidencia import Fake


def diff_test(cambios, archivo="tests/test_algo.py"):
    return (f"diff --git a/{archivo} b/{archivo}\n--- a/{archivo}\n+++ b/{archivo}\n"
            f"@@ -1,8 +1,8 @@\n{cambios}\n")


@pytest.mark.parametrize("cambios", [
    "-def test_algo():",
    "-    assert resultado == 1",
    "+    pytest.skip ('pendiente')",
    "+@pytest.mark.skip",
    "+    @pytest.mark.skipif(True, reason='pendiente')",
    "+    @unittest.skip('pendiente')",
    "+    self.skipTest ('pendiente')",
    "+@pytest.mark.xfail(reason='pendiente')",
    "+    decorador(skipif=True)",
    '     """\n-    assert resultado == 1\n     """',
    "     '''\n-    assert resultado == 1\n     '''",
    '-    assert resultado == 1\n     """',
    '-    assert uno\n-    assert dos\n+    assert uno_y_dos',
    '-    assert uno\n contexto\n contexto\n contexto\n+    assert otro',
    '-    assert uno\n@@ -20,2 +20,2 @@\n+    assert otro',
    '-def test_algo():\n-    assert uno\n-    assert dos\n+def test_algo():\n+    assert uno',
])
def test_tests_debilitados_detecta_borrados_y_desactivaciones(cambios):
    motivos = reglas_revision.tests_debilitados(diff_test(cambios))
    assert motivos and all("tests/test_algo.py" in motivo for motivo in motivos)


@pytest.mark.parametrize("cambios,archivo", [
    ("+def test_nuevo():\n+    assert resultado", "tests/test_algo.py"),
    ("-    assert resultado == 1\n+    assert resultado == 2", "tests/test_algo.py"),
    ("-def test_algo():\n+def test_algo():", "tests/test_algo.py"),
    ("     @pytest.mark.skipif(True, reason='existente')\n+    valor = 2", "tests/test_algo.py"),
    ("-    assert resultado", "src/servicio.py"),
    ("+    pytest.skip('pendiente')", "src/servicio.py"),
    ("+# pytest.skip('comentario')", "tests/test_algo.py"),
    ('+    texto = "pytest.skip(esto)"', "tests/test_algo.py"),
    ('-    """\n-    assert ejemplo\n-    """', "tests/test_algo.py"),
    ("+    '''\n+    pytest.skip('ejemplo')\n+    '''", "tests/test_algo.py"),
    ('+    """ejemplo pytest.skip()"""', "tests/test_algo.py"),
    ("+    assert not_skipif == 2", "tests/test_algo.py"),
])
def test_tests_debilitados_evita_falsos_positivos(cambios, archivo):
    assert reglas_revision.tests_debilitados(diff_test(cambios, archivo)) == []


def test_tests_debilitados_test_trasladado_entre_archivos():
    diff = diff_test("-def test_algo():\n-    assert resultado", "tests/test_viejo.py")
    diff += diff_test("+def test_algo():\n+    assert resultado", "tests/test_nuevo.py")
    assert reglas_revision.tests_debilitados(diff) == []


def test_tests_debilitados_deduplica_por_archivo_y_tipo():
    diff = diff_test("-def test_uno():\n-    assert uno\n-def test_dos():\n-    assert dos\n"
                     "+    pytest.skip('uno')\n+    self.skipTest('dos')")
    motivos = reglas_revision.tests_debilitados(diff)
    assert len(motivos) == 3 and len(set(motivos)) == 3
    assert len(reglas_revision.tests_debilitados(diff + diff_test("-    assert otro", "test_otro.py"))) == 4


def test_razones_bloqueo_con_diff_y_sin_diff():
    registro = {"branch": "orquesta/autor/fix", "review": {"veredicto": "APROBAR"}}
    assert reglas_revision.razones_bloqueo(registro) == []
    assert reglas_revision.razones_bloqueo({**registro, "diff": ""}) == []
    motivos = reglas_revision.razones_bloqueo({**registro, "diff": diff_test("-    assert resultado")})
    assert len(motivos) == 1
    assert "orquesta/autor/fix" in motivos[0] and "debilita los tests" in motivos[0]
    assert "confirmalo explícitamente" in motivos[0]


@pytest.fixture
def revision(store, proyecto, env):
    """Autor real sobre el backend de prueba; solo la respuesta del revisor es falsa."""
    def crear(texto="Veredicto: APROBAR", confianza=90, comando_tests=None):
        if comando_tests is not None:
            config = proyecto / ".orquesta" / "proyecto.toml"
            config.parent.mkdir()
            config.write_text(f'[worktree]\ntests = "{comando_tests}"\n', encoding="utf-8")
            git(proyecto, "add", "-A")
            git(proyecto, "commit", "-qm", "config de tests")
        pr = store.create_project("P", str(proyecto))
        conv = store.create_conversation(pr["id"], "w", "individual", ["codex-forja"])

        def autor(prompt):
            reply = chat.default_executor(env, pr, "codex-forja", "WRITE:revisado.txt")
            reply.meta["contrato"] = {"resultado": "OK", "confianza": confianza}
            return reply

        fake = Fake({"codex-forja": autor, "gemini-lector": texto})
        chat.run_turn(store, conv["id"], "creá revisado.txt", executor=fake)
        return conv["id"], store.get_conversation(conv["id"])["worktrees"][0]

    return crear


@pytest.mark.parametrize("texto,esperado", [
    ("Veredicto: APROBAR", "APROBAR"),
    ("vErEdIcTo: aPrObAr", "APROBAR"),
    ("Veredicto: RECHAZAR", "RECHAZAR"),
    ("VEREDICTO: rechazar", "RECHAZAR"),
    ("Veredicto: aprobar con cambios", "APROBAR CON CAMBIOS"),
    ("Revisé el diff.\nVeredicto:\tAPROBAR\nRESULTADO: OK\nCONFIANZA: 90", "APROBAR"),
    ("Veredicto:\nRECHAZAR", "RECHAZAR"),
    ("El cambio está bien, aprobaría el merge.", None),
    ("Veredicto: INCONCLUSO", None),
    # La regex toma la primera coincidencia, incluso dentro de otra línea.
    ("Resumen: Veredicto: RECHAZAR\nVeredicto: APROBAR", "RECHAZAR"),
    # Espacios de más no convierten "APROBAR CON CAMBIOS" en "APROBAR".
    ("Veredicto: APROBAR  CON CAMBIOS", "APROBAR CON CAMBIOS"),
    ("Veredicto: APROBAR con reservas", None),
    ("Veredicto: APROBARLO", None),
])
def test_auto_review_parsea_y_persiste_el_veredicto_actual(revision, store, proyecto, texto, esperado):
    cid, wt = revision(texto=texto)
    assert wt["review"] == {"by": "gemini-lector", "veredicto": esperado}
    mensaje = store.get_conversation(cid)["messages"][-1]
    assert mensaje["author"] == "gemini-lector"
    assert mensaje["text"] == texto
    assert mensaje["meta"]["rol"] == "review"
    assert mensaje["meta"]["branch"] == wt["branch"]
    if esperado == "APROBAR":
        chat.merge_worktree(store, cid, wt["branch"])
        assert (proyecto / "revisado.txt").is_file()
    else:
        with pytest.raises(chat.NeedsForce, match="review"):
            chat.merge_worktree(store, cid, wt["branch"])
        assert not (proyecto / "revisado.txt").exists()


@pytest.mark.parametrize("confianza", [None, 75, 100])
@pytest.mark.parametrize("comando_tests", [None, "test -f revisado.txt"])
def test_merge_aprobado_acepta_umbral_y_confianza_ausente(
    revision, store, proyecto, env, confianza, comando_tests,
):
    cid, wt = revision(confianza=confianza, comando_tests=comando_tests)
    assert wt["confianza_autor"] == confianza
    if comando_tests is None:
        assert wt.get("tests") is None
    else:
        assert wt["tests"]["ok"] is True
    chat.merge_worktree(store, cid, wt["branch"])
    assert (proyecto / "revisado.txt").is_file()
    assert store.get_conversation(cid)["worktrees"][0]["status"] == "mergeado"
    assert worktrees.list_worktrees(env) == []


@pytest.mark.parametrize("texto,confianza,comando_tests,motivo", [
    ("Veredicto: APROBAR", 90, "exit 1", "tests"),
    ("Veredicto: APROBAR", 74, None, "confianza"),
    ("Veredicto: APROBAR", 0, None, "confianza"),
    ("Veredicto: RECHAZAR", 90, None, "RECHAZAR"),
    ("Veredicto: APROBAR CON CAMBIOS", 90, None, "APROBAR CON CAMBIOS"),
    ("No hay veredicto explícito.", 90, None, "sin review"),
    # Con varias barreras se informa primero tests, luego confianza, luego review.
    ("Veredicto: RECHAZAR", 74, "exit 1", "tests"),
    ("Veredicto: RECHAZAR", 74, None, "confianza"),
])
def test_merge_exige_force_y_force_permite_mergear(
    revision, store, proyecto, env, texto, confianza, comando_tests, motivo,
):
    cid, wt = revision(texto, confianza, comando_tests)
    head = git(proyecto, "rev-parse", "HEAD")
    with pytest.raises(chat.NeedsForce, match=motivo):
        chat.merge_worktree(store, cid, wt["branch"])
    assert git(proyecto, "rev-parse", "HEAD") == head
    assert not (proyecto / "revisado.txt").exists()
    assert store.get_conversation(cid)["worktrees"][0]["status"] == "abierto"
    assert len(worktrees.list_worktrees(env)) == 1
    chat.merge_worktree(store, cid, wt["branch"], force=True)
    assert (proyecto / "revisado.txt").is_file()
    assert store.get_conversation(cid)["worktrees"][0]["status"] == "mergeado"
    assert worktrees.list_worktrees(env) == []


def test_merge_mira_solo_los_registros_del_worktree_elegido(revision, store, proyecto):
    cid, wt = revision()
    otro = {"branch": "otra-rama", "status": "abierto", "tests": {"ok": False, "cmd": "exit 1"},
            "confianza_autor": 0, "review": {"veredicto": "RECHAZAR"}}
    store.update(cid, lambda c: c["worktrees"].insert(0, otro))
    chat.merge_worktree(store, cid, wt["branch"])
    assert (proyecto / "revisado.txt").is_file()
    assert store.get_conversation(cid)["worktrees"] == [otro, {**wt, "status": "mergeado"}]


def test_merge_sin_registro_de_review_exige_force(revision, store):
    cid, wt = revision()
    store.update(cid, lambda c: c["worktrees"][0].pop("review"))
    with pytest.raises(chat.NeedsForce, match="sin review"):
        chat.merge_worktree(store, cid, wt["branch"])
    chat.merge_worktree(store, cid, wt["branch"], force=True)
    assert store.get_conversation(cid)["worktrees"][0]["status"] == "mergeado"


@pytest.mark.parametrize("estado", ["mergeado", "descartado", "rama-ausente"])
@pytest.mark.parametrize("force", [False, True])
def test_force_no_saltea_la_validacion_de_worktree_abierto(revision, store, estado, force):
    cid, wt = revision()
    if estado != "rama-ausente":
        store.update(cid, lambda c: c["worktrees"][0].update(status=estado))
    branch = "rama-ausente" if estado == "rama-ausente" else wt["branch"]
    with pytest.raises(chat.ChatError, match="no hay un worktree abierto") as exc:
        chat.merge_worktree(store, cid, branch, force=force)
    assert not isinstance(exc.value, chat.NeedsForce)


def test_force_no_saltea_conflictos_de_git(revision, store, proyecto, env):
    cid, wt = revision(texto="Veredicto: RECHAZAR")
    (proyecto / "revisado.txt").write_text("cambio distinto en main\n", encoding="utf-8")
    git(proyecto, "add", "-A")
    git(proyecto, "commit", "-qm", "cambio en main")
    head = git(proyecto, "rev-parse", "HEAD")
    with pytest.raises(chat.ChatError, match="el merge falló y se abortó") as exc:
        chat.merge_worktree(store, cid, wt["branch"], force=True)
    assert not isinstance(exc.value, chat.NeedsForce)
    assert git(proyecto, "rev-parse", "HEAD") == head
    assert git(proyecto, "status", "--porcelain") == ""
    assert store.get_conversation(cid)["worktrees"][0]["status"] == "abierto"
    assert len(worktrees.list_worktrees(env)) == 1


def desplegar(env, agentes):
    """Configura el registro y genera el manifiesto por la vía pública, sin mockearlo."""
    datos = registry_dict()
    datos["agents"] = agentes
    dump_yaml(datos, env.registry)
    apply_plan(build_plan(env), "deploy", run_tests=None)
    return chat.Store(env)


@pytest.mark.parametrize("writer,esperado", [
    ("codex-forja", "gemini-lector"),
    ("gemini-lector", "codex-critico"),
    ("mantenedor", "codex-critico"),
])
def test_reviewer_elige_otra_familia_y_solo_read_only(store, writer, esperado):
    assert chat._reviewer_for(store, writer) == esperado


@pytest.mark.parametrize("alternativa", ["ausente", "write", "deshabilitada"])
def test_reviewer_sin_otra_familia_disponible_usa_orquestador(env, alternativa):
    agentes = registry_dict()["agents"]
    lector = agentes[0]
    if alternativa == "write":
        lector["name"] = "gemini-escritor"
        lector["mode"] = "write"
    elif alternativa == "deshabilitada":
        lector["enabled"] = False
    seleccion = agentes[1:] + ([lector] if alternativa != "ausente" else [])
    store = desplegar(env, seleccion)
    assert chat._reviewer_for(store, "codex-forja") == "orquestador"


def test_reviewer_prioriza_candidato_preferido_de_otra_familia(env):
    agentes = registry_dict()["agents"]
    agentes += [{**agentes[2], "name": "codex-abogado-del-diablo"},
                {**agentes[0], "name": "gemini-bibliotecario"}]
    store = desplegar(env, agentes)
    # El candidato codex preferido no puede revisar a un autor codex.
    assert chat._reviewer_for(store, "codex-forja") == "gemini-bibliotecario"
    assert chat._reviewer_for(store, "gemini-lector") == "codex-abogado-del-diablo"


@pytest.mark.parametrize("participants,esperado", [
    (["gemini-lector", "codex-critico"], "orquestador"),
    (["orquestador", "gemini-lector"], "codex-critico"),
    (["orquestador", "codex-critico"], "gemini-lector"),
    (["orquestador", "gemini-lector", "codex-critico"], "orquestador"),
])
def test_judge_sin_claude_juez_busca_familia_ausente_o_usa_orquestador(store, participants, esperado):
    assert chat._judge_for(store, participants) == esperado


def test_judge_si_la_familia_ausente_no_tiene_candidato_usa_orquestador(env):
    agentes = registry_dict()["agents"]
    store = desplegar(env, agentes[1:])  # agy no está desplegado
    assert chat._judge_for(store, ["orquestador", "codex-critico"]) == "orquestador"


@pytest.mark.parametrize("participants", [
    ["gemini-lector", "codex-critico"],
    ["orquestador", "gemini-lector", "codex-critico"],
])
def test_judge_con_claude_juez_lo_usa_para_familia_ausente_o_tres_familias(env, participants):
    agentes = registry_dict()["agents"]
    agentes.append({"name": "claude-juez", "backend": "claude", "role": "Juzga propuestas.",
                    "tools": ["Read"], "prompt": "Juzgá por evidencia."})
    store = desplegar(env, agentes)
    assert chat._judge_for(store, participants) == "claude-juez"
