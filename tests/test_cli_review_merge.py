"""CLI con git real, executor simulado y confirmación TTY inyectada."""
import json
import shlex
from unittest.mock import Mock

import pytest

from helpers import dump_yaml, git, load_yaml
from orquesta import chat, cli, confirmar, revision, runner, tareas, worktrees
from orquesta.generate import apply_plan, build_plan


def configurar_tests(repo, cmd):
    """El comando de tests sale siempre de .orquesta/proyecto.toml (sin versionar: no ensucia el merge)."""
    config = repo / ".orquesta" / "proyecto.toml"
    if cmd is None:
        config.unlink(missing_ok=True)
        return
    config.parent.mkdir(exist_ok=True)
    config.write_text(f"[worktree]\ntests = {cmd!r}\n".replace("'", '"'))


@pytest.fixture
def tarea(env, tmp_path, monkeypatch):
    repo = tmp_path / "proyecto con espacios"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("hola\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    wt = worktrees.create(env, repo=repo, agent="codex-forjador", task="fix")
    (wt.path / "b.txt").write_text("nuevo\n")
    worktrees.commit_all(wt, "fix")
    sha = git(wt.path, "rev-parse", "HEAD")
    configurar_tests(repo, "true")
    pid = worktrees.project_id(repo)
    tareas.registrar(pid, {"repo": str(repo), "rama": wt.branch, "agente": wt.agent,
                          "destino": "main", "base_sha": wt.base_sha, "tests_cmd": "true",
                          "sha_revisado": sha, "tests_sha": sha, "tests_ok": True,
                          "veredicto": "APROBAR", "confianza_autor": 90,
                          "sha_autor": sha}, paths=env)
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.chdir(repo)
    return wt, pid, sha


def salida(capsys):
    texto = capsys.readouterr().out
    assert len(texto.splitlines()) == 1
    return json.loads(texto)


def tty(monkeypatch, texto, original=confirmar.pedir_confirmacion):
    monkeypatch.setattr(confirmar, "pedir_confirmacion", lambda **kw:
                        original(**kw, leer_tty=lambda prompt: texto))


@pytest.mark.parametrize("confianza,commit_posterior", [(20, False), (90, False), (None, False), (20, True)])
def test_run_review_merge_confianza_del_autor(
        env, monkeypatch, capsys, confianza, commit_posterior):
    plan = build_plan(env)
    assert not plan.errors and not plan.violations
    apply_plan(plan, "deploy", run_tests=None)
    configurar_tests(env.home, "true")
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    contrato = f" CONTRACT:{confianza}" if confianza is not None else ""
    result = runner.run(runner.RunRequest(
        agent="codex-forja", cwd=env.home, prompt=f"WRITE:nuevo.txt{contrato}", task="fix"), env)
    assert result.status == "OK" and result.meta["commit"], result.detail
    wt = worktrees._find(env, result.meta["branch"], env.home)
    sha_autor = git(wt.path, "rev-parse", "HEAD")
    if commit_posterior:
        git(wt.path, "commit", "--allow-empty", "-qm", "posterior")
    sha = git(wt.path, "rev-parse", "HEAD")
    args = cli.build_parser().parse_args([
        "review", wt.branch, "--repo", str(wt.repo), "--revisor", "gemini-lector"])
    assert cli.cmd_review(args, executor=lambda *a: chat.Reply("Veredicto: APROBAR")) == 0
    review = salida(capsys)["datos"]
    args = cli.build_parser().parse_args(["merge", wt.branch, "--repo", str(wt.repo)])
    args.confirm = True  # confirmación simulada, solo sobre el repo temporal
    bloqueada = confianza == 20 and not commit_posterior
    assert cli.cmd_merge(args, leer_tty=lambda prompt: wt.branch) == (3 if bloqueada else 0)
    respuesta = salida(capsys)
    if bloqueada:
        assert respuesta["estado"] == "BLOQUEADO"
        assert "confianza" in str(respuesta["datos"]["razones"])
        assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha
    else:
        assert respuesta["estado"] == "OK"
        assert git(wt.repo, "rev-parse", "HEAD") == sha
    assert review["confianza_autor"] == (None if commit_posterior else confianza)
    assert review["sha_autor"] == sha_autor


def test_review_con_veredicto_rechazar(env, tarea, monkeypatch, capsys):
    wt, pid, sha = tarea
    manifest = {"agents": {wt.agent: {"backend": "codex", "mode": "write"},
                           "gemini-reclutador": {"backend": "agy", "mode": "read-only"}}}
    monkeypatch.setattr(chat.mf, "load", lambda paths: manifest)

    def revisar(paths, proyecto, agente, prompt):
        assert paths == env and proyecto["dir"] == str(wt.path)
        assert agente == "gemini-reclutador"
        assert "b.txt" in prompt and "PASARON" in prompt
        return chat.Reply("Veredicto: RECHAZAR\nRESULTADO: OK\nCONFIANZA: 98")

    monkeypatch.setattr(chat, "default_executor", Mock(side_effect=revisar))
    assert cli.main(["review", wt.branch, "--repo", str(wt.repo)]) == 0
    datos = salida(capsys)["datos"]
    assert datos["veredicto"] == "RECHAZAR" and datos["tests_ok"] is True
    registro = tareas.obtener(pid, wt.branch, paths=env)
    assert registro["sha_revisado"] == registro["tests_sha"] == sha
    assert registro["revisor"] == "gemini-reclutador"
    assert registro["confianza_autor"] == 90  # no atribuirle al autor la confianza del revisor


def test_review_con_revisor_explicito(env, tarea, monkeypatch, capsys):
    wt, pid, sha = tarea
    monkeypatch.setattr(chat.mf, "load", lambda paths: {"agents": {
        wt.agent: {"backend": "codex", "mode": "write"},
        "gemini-bibliotecario": {"backend": "agy", "mode": "read-only"},
        "claude-centinela": {"backend": "claude", "mode": "read-only"}}})

    def revisar(paths, proyecto, agente, prompt):
        assert agente == "claude-centinela"
        assert "b.txt" in prompt and "PASARON" in prompt
        assert wt.agent not in prompt and str(wt.path) not in prompt  # review ciega al autor
        return chat.Reply("Veredicto: APROBAR")

    args = cli.build_parser().parse_args(["review", wt.branch, "--revisor", "claude-centinela"])
    assert cli.cmd_review(args, executor=revisar) == 0
    respuesta = salida(capsys)
    assert respuesta["estado"] == "OK"
    assert respuesta["datos"]["revisor"] == "claude-centinela"
    registro = tareas.obtener(pid, wt.branch, paths=env)
    assert registro["revisor"] == "claude-centinela"
    assert registro["sha_revisado"] == registro["tests_sha"] == sha


@pytest.mark.parametrize("nombre,agente,motivo", [
    ("inexistente", None, "no existe"),
    ("codex-critico", {"backend": "codex", "mode": "read-only"}, "otra familia"),
    ("claude-escritor", {"backend": "claude", "mode": "write"}, "read-only"),
])
def test_review_revisor_invalido_conserva_evidencia(
        env, tarea, monkeypatch, capsys, nombre, agente, motivo):
    wt, pid, _ = tarea
    agents = {wt.agent: {"backend": "codex", "mode": "write"}}
    if agente is not None:
        agents[nombre] = agente
    monkeypatch.setattr(chat.mf, "load", lambda paths: {"agents": agents})
    anterior = tareas.obtener(pid, wt.branch, paths=env)

    def no_ejecutar(*args, **kwargs):
        pytest.fail("un revisor inválido no debe actualizar la tarea ni ejecutar tests o review")

    monkeypatch.setattr(tareas, "actualizar", no_ejecutar)
    monkeypatch.setattr(worktrees, "run_tests", no_ejecutar)
    args = cli.build_parser().parse_args(["review", wt.branch, "--revisor", nombre])
    assert cli.cmd_review(args, executor=no_ejecutar) == 1
    respuesta = salida(capsys)
    assert respuesta["estado"] == "ERROR"
    assert motivo in respuesta["errores"][0]
    assert tareas.obtener(pid, wt.branch, paths=env) == anterior


def test_review_sin_revisor_usa_seleccion_automatica(env, tarea, monkeypatch, capsys):
    wt, pid, _ = tarea
    manifest = {"agents": {
        wt.agent: {"backend": "codex", "mode": "write"},
        "gemini-bibliotecario": {"backend": "agy", "mode": "read-only"},
        "claude-centinela": {"backend": "claude", "mode": "read-only"}}}
    monkeypatch.setattr(chat.mf, "load", lambda paths: manifest)
    elegir = Mock(wraps=revision.elegir_revisor)
    monkeypatch.setattr(revision, "elegir_revisor", elegir)

    def revisar(paths, proyecto, agente, prompt):
        assert agente == "gemini-bibliotecario"
        return chat.Reply("Veredicto: APROBAR")

    args = cli.build_parser().parse_args(["review", wt.branch])
    assert cli.cmd_review(args, executor=revisar) == 0
    assert salida(capsys)["datos"]["revisor"] == "gemini-bibliotecario"
    elegir.assert_called_once()
    assert elegir.call_args.args == (manifest, wt.agent)
    disponible = elegir.call_args.kwargs["disponible"]
    assert disponible("agy") and disponible("claude")
    assert tareas.obtener(pid, wt.branch, paths=env)["revisor"] == "gemini-bibliotecario"


@pytest.mark.parametrize("via", ["cli", "chat"])
@pytest.mark.parametrize("con_claude", [False, True])
def test_review_no_elige_backend_sin_binario(env, tarea, monkeypatch, capsys, via, con_claude):
    wt, _, _ = tarea
    backends = load_yaml(env.backends)
    backends["backends"]["agy"]["command"] = ["orquesta-backend-ausente-regresion"]
    # Claude es nativo: cuenta aunque su comando tampoco esté instalado.
    backends["backends"]["claude"]["command"] = ["orquesta-claude-ausente-regresion"]
    dump_yaml(backends, env.backends)
    agents = {
        wt.agent: {"backend": "codex", "mode": "write"},
        "gemini-bibliotecario": {"backend": "agy", "mode": "read-only"},
    }
    if con_claude:
        agents["claude-centinela"] = {"backend": "claude", "mode": "read-only"}
    monkeypatch.setattr(chat.mf, "load", lambda paths: {"agents": agents})
    if via == "chat":
        assert chat._reviewer_for(chat.Store(env), wt.agent) == (
            "claude-centinela" if con_claude else "orquestador")
        return

    def revisar(paths, project, agent, prompt):
        assert con_claude and agent == "claude-centinela"
        return chat.Reply("Veredicto: APROBAR")

    args = cli.build_parser().parse_args(["review", wt.branch])
    assert cli.cmd_review(args, executor=revisar) == (0 if con_claude else 1)
    respuesta = salida(capsys)
    if con_claude:
        assert respuesta["datos"]["revisor"] == "claude-centinela"
    else:
        assert "no hay un revisor de otra familia disponible" in str(respuesta)


def test_merge_sin_confirm_necesita_confirmacion(tarea, capsys):
    wt, _, sha = tarea
    assert cli.main(["merge", wt.branch, "--repo", str(wt.repo)]) == 3
    respuesta = salida(capsys)
    assert respuesta["estado"] == "NECESITO_CONFIRMACION"
    assert shlex.split(respuesta["datos"]["comando"]) == [
        "orquesta", "merge", wt.branch, "--repo", str(wt.repo), "--confirm"]
    assert git(wt.repo, "rev-parse", "HEAD") != sha


def test_merge_con_confirm_bloqueado_veredicto_rechazar(env, tarea, capsys):
    wt, pid, sha = tarea
    tareas.actualizar(pid, wt.branch, {"veredicto": "RECHAZAR"}, paths=env)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 3
    assert salida(capsys)["estado"] == "BLOQUEADO"
    assert git(wt.repo, "rev-parse", "HEAD") != sha


def test_merge_con_confirm_forzar_y_tty_falso(env, tarea, monkeypatch, capsys):
    wt, pid, sha = tarea
    tareas.actualizar(pid, wt.branch, {"veredicto": "RECHAZAR"}, paths=env)
    tty(monkeypatch, f"forzar {wt.branch}")
    assert cli.main(["merge", wt.branch, "--forzar", "--confirm"]) == 0
    assert salida(capsys)["datos"]["sha_merged"] == sha
    assert git(wt.repo, "rev-parse", "HEAD") == sha
    assert tareas.obtener(pid, wt.branch, paths=env)["status"] == "mergeado"


@pytest.mark.parametrize("campo", ["sha_revisado", "tests_sha"])
def test_merge_con_commit_nuevo_despues_review(env, tarea, campo, capsys):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, {campo: wt.base_sha}, paths=env)
    assert cli.main(["merge", wt.branch, "--confirm", "--forzar"]) == 3
    respuesta = salida(capsys)
    assert respuesta["estado"] == "BLOQUEADO" and "review vieja" in str(respuesta)
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


