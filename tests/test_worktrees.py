"""Worktrees de agentes write: rama orquesta/<agente>/<tarea>, registro y huérfanos."""
from __future__ import annotations

import shutil

import pytest

from helpers import git
from orquesta import worktrees


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proyecto con espacios"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("hola\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    return r


@pytest.mark.parametrize("entrada,esperado", [
    ("Fix promedio!", "fix-promedio"), ("  ÑANDÚ de   prueba ", "nandu-de-prueba"),
    ("", "tarea"), ("a" * 80, "a" * 40), ("--x--", "x")])
def test_slugify(entrada, esperado):
    assert worktrees.slugify(entrada) == esperado


def test_crea_el_worktree_en_su_rama_sin_tocar_el_repo(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="Fix promedio")
    assert wt.branch == "orquesta/codex-forja/fix-promedio"
    assert (wt.path / "a.txt").read_text() == "hola\n"
    assert wt.path.is_relative_to(env.worktrees_dir)
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "orquesta/codex-forja/fix-promedio" in git(repo, "branch", "--list", "orquesta/*")


def test_colision_de_rama_agrega_sufijo(env, repo):
    a = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    b = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    assert b.branch == "orquesta/codex-forja/fix-2"
    assert a.path != b.path


def test_commit_y_diffstat(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    (wt.path / "b.txt").write_text("nuevo\n")
    assert worktrees.commit_all(wt, "orquesta(codex-forja): fix")
    assert "b.txt" in worktrees.diffstat(wt)
    assert git(repo, "status", "--porcelain") == ""


def test_commit_sin_cambios_devuelve_none(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    assert worktrees.commit_all(wt, "nada") is None


def test_queda_registrado(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    assert [w.branch for w in worktrees.list_worktrees(env)] == [wt.branch]


def test_recien_creado_no_es_huerfano(env, repo):
    worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    assert worktrees.orphans(env) == []


def test_huerfano_si_el_directorio_no_existe(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    shutil.rmtree(wt.path)
    assert any(o.branch == wt.branch and "no existe" in o.reason for o in worktrees.orphans(env))


def test_huerfano_si_ya_se_mergeo(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    (wt.path / "b.txt").write_text("nuevo\n")
    worktrees.commit_all(wt, "fix")
    git(repo, "merge", "-q", "--no-edit", wt.branch)
    assert any("mergeada" in o.reason for o in worktrees.orphans(env))


def test_clean_borra_worktree_y_rama_mergeada(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    (wt.path / "b.txt").write_text("nuevo\n")
    worktrees.commit_all(wt, "fix")
    git(repo, "merge", "-q", "--no-edit", wt.branch)
    worktrees.clean(env, wt.branch)
    assert not wt.path.exists()
    assert git(repo, "branch", "--list", "orquesta/*") == ""
    assert worktrees.list_worktrees(env) == []


def test_clean_de_rama_sin_mergear_requiere_force(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    (wt.path / "b.txt").write_text("nuevo\n")
    worktrees.commit_all(wt, "fix")
    with pytest.raises(worktrees.WorktreeError, match="force"):
        worktrees.clean(env, wt.branch)
    worktrees.clean(env, wt.branch, force=True)
    assert not wt.path.exists()


def test_find_rama_en_varios_repos_requiere_repo(env, repo, tmp_path):
    otro = tmp_path / "otro"
    git(tmp_path, "clone", "-q", str(repo), str(otro))
    a = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    b = worktrees.create(env, repo=otro, agent="codex-forja", task="fix")
    assert a.branch == b.branch
    with pytest.raises(worktrees.WorktreeError, match="varios repos registrados"):
        worktrees._find(env, a.branch)
    assert worktrees._find(env, a.branch, repo=repo / ".") == a
    worktrees.discard(env, a.branch, repo=repo)
    assert worktrees.list_worktrees(env) == [b]


def test_merge_sha_mergea_el_commit_revisado(env, repo, monkeypatch):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    (wt.path / "b.txt").write_text("nuevo\n")
    worktrees.commit_all(wt, "fix")
    sha = git(wt.path, "rev-parse", "HEAD")
    comandos = []
    original = worktrees._git

    def registrar(cwd, *args, **kwargs):
        comandos.append(args)
        return original(cwd, *args, **kwargs)

    monkeypatch.setattr(worktrees, "_git", registrar)
    result = worktrees.merge_sha(env, wt.branch, sha, "main", repo=repo)
    assert result == {"status": "ok", "sha_merged": sha, "destino": "main"}
    assert git(repo, "rev-parse", "HEAD") == sha
    assert (repo / "b.txt").read_text() == "nuevo\n"
    assert ("merge", "--no-edit", "--ff-only", sha) in comandos
    assert ("merge", "--no-edit", wt.branch) not in comandos


@pytest.mark.parametrize("problema,mensaje", [
    ("rama", "destino"), ("sucio", "limpio"), ("sha", "review vieja"),
    ("base", "ancestro"), ("worktree_sucio", "limpio")])
def test_merge_sha_barreras(env, repo, problema, mensaje):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="fix")
    sha = wt.base_sha
    if problema == "rama":
        git(repo, "checkout", "-qb", "otra")
    elif problema == "sucio":
        (repo / "a.txt").write_text("cambio sin commit\n")
    elif problema == "worktree_sucio":
        (wt.path / "a.txt").write_text("cambio sin commit\n")
    elif problema == "sha":
        (wt.path / "b.txt").write_text("nuevo")
        worktrees.commit_all(wt, "fix")
    elif problema == "base":
        git(repo, "checkout", "--orphan", "nueva")
        git(repo, "commit", "-qm", "otra historia")
        git(repo, "branch", "-M", "main")
    with pytest.raises(worktrees.WorktreeError, match=mensaje):
        worktrees.merge_sha(env, wt.branch, sha, "main", repo=repo)
    assert git(wt.path, "rev-parse", "HEAD") == worktrees._branch_tip(repo, wt.branch)
