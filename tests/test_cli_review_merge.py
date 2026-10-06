"""CLI con git real, executor simulado y confirmación TTY inyectada."""
import json
import shlex
from unittest.mock import Mock

import pytest

from helpers import git
from orquesta import chat, cli, confirmar, tareas, worktrees


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
    pid = worktrees.project_id(repo)
    tareas.registrar(pid, {"repo": str(repo), "rama": wt.branch, "agente": wt.agent,
                          "destino": "main", "base_sha": wt.base_sha, "tests_cmd": "true",
                          "sha_revisado": sha, "tests_sha": sha, "tests_ok": True,
                          "veredicto": "APROBAR", "confianza_autor": 90}, paths=env)
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
    tty(monkeypatch, wt.branch)
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
        (wt.repo / "sin-commit.txt").write_text("sucio")
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


def test_review_registra_tarea_y_descubre_tests_del_proyecto(env, tarea, revisor, monkeypatch, capsys):
    wt, pid, sha = tarea
    (env.state_dir / pid / "tareas.json").unlink()
    config = wt.repo / ".orquesta" / "proyecto.toml"
    config.parent.mkdir()
    config.write_text('[worktree]\ntests = "test -f b.txt"\n')
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["veredicto"] == "APROBAR" and datos["destino"] == "main"
    assert datos["tests_cmd"] == "test -f b.txt" and datos["tests_ok"] is True
    assert datos["tests_sha"] == datos["sha_revisado"] == sha
    # La configuración local queda fuera del merge; al limpiarla, la review habilita el SHA.
    config.unlink()
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 0
    assert salida(capsys)["estado"] == "OK"
    assert git(wt.repo, "rev-parse", "HEAD") == sha


@pytest.mark.parametrize("campos,motivo", [
    ({"tests_cmd": "exit 1"}, "tests"),
    ({"confianza_autor": 0.74}, "confianza"),
])
def test_review_bloqueadores_tests_y_confianza(env, tarea, revisor, capsys, campos, motivo):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, campos, paths=env)
    assert cli.main(["review", wt.branch]) == 0
    assert salida(capsys)["datos"]["veredicto"] == "APROBAR"
    assert cli.main(["merge", wt.branch, "--confirm"]) == 3
    assert motivo in str(salida(capsys)["errores"])
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


def test_review_sin_tests_no_inventa_evidencia(env, tarea, revisor, capsys, monkeypatch):
    wt, pid, _ = tarea
    tareas.actualizar(pid, wt.branch, {"tests_cmd": None}, paths=env)
    assert cli.main(["review", wt.branch]) == 0
    datos = salida(capsys)["datos"]
    assert datos["tests_sha"] is None and datos["tests_ok"] is None
    # Sin tests, merge debe funcionar con TTY correcta y veredicto APROBAR
    tty(monkeypatch, wt.branch)
    assert cli.main(["merge", wt.branch, "--confirm"]) == 0
    assert salida(capsys)["estado"] == "OK"


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
    tareas.actualizar(pid, wt.branch, {"tests_cmd": "git commit --allow-empty -qm tests"}, paths=env)
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