def test_merge_rama_en_dos_repos_sin_repo_error(env, tarea, tmp_path, monkeypatch, capsys):
    wt, _, sha = tarea
    otro = tmp_path / "otro"
    git(tmp_path, "clone", "-q", str(wt.repo), str(otro))
    worktrees.create(env, repo=otro, agent=wt.agent, task="fix")
    assert cli.main(["merge", wt.branch]) == 1
    assert "varios repos registrados" in str(salida(capsys))
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--repo", str(wt.repo), "--confirm"]) == 0
    assert salida(capsys)["estado"] == "OK"
    assert git(wt.repo, "rev-parse", "HEAD") == sha
    assert git(otro, "rev-parse", "HEAD") == wt.base_sha


def test_merge_sin_tty_necesita_confirmacion(tarea, monkeypatch, capsys):
    wt, _, _ = tarea

    def sin_tty(**kwargs):
        raise confirmar.SinTTY(kwargs["comando"])

    monkeypatch.setattr(confirmar, "pedir_confirmacion", sin_tty)
    assert cli.main(["merge", wt.branch, "--confirm", "--forzar"]) == 3
    respuesta = salida(capsys)
    assert respuesta["estado"] == "NECESITO_CONFIRMACION"
    assert "--forzar" in respuesta["datos"]["comando"]


