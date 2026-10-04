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
