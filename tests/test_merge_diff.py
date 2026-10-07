"""Guardia de merge en CLI y chat, con repos temporales y diff completo."""
import json

import pytest

from helpers import git
from orquesta import chat, cli, revision, tareas, worktrees


@pytest.fixture
def merge_diff(env, tmp_path, monkeypatch):
    repo = tmp_path / "proyecto"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "test_algo.py").write_text("def test_algo():\n    assert True\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "base con test")
    wt = worktrees.create(env, repo=repo, agent="codex-forjador", task="guardia")
    pid = worktrees.project_id(repo)
    store = chat.Store(env)
    pr = store.create_project("P", str(repo))
    conv = store.create_conversation(pr["id"], "merge", "individual", ["orquestador"])
    # El chat real tampoco guarda repo ni base_sha en la conversación.
    store.update(conv["id"], lambda c: c["worktrees"].append({
        "branch": wt.branch, "status": "abierto", "review": {"veredicto": "APROBAR"}}))
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.chdir(repo)

    def preparar(contenido, extra=None):
        if contenido is not None:
            (wt.path / "test_algo.py").write_bytes(contenido)
        if extra is not None:
            extra(wt.path)
        worktrees.commit_all(wt, "cambio de test")
        sha = git(wt.path, "rev-parse", "HEAD")
        tareas.registrar(pid, {"repo": str(repo), "rama": wt.branch, "agente": wt.agent,
                              "destino": "main", "base_sha": wt.base_sha, "tests_ok": True,
                              "tests_sha": sha, "sha_revisado": sha, "veredicto": "APROBAR",
                              "confianza_autor": 90}, paths=env)
        return sha

    def mergear(via, forzar=False, leer_tty=None):
        if via == "chat":
            chat.merge_worktree(store, conv["id"], wt.branch, force=forzar)
            return None
        opciones = ["merge", wt.branch, "--confirm"] + (["--forzar"] if forzar else [])
        args = cli.build_parser().parse_args(opciones)
        return cli.cmd_merge(args, leer_tty=leer_tty or (lambda prompt: wt.branch))

    return wt, preparar, mergear, store, conv["id"], pid


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_bloquea_test_debilitado(merge_diff, via, capsys):
    wt, preparar, mergear, store, cid, _ = merge_diff
    preparar(b"def test_algo():\n    pass\n")
    if via == "cli":
        assert mergear(via) == 3
        respuesta = json.loads(capsys.readouterr().out)
        assert respuesta["estado"] == "BLOQUEADO" and "debilita los tests" in str(respuesta)
    else:
        with pytest.raises(chat.NeedsForce, match="debilita los tests"):
            mergear(via)
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha
    assert store.get_conversation(cid)["worktrees"][0]["status"] == "abierto"


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_permite_assert_reemplazado(merge_diff, via):
    wt, preparar, mergear, store, cid, _ = merge_diff
    sha = preparar(b"def test_algo():\n    assert 1 == 1\n")
    assert mergear(via) == (0 if via == "cli" else None)
    assert git(wt.repo, "rev-parse", "HEAD") == sha
    if via == "chat":
        assert store.get_conversation(cid)["worktrees"][0]["status"] == "mergeado"


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_no_trunca_el_diff(merge_diff, via, capsys):
    wt, preparar, mergear, _, _, _ = merge_diff
    # El borrado aparece después del límite usado únicamente para prompts de review.
    (wt.path / "a_largo.txt").write_text("contenido largo\n" * chat.REVIEW_DIFF_CHARS)
    preparar(b"def test_algo():\n    pass\n")
    if via == "cli":
        assert mergear(via) == 3
        assert "debilita los tests" in capsys.readouterr().out
    else:
        with pytest.raises(chat.NeedsForce, match="debilita los tests"):
            mergear(via)
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_forzado_permite_test_debilitado(merge_diff, via):
    wt, preparar, mergear, _, _, _ = merge_diff
    sha = preparar(b"def test_algo():\n    pass\n")
    assert mergear(via, forzar=True, leer_tty=lambda prompt: f"forzar {wt.branch}") == (
        0 if via == "cli" else None)
    assert git(wt.repo, "rev-parse", "HEAD") == sha


def test_cli_tareas_no_calcula_diff(merge_diff, monkeypatch, capsys):
    _, preparar, _, _, _, _ = merge_diff
    preparar(b"def test_algo():\n    pass\n")
    original = worktrees._git

    def sin_diff(repo, *args, **kwargs):
        assert args[0] != "diff", "el listado no debe calcular diffs"
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(worktrees, "_git", sin_diff)
    assert cli.main(["tareas", "--linea"]) == 0
    assert "1 tareas abiertas" in capsys.readouterr().out


def _bloquea(mergear, via, capsys, texto):
    if via == "cli":
        assert mergear(via) == 3
        assert texto in capsys.readouterr().out
    else:
        with pytest.raises(chat.NeedsForce, match=texto):
            mergear(via)


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_latin1_en_otro_archivo_no_apaga_la_guardia(merge_diff, via, capsys):
    wt, preparar, mergear, _, _, _ = merge_diff
    # Un byte Latin-1 en un archivo cualquiera, junto con un test debilitado, no puede esconder el borrado.
    sha = preparar(b"def test_algo():\n    pass\n",
                   extra=lambda p: (p / "otro.py").write_bytes(b'x = "revisi\xf3n"\n'))
    with pytest.raises(UnicodeDecodeError):
        worktrees._git(wt.repo, "diff", f"{wt.base_sha}..{sha}")  # git emite bytes crudos de verdad
    _bloquea(mergear, via, capsys, "debilita los tests")
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_renombrar_el_archivo_de_tests_se_bloquea(merge_diff, via, capsys):
    wt, preparar, mergear, _, _, _ = merge_diff
    # pytest deja de recolectar `algo_viejo.py`; sin --no-renames el diff sale sin hunks.
    preparar(None, extra=lambda p: git(p, "mv", "test_algo.py", "algo_viejo.py"))
    _bloquea(mergear, via, capsys, "debilita los tests")
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


@pytest.mark.parametrize("via", ["cli", "chat"])
@pytest.mark.parametrize("error", [worktrees.WorktreeError("git falló"), OSError("permiso")])
def test_merge_se_bloquea_si_falla_el_diff(merge_diff, monkeypatch, via, error, capsys):
    wt, preparar, mergear, _, _, _ = merge_diff
    preparar(b"def test_algo():\n    assert 1 == 1\n")

    def falla(*args, **kwargs):
        raise error

    monkeypatch.setattr(worktrees, "diff_completo", falla)
    _bloquea(mergear, via, capsys, "no pude calcular el diff")
    assert git(wt.repo, "rev-parse", "HEAD") == wt.base_sha


@pytest.mark.parametrize("trivial", ["assert True", "assert 1", "assert(True)"])
def test_assert_trivial_no_cuenta_como_reemplazo(trivial):
    diff = ("--- a/test_algo.py\n+++ b/test_algo.py\n@@ -1,2 +1,2 @@\n def test_algo():\n"
            "-    assert f() == 1\n+    " + trivial + "\n")
    assert revision.tests_debilitados(diff) == ["test_algo.py: assert eliminado"]


@pytest.mark.parametrize("linea", ["addopts = -k 'not test_algo'", "collect_ignore = ['test_algo.py']",
                                   "def pytest_collection_modifyitems(items): items.clear()"])
def test_config_de_pytest_que_desactiva_tests_se_marca(linea):
    diff = "--- a/conftest.py\n+++ b/conftest.py\n@@ -1,1 +1,2 @@\n x = 1\n+" + linea + "\n"
    assert revision.tests_debilitados(diff) == ["conftest.py: configuración de pytest cambiada"]