def test_merge_tty_texto_incorrecto_no_mergea(tarea, monkeypatch, capsys):
    wt, _, _ = tarea
    tty(monkeypatch, "otra")
    assert cli.main(["merge", wt.branch, "--confirm"]) == 1
    assert "confirmación cancelada" in str(salida(capsys))
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


@pytest.mark.parametrize("problema", ["rama", "sucio"])
def test_repo_otra_rama_o_sucio_bloqueado(tarea, monkeypatch, capsys, problema):
    wt, _, _ = tarea
    if problema == "rama":
        git(wt.repo, "checkout", "-qb", "otra")
    else:
        (wt.repo / "a.txt").write_text("cambio sin commit\n")  # versionado: los sin versionar no bloquean
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 3
    assert salida(capsys)["estado"] == "BLOQUEADO"


def test_tareas_linea_una_sola_linea(env, tarea, capsys):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, {"veredicto": "RECHAZAR"}, paths=env)
    assert cli.main(["tareas", "--linea"]) == 0
    lineas = capsys.readouterr().out.splitlines()
    assert len(lineas) == 1 and not lineas[0].startswith("{")
    assert "1 tareas abiertas" in lineas[0] and "1 en review" in lineas[0] and "1 bloqueadas" in lineas[0]


def test_tareas_json_agrega_worktrees_del_proyecto(tarea, capsys):
    wt, _, _ = tarea
    assert cli.main(["tareas"]) == 0
    respuesta = salida(capsys)
    assert respuesta["datos"]["tareas"][0]["rama"] == wt.branch
    assert respuesta["datos"]["worktrees"][0]["repo"] == str(wt.repo)


