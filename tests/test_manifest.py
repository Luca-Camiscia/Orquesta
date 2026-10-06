"""Resolución del manifiesto de la fase actual, sin fallback."""
import pytest

from orquesta import manifest, migracion


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_load_usa_solo_manifiesto_de_la_fase(env, fase):
    env.legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    env.plugin_manifest.parent.mkdir(parents=True, exist_ok=True)
    env.legacy_manifest.write_text('{"agents": {}, "fase": "legacy"}', encoding="utf-8")
    env.plugin_manifest.write_text('{"agents": {}, "fase": "plugin"}', encoding="utf-8")
    migracion.set_fase(env, fase)
    assert manifest.load(env) == {"agents": {}, "fase": fase}


def test_load_plugin_ausente_no_hace_fallback(env):
    env.legacy_manifest.parent.mkdir(parents=True, exist_ok=True)
    env.legacy_manifest.write_text('{"agents": {}}', encoding="utf-8")
    migracion.set_fase(env, "plugin")
    with pytest.raises(manifest.ManifestError, match="plugin"):
        manifest.load(env)


def test_load_legacy_ausente_devuelve_none(env):
    env.plugin_manifest.parent.mkdir(parents=True, exist_ok=True)
    env.plugin_manifest.write_text('{"agents": {}}', encoding="utf-8")
    assert manifest.load(env) is None


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
@pytest.mark.parametrize("contenido", ["{ invalid json", "null", "[]"])
def test_load_corrupto_levanta_manifesterror(env, fase, contenido):
    path = env.legacy_manifest if fase == "legacy" else env.plugin_manifest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contenido, encoding="utf-8")
    migracion.set_fase(env, fase)
    with pytest.raises(manifest.ManifestError, match=fase):
        manifest.load(env)


@pytest.mark.parametrize("contenido", ["no es json", '{"fase": "plugins"}', "[]"])
def test_archivo_de_fase_corrupto_es_manifest_error(env, contenido):
    migracion.set_fase(env, "legacy")
    archivo = next((env.state_dir / "migracion").glob("*.json"))
    archivo.write_text(contenido, encoding="utf-8")
    with pytest.raises(manifest.ManifestError, match="fase de deploy"):
        manifest.load(env)
