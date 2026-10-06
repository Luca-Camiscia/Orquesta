"""Fixtures comunes. Ningún test toca el ~/.claude real ni el repo real de orquesta."""
from __future__ import annotations

import pytest

from helpers import backends_dict, dump_yaml, git, registry_dict
from orquesta.paths import Paths


@pytest.fixture(autouse=True)
def _entorno_aislado(monkeypatch, tmp_path_factory):
    """Si algún código usara default_paths() en un test, cae en un directorio temporal."""
    guard = tmp_path_factory.mktemp("guard")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(guard / "claude"))
    monkeypatch.setenv("ORQUESTA_HOME", str(guard / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(guard / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for var in ("GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.setenv(var, "Test")
    for var in ("GIT_AUTHOR_EMAIL", "GIT_COMMITTER_EMAIL"):
        monkeypatch.setenv(var, "test@example.com")


@pytest.fixture
def env(tmp_path) -> Paths:
    """Home de orquesta (repo git con registro y backends de prueba) + config de Claude Code."""
    home, claude = tmp_path / "orquesta", tmp_path / "claude"
    home.mkdir()
    claude.mkdir()
    dump_yaml(registry_dict(), home / "orquesta.yaml")
    dump_yaml(backends_dict(), home / "backends.yaml")
    (home / "manual").mkdir()
    (home / ".gitignore").write_text("state/\nbackups/\nworktrees/\nplugin/\n", encoding="utf-8")
    git(home, "init", "-q", "-b", "main")
    git(home, "add", "-A")
    git(home, "commit", "-qm", "init")
    return Paths(home=home, claude_dir=claude)
