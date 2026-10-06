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

    def preparar(contenido):
        (wt.path / "test_algo.py").write_bytes(contenido)
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
@pytest.mark.parametrize("error", [worktrees.WorktreeError("git falló"), OSError("permiso"),
                                   ValueError("encoding")])
def test_merge_continua_si_falla_diff(merge_diff, monkeypatch, via, error):
    wt, preparar, mergear, _, _, _ = merge_diff
    sha = preparar(b"def test_algo():\n    assert 1 == 1\n")
    original = worktrees._git

    def falla_diff(repo, *args, **kwargs):
        if args[0] == "diff":
            raise error
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(worktrees, "_git", falla_diff)
    assert mergear(via) == (0 if via == "cli" else None)
    assert git(wt.repo, "rev-parse", "HEAD") == sha


@pytest.mark.parametrize("via", ["cli", "chat"])
def test_merge_latin1_continua_sin_diff(merge_diff, monkeypatch, via):
    wt, preparar, mergear, _, _, _ = merge_diff
    sha = preparar(b"# revisi\xf3n\ndef test_algo():\n    pass\n")
    # Git realmente emite bytes Latin-1: no reemplazamos el error por un mock.
    with pytest.raises(UnicodeDecodeError):
        worktrees._git(wt.repo, "diff", f"{wt.base_sha}..{sha}")
    recibidos = []
    original = revision.razones_bloqueo

    def observar(registro):
        recibidos.append(dict(registro))
        return original(registro)

    monkeypatch.setattr(revision if via == "cli" else chat, "razones_bloqueo", observar)
    assert mergear(via) == (0 if via == "cli" else None)
    assert len(recibidos) == 1 and "diff" not in recibidos[0]
    assert original(recibidos[0]) == []
    assert git(wt.repo, "rev-parse", "HEAD") == sha


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