def test_descartar_confirma_y_marca_registro(env, tarea, monkeypatch, capsys):
    wt, pid, _ = tarea
    assert cli.main(["descartar", wt.branch]) == 3
    assert salida(capsys)["estado"] == "NECESITO_CONFIRMACION"
    tty(monkeypatch, "otra")
    assert cli.main(["descartar", wt.branch, "--confirm"]) == 1
    assert salida(capsys)["estado"] == "ERROR"
    assert wt.path.exists()
    tty(monkeypatch, wt.branch)
    assert cli.main(["descartar", wt.branch, "--confirm"]) == 0
    assert salida(capsys)["estado"] == "OK"
    assert tareas.obtener(pid, wt.branch, paths=env)["status"] == "descartado"
    assert not wt.path.exists()


def test_chat_merge_sigue_funcionando(env, tarea, tmp_path):
    wt, _, sha = tarea
    otro = tmp_path / "otro"
    git(tmp_path, "clone", "-q", str(wt.repo), str(otro))
    worktrees.create(env, repo=otro, agent=wt.agent, task="fix")
    store = chat.Store(env)
    pr = store.create_project("p", str(wt.repo))
    conv = store.create_conversation(pr["id"], "merge", "individual", ["orquestador"])
    store.update(conv["id"], lambda c: c["worktrees"].append({
        "branch": wt.branch, "repo": str(wt.repo), "status": "abierto",
        "review": {"veredicto": "APROBAR"}}))
    chat.merge_worktree(store, conv["id"], wt.branch)
    assert git(wt.repo, "rev-parse", "HEAD") == sha
    assert store.get_conversation(conv["id"])["worktrees"][0]["status"] == "mergeado"
    assert len(worktrees.list_worktrees(env)) == 1


