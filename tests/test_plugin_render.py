"""Contrato del plugin local: render puro, namespace y validación con Claude Code."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess

import pytest

from helpers import backends_dict, registry_dict, snapshot
from orquesta import plugin_render
from orquesta.render import RenderContext, render_agent_md, render_block_inner
from orquesta.schema import ConfigError, parse_backends, parse_registry


@pytest.fixture
def reg():
    return parse_registry(registry_dict())


@pytest.fixture
def ctx(env):
    return {"paths": env, "generacion": "abc12345"}


def test_render_determinista_sin_escribir(reg, ctx):
    before = snapshot(ctx["paths"].home), snapshot(ctx["paths"].claude_dir)
    first = plugin_render.render_plugin(reg, backends_dict(), ctx)
    assert first == plugin_render.render_plugin(reg, backends_dict(), ctx)
    assert before == (snapshot(ctx["paths"].home), snapshot(ctx["paths"].claude_dir))


def test_estructura_completa(reg, ctx):
    files = plugin_render.render_plugin(reg, backends_dict(), ctx)
    assert set(files) == {
        ".claude-plugin/marketplace.json", "orquesta/.claude-plugin/plugin.json",
        "orquesta/agents/gemini-lector.md", "orquesta/agents/codex-forja.md",
        "orquesta/agents/codex-critico.md", "orquesta/agents/mantenedor.md",
        "orquesta/commands/plan.md", "orquesta/commands/reunion.md",
        "orquesta/commands/rrhh.md", "orquesta/commands/tareas.md",
        "orquesta/commands/mood.md", "orquesta/skills/orquesta-cli/SKILL.md",
        "orquesta/hooks/hooks.json", "orquesta/context/orquestador.md",
    }
    assert all(isinstance(content, str) for content in files.values())


def test_manifiestos_json_validos(ctx):
    files = plugin_render.render_plugin(parse_registry(registry_dict()), backends_dict(), ctx)
    marketplace = json.loads(files[".claude-plugin/marketplace.json"])
    assert marketplace["name"] == "orquesta-local"
    assert marketplace["plugins"] == [{"name": "orquesta", "source": "./orquesta"}]
    assert marketplace["owner"]["name"]
    manifest = json.loads(files["orquesta/.claude-plugin/plugin.json"])
    assert manifest["name"] == "orquesta"
    assert manifest["version"] == plugin_render.PLUGIN_VERSION
    assert manifest["description"]


@pytest.mark.parametrize("venv", [False, True])
def test_hooks_formato_real_y_ruta_absoluta_o_path(reg, ctx, venv):
    binary = ctx["paths"].home / ".venv/bin/orquesta"
    if venv:
        binary.parent.mkdir(parents=True)
        binary.write_text("", encoding="utf-8")
    files = plugin_render.render_plugin(reg, backends_dict(), ctx)
    hooks = json.loads(files["orquesta/hooks/hooks.json"])["hooks"]
    assert set(hooks) == {"SessionStart", "PreToolUse"}
    assert hooks["SessionStart"][0]["matcher"] == "startup|resume|clear|compact"
    assert hooks["PreToolUse"][0]["matcher"] == "Bash"
    for event, suffix in [("SessionStart", "session-start"), ("PreToolUse", "pre-bash")]:
        hook = hooks[event][0]["hooks"][0]
        assert hook["type"] == "command"
        assert shlex.split(hook["command"]) == [str(binary) if venv else "orquesta", "hook", suffix]


def test_hook_cita_ruta_con_espacios(reg, env):
    paths = type(env)(home=env.home / "con espacios", claude_dir=env.claude_dir)
    binary = paths.home / ".venv/bin/orquesta"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    files = plugin_render.render_plugin(reg, backends_dict(), {"paths": paths, "generacion": "abc12345"})
    hook = json.loads(files["orquesta/hooks/hooks.json"])["hooks"]["SessionStart"][0]["hooks"][0]
    assert shlex.split(hook["command"])[0] == str(binary)


def test_contexto_reusa_bloque_y_agrega_reglas(reg, ctx):
    files = plugin_render.render_plugin(reg, backends_dict(), ctx)
    content = files["orquesta/context/orquestador.md"]
    assert content.splitlines()[0] == "Generación: abc12345"
    render_ctx = RenderContext(paths=ctx["paths"], backends=parse_backends(backends_dict()),
                               agent_prefix="orquesta:", agent_names=tuple(a.name for a in reg.agents))
    assert render_block_inner(reg, render_ctx) in content
    prose = " ".join(content.split())
    for rule in ["Reglas de orquestación", ".orquesta/plan.md", "único OK", "merge a main",
                 "escaladas", "altas", "bajas", "prompt", "fallas", "review rechazada",
                 "segunda terminal", "comando exacto", "run_in_background", "tres familias",
                 "reinicio", "orquesta rrhh triage"]:
        assert rule in prose


def test_agentes_y_referencias_prefijadas_sin_romper_cli_o_rutas(ctx):
    data = registry_dict()
    data["agents"][0]["prompt"] += " Delegá en mantenedor."
    data["agents"][3]["prompt"] += (
        " Delegá en gemini-lector, orquesta:codex-forja y codex:codex-rescue."
        " No confundas gemini-lector-extra ni manual/gemini-lector.md."
        " Plugin: {plugin_dir}. Corré `orquesta run gemini-lector`."
    )
    data["agents"][3]["description"] = "Coordina con codex-critico."
    reg = parse_registry(data)
    files = plugin_render.render_plugin(reg, backends_dict(), ctx)
    content = files["orquesta/agents/mantenedor.md"]
    for phrase in ["orquesta:gemini-lector", "orquesta:codex-forja", "codex:codex-rescue",
                   "gemini-lector-extra", "manual/gemini-lector.md", "orquesta run gemini-lector",
                   "orquesta:codex-critico", str(ctx["paths"].plugin_dir)]:
        assert phrase in content
    assert "name: mantenedor\n" in content
    assert "orquesta:orquesta:" not in content
    wrapper = files["orquesta/agents/gemini-lector.md"]
    assert "orquesta:mantenedor" in wrapper
    assert "orquesta run gemini-lector" in wrapper
    assert "orquesta/codex-forja/" in files["orquesta/agents/codex-forja.md"]
    assert "| `orquesta:gemini-lector` |" in files["orquesta/context/orquestador.md"]


def test_prefijo_y_placeholder_opt_in_no_cambian_legacy(reg, ctx):
    render_ctx = RenderContext(paths=ctx["paths"], backends=parse_backends(backends_dict()))
    assert render_ctx.agent_prefix == ""
    assert render_ctx.substitute("{plugin_dir}") == str(ctx["paths"].plugin_dir)
    assert render_agent_md(reg.agent("mantenedor"), render_ctx).startswith("---\nname: mantenedor\n")


def test_agentes_deshabilitados_no_se_renderizan(ctx):
    data = registry_dict()
    data["agents"][0].update(enabled=False, manual=True)
    files = plugin_render.render_plugin(parse_registry(data), backends_dict(), ctx)
    assert "orquesta/agents/gemini-lector.md" not in files


def test_manual_se_copia_byte_a_byte(ctx):
    data = registry_dict()
    data["agents"][3]["manual"] = True
    manual = "---\nname: mantenedor\n---\n\nTexto manual: gemini-lector.\n"
    (ctx["paths"].manual_dir / "mantenedor.md").write_text(manual, encoding="utf-8")
    files = plugin_render.render_plugin(parse_registry(data), backends_dict(), ctx)
    assert files["orquesta/agents/mantenedor.md"].encode() == manual.encode()


def test_manual_ausente_no_se_reemplaza_por_prompt(ctx):
    data = registry_dict()
    data["agents"][3]["manual"] = True
    with pytest.raises(ConfigError, match="manual/mantenedor.md"):
        plugin_render.render_plugin(parse_registry(data), backends_dict(), ctx)


def test_commands_y_skill_contrato(reg, ctx):
    files = plugin_render.render_plugin(reg, backends_dict(), ctx)
    plan = files["orquesta/commands/plan.md"]
    assert "orquesta:claude-interrogador" in plan
    assert "orquesta:claude-arquitecto" in plan
    assert ".orquesta/plan.md" in plan
    skill = files["orquesta/skills/orquesta-cli/SKILL.md"]
    for field in ["version", "comando", "estado", "generacion", "datos", "errores", "avisos"]:
        assert f"`{field}`" in skill
    for state in ["OK", "NECESITO_CONFIRMACION", "BLOQUEADO", "RATE_LIMIT", "ORQUESTA_OFF", "OCUPADO", "ERROR"]:
        assert state in skill
    assert "--confirm" in skill and "--forzar" in skill
    assert "segunda terminal" in " ".join(skill.split())
    assert "nunca" in skill


@pytest.mark.skipif(shutil.which("claude") is None, reason="claude no está en PATH")
def test_claude_plugin_validate_json(reg, ctx, tmp_path):
    root = tmp_path / "plugin"
    for relative, content in plugin_render.render_plugin(reg, backends_dict(), ctx).items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for target in [root, root / "orquesta"]:
        result = subprocess.run(["claude", "plugin", "validate", str(target), "--json"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["success"] is True


def test_comandos_usan_argumentos_y_rrhh_abre_la_sesion_aparte(ctx):
    files = plugin_render.render_plugin(parse_registry(registry_dict()), backends_dict(), ctx)
    for nombre in ("plan", "reunion", "mood"):
        assert "$ARGUMENTS" in files[f"orquesta/commands/{nombre}.md"]
    assert "orquesta rrhh abrir" in files["orquesta/commands/rrhh.md"]


def test_subcomando_rrhh_no_se_prefija_aunque_haya_un_agente_rrhh(ctx):
    render_ctx = RenderContext(paths=ctx["paths"], backends=parse_backends(backends_dict()),
                               agent_prefix="orquesta:", agent_names=("rrhh", "claude-juez"))
    text = render_ctx.reference_agents(
        "Corré `orquesta rrhh triage` y `orquesta rrhh nota`; consultá a rrhh o a claude-juez.")
    assert "orquesta orquesta:" not in text
    assert "`orquesta rrhh triage`" in text and "`orquesta rrhh nota`" in text
    assert "consultá a orquesta:rrhh o a orquesta:claude-juez" in text
