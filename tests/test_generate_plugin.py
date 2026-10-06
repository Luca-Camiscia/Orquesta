"""Deploy por fase: plugin completo, rollback y avisos de sesiones."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
from filelock import Timeout

from helpers import dump_yaml, load_yaml, snapshot
from orquesta import generate, manifest, migracion
from orquesta.claude_md import START, extract_inner

_REAL_LIVE_SESSIONS = generate._live_sessions  # el conftest la reemplaza por 0 en el resto de los tests


@pytest.fixture(autouse=True)
def sin_claude_real(monkeypatch):
    real_run = subprocess.run

    def run(command, **kwargs):
        if command == ["claude", "agents", "--json"]:
            raise FileNotFoundError("claude")
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)


def aplicar(env, run_tests=None):
    plan = generate.build_plan(env)
    assert not plan.errors and not plan.violations
    return generate.apply_plan(plan, "deploy", run_tests=run_tests)


def test_legacy_conserva_snapshot_y_agrega_plugin_con_generacion(env):
    aplicar(env)
    files = {name: content.decode() for name, content in snapshot(env.claude_dir).items()}
    deployed = json.loads(files["agents/orquesta/.orquesta-manifest.json"])
    assert files["agents/orquesta/.orquesta-manifest.json"] == manifest.dumps(deployed)
    # Snapshot capturado antes de E2.3; normalizar solo las rutas temporales y sus hashes.
    for name, entry in deployed["agents"].items():
        assert entry["sha256"] == hashlib.sha256(
            files[f"agents/orquesta/{name}.md"].encode()).hexdigest()
    assert deployed["claude_md_block_sha256"] == hashlib.sha256(
        extract_inner(files["CLAUDE.md"]).encode()).hexdigest()
    for name, text in files.items():
        if name.endswith(".md"):
            files[name] = text.replace(str(env.home), "/orquesta").replace(str(env.claude_dir), "/claude")
    for name, entry in deployed["agents"].items():
        entry["sha256"] = hashlib.sha256(files[f"agents/orquesta/{name}.md"].encode()).hexdigest()
    deployed["claude_md_block_sha256"] = hashlib.sha256(
        extract_inner(files["CLAUDE.md"]).encode()).hexdigest()
    files["agents/orquesta/.orquesta-manifest.json"] = manifest.dumps(deployed)
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert digest == "b3f059d60e82ffb25a3a27513ac2e4dacfa4b7b922834ff7a70126dd5702dbd9"
    plugin = json.loads(env.plugin_manifest.read_text())
    generation = plugin.pop("generacion")
    assert len(generation) == 8
    assert (env.plugin_dir / "context/orquestador.md").read_text().startswith(f"Generación: {generation}\n")
    assert (env.plugin_root / ".claude-plugin/marketplace.json").is_file()
    assert "`orquesta:codex-forja`" in (env.plugin_dir / "context/orquestador.md").read_text()
    for name, entry in plugin["agents"].items():
        assert entry["sha256"] == hashlib.sha256(
            (env.plugin_dir / "agents" / entry["file"]).read_bytes()).hexdigest()
    assert generate.build_plan(env).empty


def test_plugin_no_toca_claude_ni_marcadores_rotos(env):
    (env.claude_dir / "CLAUDE.md").write_text(f"{START}\nsin cierre\n")
    env.agents_dir.mkdir(parents=True)
    (env.agents_dir / "sobrante.md").write_text("conservar")
    migracion.set_fase(env, "plugin")
    before = snapshot(env.claude_dir)
    aplicar(env)
    assert snapshot(env.claude_dir) == before
    assert env.plugin_manifest.is_file()
    assert (env.plugin_dir / "agents/codex-forja.md").is_file()
    assert generate.build_plan(env).empty


def test_reemplazo_atomico_retira_archivos_sobrantes(env, monkeypatch):
    aplicar(env)
    stale = env.plugin_dir / "sobrante.bin"
    stale.write_bytes(b"\xff\x00")
    moves = []
    replace = os.replace

    def record(source, target):
        moves.append((source, target))
        return replace(source, target)

    monkeypatch.setattr(os, "replace", record)
    aplicar(env)
    assert not stale.exists()
    assert (env.plugin_root, env.home / f".plugin.old-{os.getpid()}") in moves
    assert (env.home / f".plugin.tmp-{os.getpid()}", env.plugin_root) in moves
    assert not list(env.home.glob(".plugin.*"))


@pytest.mark.parametrize("phase", ["legacy", "plugin"])
def test_fallo_entre_renames_restaura_todo(env, monkeypatch, phase):
    aplicar(env)
    migracion.set_fase(env, phase)
    data = load_yaml(env.registry)
    data["orchestrator"]["default_mood"] = "debate"
    dump_yaml(data, env.registry)
    before = snapshot(env.claude_dir), snapshot(env.plugin_root)
    replace = os.replace

    def fail(source, target):
        if source == env.home / f".plugin.tmp-{os.getpid()}" and target == env.plugin_root:
            assert not env.plugin_root.exists()
            assert (env.home / f".plugin.old-{os.getpid()}").is_dir()
            raise OSError("fallo de rename simulado")
        return replace(source, target)

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="rename simulado"):
        aplicar(env)
    assert (snapshot(env.claude_dir), snapshot(env.plugin_root)) == before
    assert not list(env.home.glob(".plugin.*"))


@pytest.mark.parametrize("phase", ["legacy", "plugin"])
@pytest.mark.parametrize("existing", [False, True])
def test_fallo_restaura_plugin_y_legacy(env, phase, existing):
    if existing:
        aplicar(env)
        (env.plugin_dir / "extra.txt").write_text("restaurar")
    migracion.set_fase(env, phase)
    before = snapshot(env.claude_dir), snapshot(env.plugin_root)
    data = load_yaml(env.registry)
    data["orchestrator"]["default_mood"] = "debate"
    dump_yaml(data, env.registry)
    with pytest.raises(generate.TestsFailed):
        aplicar(env, run_tests=lambda paths: (False, "fallo simulado"))
    assert (snapshot(env.claude_dir), snapshot(env.plugin_root)) == before
    assert env.plugin_root.exists() == existing
    assert not list(env.home.glob(".plugin.*"))


def test_dry_run_no_escribe_y_muestra_rutas_plugin(env):
    before = snapshot(env.claude_dir), snapshot(env.home)
    plan = generate.build_plan(env)
    assert not plan.errors
    assert env.pretty(env.plugin_dir / "context/orquestador.md") in plan.diff()
    assert any("plugin" in line for line in plan.summary)
    assert (snapshot(env.claude_dir), snapshot(env.home)) == before


def test_diff_agrupa_muchos_archivos_plugin(env):
    migracion.set_fase(env, "plugin")
    data = load_yaml(env.registry)
    new_agents = [f"lector-{n}" for n in range(30)]
    data["agents"].extend({**data["agents"][0], "name": name} for name in new_agents)
    dump_yaml(data, env.registry)
    plan = generate.build_plan(env, confirmations=new_agents)
    assert not plan.errors and not plan.violations
    diff = plan.diff()
    assert env.pretty(env.plugin_root) in diff
    assert "45 archivos" in diff and "45 creados" in diff
    assert len(diff.splitlines()) == 1


@pytest.mark.parametrize("phase", ["legacy", "plugin"])
def test_secreto_en_prompt_bloquea_plugin(env, phase):
    migracion.set_fase(env, phase)
    data = load_yaml(env.registry)
    # Credencial sintética armada en memoria, nunca escrita al registro.
    data["agents"][1]["prompt"] = "sk-proj-" + "x" * 40
    from orquesta.config import load_backends
    from orquesta.schema import parse_registry
    # load_all es la frontera de lectura; mantener el render y el escaneo reales.
    from unittest.mock import patch
    with patch.object(generate, "load_all", return_value=(parse_registry(data), load_backends(env))):
        plan = generate.build_plan(env)
    assert any("secreto" in error and str(env.plugin_dir) in error for error in plan.errors)
    with pytest.raises(generate.GenerateError, match="secreto"):
        generate.apply_plan(plan, "bloqueado", run_tests=None)
    assert not env.plugin_root.exists()


@pytest.mark.parametrize("response", ['{"sessions": [{"id": "a"}, {"id": "b"}]}',
                                      '[{"id": "a"}, {"id": "b"}]'])
def test_sesiones_abiertas_en_resumen_solo_post_apply(env, monkeypatch, response):
    monkeypatch.setattr(generate, "_live_sessions", _REAL_LIVE_SESSIONS)
    real_run = subprocess.run

    def run(command, **kwargs):
        if command == ["claude", "agents", "--json"]:
            # Consulta directa: el estado propio de Claude Code no es un archivo generado por orquesta.
            assert Path(kwargs["env"]["CLAUDE_CONFIG_DIR"]) == env.claude_dir
            return subprocess.CompletedProcess(command, 0, response, "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    plan = generate.build_plan(env)
    assert not any("sesiones abiertas" in line for line in plan.summary)
    result = generate.apply_plan(plan, "deploy", run_tests=None)
    assert "Hay 2 sesiones abiertas de Claude Code: reinicialas o usá /reload-plugins para cargar los cambios" in result.summary
    assert not any("sesiones abiertas" in line for line in plan.summary)
    assert "sesiones abiertas" not in env.changelog.read_text()


@pytest.mark.parametrize("response", ["[]", "{}", "no JSON", "timeout", "fallo"])
def test_sesiones_ausentes_o_fallo_no_afectan_apply(env, monkeypatch, response):
    monkeypatch.setattr(generate, "_live_sessions", _REAL_LIVE_SESSIONS)
    real_run = subprocess.run

    def run(command, **kwargs):
        if command == ["claude", "agents", "--json"]:
            if response == "timeout":
                raise subprocess.TimeoutExpired(command, 2)
            return subprocess.CompletedProcess(command, int(response == "fallo"), response, "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    result = aplicar(env)
    assert result.applied
    assert not any("sesiones abiertas" in line for line in result.summary)


def test_cambio_de_fase_invalida_plan(env):
    plan = generate.build_plan(env)
    migracion.set_fase(env, "plugin")
    with pytest.raises(generate.GenerateError, match="fase"):
        generate.apply_plan(plan, "obsoleto", run_tests=None)
    assert snapshot(env.claude_dir) == {}
    assert not env.plugin_root.exists()


def test_plugin_modificado_desde_plan_no_se_pisa(env):
    aplicar(env)
    stale = env.plugin_dir / "extra.md"
    stale.write_text("antes")
    plan = generate.build_plan(env)
    # Archivo que no era un cambio del plan, pero se perdería al reemplazar todo el árbol.
    target = env.plugin_dir / "commands/plan.md"
    target.write_text("editado después")
    with pytest.raises(generate.GenerateError, match="cambió"):
        generate.apply_plan(plan, "obsoleto", run_tests=None)
    assert target.read_text() == "editado después"
    assert stale.exists()


def test_apply_mantiene_lock_compartido_durante_escritura_y_tests(env):
    def tests(paths):
        with pytest.raises(Timeout):
            with generate.get_lock(paths).acquire(timeout=0):
                pytest.fail("el lock del deploy quedó libre")
        return True, "lock verificado"

    result = aplicar(env, run_tests=tests)
    assert result.tests_output == "lock verificado"
    # Después de apply queda disponible para migración u otro generate.
    with generate.get_lock(env).acquire(timeout=0):
        assert env.plugin_manifest.is_file()


def test_recupera_el_plugin_si_una_corrida_se_corto_entre_renames(env):
    aplicar_legacy = generate.apply_plan(generate.build_plan(env), "deploy", run_tests=None)
    assert aplicar_legacy.applied
    viejo = env.plugin_root.with_name(".plugin.old-999")
    os.replace(env.plugin_root, viejo)
    env.plugin_root.with_name(".plugin.tmp-999").mkdir()
    editar = load_yaml(env.registry)
    editar["agents"][0]["route_when"] = ["algo nuevo"]
    dump_yaml(editar, env.registry)
    plan = generate.build_plan(env)
    assert generate.apply_plan(plan, "otra", run_tests=None).applied
    assert (env.plugin_root / "orquesta" / "context" / "orquestador.md").is_file()
    assert not list(env.plugin_root.parent.glob(".plugin.old-*"))
    assert not list(env.plugin_root.parent.glob(".plugin.tmp-*"))


def test_plugin_root_symlink_se_rechaza(env, tmp_path):
    destino = tmp_path / "otro"
    destino.mkdir()
    env.plugin_root.symlink_to(destino)
    with pytest.raises(generate.GenerateError, match="enlace simbólico"):
        generate.apply_plan(generate.build_plan(env), "deploy", run_tests=None)


def test_generacion_cambia_con_el_contenido_del_plugin(env, monkeypatch):
    from orquesta import plugin_render
    antes = generate.build_plan(env)
    monkeypatch.setattr(plugin_render, "_RULES", plugin_render._RULES + "\n- Regla nueva.\n")
    despues = generate.build_plan(env)
    gen = lambda plan: json.loads(plan.plugin_files[env.plugin_manifest])["generacion"]
    assert gen(antes) != gen(despues)


def test_varios_respaldos_sin_plugin_no_adivina(env):
    generate.apply_plan(generate.build_plan(env), "deploy", run_tests=None)
    os.replace(env.plugin_root, env.plugin_root.with_name(".plugin.old-1"))
    env.plugin_root.with_name(".plugin.old-2").mkdir()
    editar = load_yaml(env.registry)
    editar["agents"][0]["route_when"] = ["otra cosa"]
    dump_yaml(editar, env.registry)
    with pytest.raises(generate.GenerateError, match="varios respaldos"):
        generate.apply_plan(generate.build_plan(env), "x", run_tests=None)