@pytest.fixture
def revisor(tarea, monkeypatch):
    wt, _, _ = tarea
    monkeypatch.setattr(chat.mf, "load", lambda paths: {"agents": {
        wt.agent: {"backend": "codex", "mode": "write"},
        "gemini-reclutador": {"backend": "agy", "mode": "read-only"}}})
    monkeypatch.setattr(chat, "default_executor", lambda *args: chat.Reply("Veredicto: APROBAR"))


def test_review_registra_tarea_y_descubre_configurar_tests(env, tarea, revisor, monkeypatch, capsys):
    wt, pid, sha = tarea
    (env.state_dir / pid / "tareas.json").unlink()
    configurar_tests(wt.repo, "test -f b.txt")
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["veredicto"] == "APROBAR" and datos["destino"] == "main"
    assert datos["tests_cmd"] == "test -f b.txt" and datos["tests_ok"] is True
    assert datos["tests_sha"] == datos["sha_revisado"] == sha
    # La configuración sin versionar no bloquea el merge.
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 0
    assert salida(capsys)["estado"] == "OK"
    assert git(wt.repo, "rev-parse", "HEAD") == sha


@pytest.mark.parametrize("tests_cmd,campos,motivo", [
    ("exit 1", {}, "tests"),
    ("true", {"confianza_autor": 74}, "confianza"),
])
def test_review_bloqueadores_tests_y_confianza(env, tarea, revisor, capsys, tests_cmd, campos, motivo):
    wt, pid, _ = tarea
    configurar_tests(wt.repo, tests_cmd)
    if campos:
        tareas.actualizar(pid, wt.branch, campos, paths=env)
    assert cli.main(["review", wt.branch]) == 0
    assert salida(capsys)["datos"]["veredicto"] == "APROBAR"
    assert cli.main(["merge", wt.branch, "--confirm"]) == 3
    assert motivo in str(salida(capsys)["errores"])
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


