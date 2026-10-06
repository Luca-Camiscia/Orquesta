"""Fase de deploy persistida por configuración de Claude Code."""
import hashlib
import json

import pytest

from orquesta import migracion
from orquesta.paths import Paths, default_paths


def test_fase_default_es_legacy(env):
    assert not (env.state_dir / "migracion").exists()
    assert migracion.fase(env) == "legacy"


@pytest.mark.parametrize("valor", ["unknown", "", None])
def test_set_fase_valida(env, valor):
    with pytest.raises(ValueError):
        migracion.set_fase(env, valor)
    assert not (env.state_dir / "migracion").exists()


def test_set_y_get_fase(env):
    migracion.set_fase(env, "plugin")
    assert migracion.fase(env) == "plugin"
    migracion.set_fase(env, "legacy")
    assert migracion.fase(env) == "legacy"


def test_fases_independientes_por_claude_dir(env, tmp_path):
    env2 = Paths(home=env.home, claude_dir=tmp_path / "otra")
    env2.claude_dir.mkdir()
    migracion.set_fase(env, "plugin")
    migracion.set_fase(env2, "legacy")
    assert migracion.fase(env) == "plugin"
    assert migracion.fase(env2) == "legacy"


def test_fase_symlink_comparte_estado_y_persiste_metadatos(env, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(env.claude_dir, target_is_directory=True)
    env2 = Paths(home=env.home, claude_dir=alias)
    migracion.set_fase(env2, "plugin")
    assert migracion.fase(env) == "plugin"
    sha = hashlib.sha256(str(env.claude_dir.resolve()).encode()).hexdigest()[:12]
    folder = env.state_dir / "migracion"
    data = json.loads((folder / f"{sha}.json").read_text())
    assert data["fase"] == "plugin"
    assert data["claude_dir"] == str(env.claude_dir.resolve())
    assert isinstance(data["ts"], float)
    assert not list(folder.glob("*.tmp"))


def test_paths_plugin_respetan_entorno(env, monkeypatch):
    monkeypatch.setenv("ORQUESTA_HOME", str(env.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env.claude_dir))
    paths = default_paths()
    assert paths.plugin_root == env.home / "plugin"
    assert paths.plugin_dir == env.home / "plugin" / "orquesta"
    assert paths.plugin_manifest == env.home / "plugin" / ".orquesta-manifest.json"
    assert paths.legacy_manifest == paths.manifest == env.agents_dir / ".orquesta-manifest.json"
