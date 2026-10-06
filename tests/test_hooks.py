"""Hooks del plugin: inyección de reglas y freno de confirmaciones (tolerantes a cualquier stdin)."""
import io
import json

import pytest

from orquesta import cli, hooks
from orquesta.render import DISABLED_LINE


def correr(evento, paths, stdin_text):
    out = io.StringIO()
    assert hooks.run(evento, paths, stdin=io.StringIO(stdin_text), stdout=out) == 0
    return json.loads(out.getvalue()) if out.getvalue().strip() else None


@pytest.fixture
def contexto(env):
    f = env.plugin_dir / "context" / "orquestador.md"
    f.parent.mkdir(parents=True)
    f.write_text("Generación: abc12345\nreglas del orquestador\n", encoding="utf-8")
    return f


def test_session_start_inyecta_el_contexto(env, contexto, tmp_path):
    salida = correr("session-start", env, json.dumps({"cwd": str(tmp_path), "source": "startup"}))
    assert salida["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "reglas del orquestador" in salida["hookSpecificOutput"]["additionalContext"]


def test_session_start_no_inyecta_en_sesiones_de_agente(env, contexto, tmp_path):
    data = {"cwd": str(tmp_path), "agent_type": "orquesta:claude-juez"}
    assert correr("session-start", env, json.dumps(data)) is None


def test_session_start_con_orquesta_off_solo_la_linea(env, contexto, tmp_path):
    (tmp_path / ".orquesta-off").write_text("")
    salida = correr("session-start", env, json.dumps({"cwd": str(tmp_path)}))
    assert salida["hookSpecificOutput"]["additionalContext"] == DISABLED_LINE


def test_session_start_sin_plugin_no_inyecta(env, tmp_path):
    assert correr("session-start", env, json.dumps({"cwd": str(tmp_path)})) is None


@pytest.mark.parametrize("stdin_text", ["", "no es json", "[1, 2]", '{"tool_input": 3}', "\xff"])
@pytest.mark.parametrize("evento", ["session-start", "pre-bash"])
def test_stdin_invalido_sale_cero_sin_traceback(env, evento, stdin_text):
    out = io.StringIO()
    assert hooks.run(evento, env, stdin=io.StringIO(stdin_text), stdout=out) == 0


@pytest.mark.parametrize("comando,pide", [
    ("orquesta merge orquesta/codex-forjador/x --confirm", True),
    ("uv run orquesta generate --confirm that-it-guy", True),
    ("orquesta merge x --forzar --confirm", True),
    ("python3 -c 'from orquesta.cli import main; main([\"merge\", \"x\", \"--confirm\"])'", True),
    ("python -c 'from orquesta import confirmar; confirmar.pedir_confirmacion(\"x\", \"y\")'", True),
    ("/home/luca/orquesta/.venv/bin/orquesta merge x", True),
    ("script -qc 'orquesta merge x' /dev/null", True),
    ("orquesta merge x", False),
    ("orquesta tareas --json", False),
    ("cd /home/luca/orquesta && uv run pytest -q", False),
    ("uv run python -c \"from orquesta import modelos; print(1)\"", False),
    ("git commit -m 'merge --confirm docs'", False),
])
def test_pre_bash(comando, pide, env):
    data = {"tool_name": "Bash", "tool_input": {"command": comando}}
    salida = correr("pre-bash", env, json.dumps(data))
    if pide:
        assert salida["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert "terminal" in salida["hookSpecificOutput"]["permissionDecisionReason"]
    else:
        assert salida is None


def test_cli_hook_por_stdin(env, monkeypatch, capsys):
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "orquesta merge x --confirm"}})))
    assert cli.main(["hook", "pre-bash"]) == 0
    assert json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecision"] == "ask"