def test_review_sin_tests_no_inventa_evidencia(env, tarea, revisor, capsys, monkeypatch):
    wt, pid, _ = tarea
    configurar_tests(wt.repo, None)
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["tests_sha"] is None and datos["tests_ok"] is None
    # Sin tests, merge debe funcionar con TTY correcta y veredicto APROBAR
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 0
    assert salida(capsys)["estado"] == "OK"


def test_review_con_config_de_tests_rota_falla_cerrado(env, tarea, revisor, capsys, monkeypatch):
    """Un proyecto.toml inválido no equivale a "sin tests": la review registra tests fallidos y el merge frena."""
    wt, pid, _ = tarea
    (wt.repo / ".orquesta" / "proyecto.toml").write_text('[worktree]\ntests = "python -m pytest\n')
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["tests_ok"] is False and datos["tests_sha"] is not None
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 3
    assert "FALLARON" in str(salida(capsys)["errores"])
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


@pytest.mark.parametrize("texto,error", [("sin veredicto", None), ("Veredicto: APROBAR", "falló el backend")])
def test_review_fallida_invalida_aprobacion_anterior(env, tarea, revisor, monkeypatch, capsys, texto, error):
    wt, pid, _ = tarea
    monkeypatch.setattr(chat, "default_executor", lambda *args: chat.Reply(texto, error=error))
    assert cli.main(["review", wt.branch]) == 1
    assert salida(capsys)["estado"] == "ERROR"
    assert tareas.obtener(pid, wt.branch, paths=env)["veredicto"] is None
    assert cli.main(["merge", wt.branch, "--confirm"]) == 3
    assert salida(capsys)["estado"] == "BLOQUEADO"


def test_review_tests_que_cambian_sha_no_habilitan_merge(env, tarea, revisor, capsys):
    wt, pid, sha = tarea
    configurar_tests(wt.repo, "git commit --allow-empty -qm tests")
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["sha_revisado"] == sha and datos["tests_sha"] != sha
    assert cli.main(["merge", wt.branch, "--confirm", "--forzar"]) == 3
    assert "review vieja" in str(salida(capsys))


def test_merge_revalida_sha_despues_de_confirmar(tarea, capsys):
    wt, _, _ = tarea

    def confirmar_y_mover(prompt):
        git(wt.path, "commit", "--allow-empty", "-qm", "nuevo")
        return wt.branch

    args = cli.build_parser().parse_args(["merge", wt.branch, "--confirm"])
    assert cli.cmd_merge(args, leer_tty=confirmar_y_mover) == 3
    assert "review vieja" in str(salida(capsys))
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


def test_tareas_desde_worktree_usa_el_proyecto_original(tarea, monkeypatch, capsys):
    wt, _, _ = tarea
    monkeypatch.chdir(wt.path)
    assert cli.main(["tareas", "--json"]) == 0
    assert salida(capsys)["datos"]["tareas"][0]["rama"] == wt.branch


def test_review_no_reusa_tests_cmd_viejo_del_registro(env, tarea, revisor, capsys):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, {"tests_cmd": "true"}, paths=env)
    configurar_tests(wt.repo, "exit 1")
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["tests_cmd"] == "exit 1" and datos["tests_ok"] is False


def test_review_sin_veredicto_no_deja_sha_revisado(env, tarea, revisor, monkeypatch, capsys):
    wt, pid, _ = tarea
    monkeypatch.setattr(chat, "default_executor", lambda *args: chat.Reply("no sé"))
    assert cli.main(["review", wt.branch]) == 1
    assert salida(capsys)["estado"] == "ERROR"
    assert tareas.obtener(pid, wt.branch, paths=env)["sha_revisado"] is None
    tty(monkeypatch, f"forzar {wt.branch}")
    assert cli.main(["merge", wt.branch, "--confirm", "--forzar"]) == 3
    assert "review vieja" in str(salida(capsys))


