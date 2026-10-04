"""CLI `orquesta`: códigos de salida y mensajes (sin aplicar de verdad: eso corre la suite)."""
from __future__ import annotations

import io

import pytest

from helpers import dump_yaml, git, load_yaml, snapshot
from orquesta import cli
from orquesta.generate import apply_plan, build_plan


@pytest.fixture
def usar_env(env, monkeypatch):
    monkeypatch.setenv("ORQUESTA_HOME", str(env.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env.claude_dir))
    return env


def deploy(env):
    apply_plan(build_plan(env), "deploy", run_tests=None)


def test_generate_dry_run_muestra_el_diff_y_no_escribe(usar_env, capsys):
    antes = snapshot(usar_env.claude_dir)
    assert cli.main(["generate", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "codex-forja.md" in out and "dry-run" in out
    assert snapshot(usar_env.claude_dir) == antes


def test_generate_sin_cambios_lo_dice(usar_env, capsys):
    deploy(usar_env)
    assert cli.main(["generate", "--dry-run"]) == 0
    assert "sincronizado" in capsys.readouterr().out


def test_generate_con_errores_sale_con_1(usar_env, capsys):
    data = load_yaml(usar_env.registry)
    data["agents"][0]["tools"] = ["Glob"]
    dump_yaml(data, usar_env.registry)
    assert cli.main(["generate", "--dry-run"]) == 1
    assert "Glob" in capsys.readouterr().out


def test_generate_con_cambio_protegido_sale_con_3_y_explica_como_confirmar(usar_env, capsys):
    deploy(usar_env)
    data = load_yaml(usar_env.registry)
    data["agents"][3]["prompt"] = "otro"
    dump_yaml(data, usar_env.registry)
    assert cli.main(["generate", "-m", "x"]) == 3
    assert "--confirm mantenedor" in capsys.readouterr().out


def test_run_agente_inexistente_sale_con_2(usar_env, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("hola"))
    assert cli.main(["run", "nadie", "--cwd", str(usar_env.home)]) == 2
    assert capsys.readouterr().out.startswith("ESTADO: ERROR")


def test_run_lee_el_pedido_de_stdin(usar_env, capsys, monkeypatch, tmp_path):
    deploy(usar_env)
    proyecto = tmp_path / "p"
    proyecto.mkdir()
    git(proyecto, "init", "-q", "-b", "main")
    monkeypatch.setattr("sys.stdin", io.StringIO("hola desde stdin"))
    assert cli.main(["run", "codex-critico", "--cwd", str(proyecto)]) == 0
    assert "PONG: " in capsys.readouterr().out


def test_wt_list_sin_worktrees(usar_env, capsys):
    assert cli.main(["wt", "list"]) == 0
    assert "No hay worktrees" in capsys.readouterr().out


def _reporte(status):
    from orquesta.doctor import Check, Report
    return Report([Check("x", "X", status, "mensaje", "sugerencia")], "2026-10-04 18:00:00")


@pytest.mark.parametrize("status,code", [("ok", 0), ("warn", 0), ("fail", 1)])
def test_doctor_codigo_de_salida(usar_env, capsys, monkeypatch, status, code):
    monkeypatch.setattr("orquesta.doctor.run_doctor", lambda *a, **k: _reporte(status))
    assert cli.main(["doctor"]) == code
    assert "Resumen" in capsys.readouterr().out


def test_doctor_json(usar_env, capsys, monkeypatch):
    import json
    monkeypatch.setattr("orquesta.doctor.run_doctor", lambda *a, **k: _reporte("warn"))
    assert cli.main(["doctor", "--json", "--skip-live"]) == 0
    assert json.loads(capsys.readouterr().out)["overall"] == "warn"


def test_ui_con_puerto_ocupado_avisa_y_sale_con_1(usar_env, capsys):
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        puerto = s.getsockname()[1]
        assert cli.main(["ui", "--port", str(puerto), "--no-browser"]) == 1
    assert "ocupado" in capsys.readouterr().out