def test_merge_bloquea_si_la_tarea_cambia_mientras_se_confirma(env, tarea, capsys):
    wt, pid, _ = tarea

    def confirmar_y_rechazar(prompt):
        tareas.actualizar(pid, wt.branch, {"veredicto": "RECHAZAR"}, paths=env)
        return wt.branch

    args = cli.build_parser().parse_args(["merge", wt.branch, "--confirm"])
    assert cli.cmd_merge(args, leer_tty=confirmar_y_rechazar) == 3
    assert "cambió mientras confirmabas" in str(salida(capsys))
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


def test_merge_muestra_que_se_confirma(tarea, capsys):
    wt, _, sha = tarea
    vistos = []
    args = cli.build_parser().parse_args(["merge", wt.branch, "--confirm"])
    assert cli.cmd_merge(args, leer_tty=lambda prompt: vistos.append(prompt) or wt.branch) == 0
    assert f"Mergear {sha[:8]} de {wt.branch} en main" in vistos[0] and "FORZADO" not in vistos[0]


def test_merge_forzado_lo_dice_y_pide_otro_texto(env, tarea, capsys):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, {"veredicto": "RECHAZAR"}, paths=env)
    vistos = []
    args = cli.build_parser().parse_args(["merge", wt.branch, "--confirm", "--forzar"])
    assert cli.cmd_merge(args, leer_tty=lambda prompt: vistos.append(prompt) or wt.branch) == 1
    assert "FORZADO" in vistos[0] and "RECHAZAR" in vistos[0] and f"«forzar {wt.branch}»" in vistos[0]
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


def test_descartar_muestra_que_se_pierde(tarea, capsys):
    wt, _, _ = tarea
    vistos = []
    args = cli.build_parser().parse_args(["descartar", wt.branch, "--confirm"])
    assert cli.cmd_descartar(args, leer_tty=lambda prompt: vistos.append(prompt) or "no") == 1
    assert f"Descartar {wt.branch}" in vistos[0] and "se pierden los cambios" in vistos[0]
    assert wt.path.exists()


def test_tareas_json_malformado_es_error_sin_traceback(env, tarea, capsys):
    _, pid, _ = tarea
    (env.state_dir / pid / "tareas.json").write_text('[{"rama": 1}]')
    assert cli.main(["tareas"]) == 1
    assert salida(capsys)["estado"] == "ERROR"


def test_cerrar_si_vigente_no_actua_si_la_tarea_cambio(env, tarea):
    wt, pid, sha = tarea
    llamado = []
    assert tareas.cerrar_si_vigente(pid, wt.branch, str(wt.repo), {"veredicto": "RECHAZAR"},
                                    lambda: llamado.append(1), status="mergeado", paths=env) is None
    assert llamado == [] and tareas.obtener(pid, wt.branch, paths=env)["status"] == "abierto"
    assert tareas.cerrar_si_vigente(pid, wt.branch, str(wt.repo), {"veredicto": "APROBAR"},
                                    lambda: "ok", status="mergeado", paths=env) == "ok"
    assert tareas.obtener(pid, wt.branch, paths=env)["status"] == "mergeado"


def test_merge_sin_tests_avisa(env, tarea, monkeypatch, capsys):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, {"tests_ok": None, "tests_sha": None}, paths=env)
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 0
    assert "no tiene comando de tests" in salida(capsys)["avisos"][0]


def test_wt_clean_force_pide_confirmacion_humana(tarea, monkeypatch, capsys):
    wt, _, _ = tarea

    def sin_tty(**kwargs):
        raise confirmar.SinTTY(kwargs["comando"])

    monkeypatch.setattr(confirmar, "pedir_confirmacion", sin_tty)
    assert cli.main(["wt", "clean", wt.branch, "--force"]) == 3
    assert wt.path.exists()
    tty(monkeypatch, wt.branch)
    assert cli.main(["wt", "clean", wt.branch, "--force"]) == 0
    assert not wt.path.exists()
