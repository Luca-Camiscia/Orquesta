"""Runner (`orquesta run`): arma el comando desde backends.yaml y aplica las reglas en código."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from helpers import REPO, backends_dict, dump_yaml, git, load_yaml
from orquesta import runner
from orquesta.generate import apply_plan, build_plan
from orquesta.schema import parse_backends


def editar(path: Path, mod) -> None:
    data = load_yaml(path)
    mod(data)
    dump_yaml(data, path)


def deploy(env, mod=None) -> None:
    if mod:
        editar(env.registry, mod)
    plan = build_plan(env)
    assert not plan.errors and not plan.violations, (plan.errors, plan.violations)
    apply_plan(plan, "deploy", run_tests=None)


@pytest.fixture
def proyecto(tmp_path) -> Path:
    p = tmp_path / "proyecto"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    (p / "README.md").write_text("hola\n")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "init")
    return p


def correr(env, agente, prompt, cwd, **kw) -> runner.RunResult:
    return runner.run(runner.RunRequest(agent=agente, cwd=cwd, prompt=prompt, **kw), env)


BACKENDS = parse_backends(backends_dict()).backends
REAL = parse_backends(load_yaml(REPO / "backends.yaml")).backends


# --- armado del comando ----------------------------------------------------------------------

def test_codex_recibe_el_prompt_por_stdin_con_los_flags_del_modo(env):
    cmd = runner.build_command(BACKENDS["codex"], mode="read-only", model="gpt-x",
                               cwd=Path("/proyecto"), prompt="hola", state_dir=env.state_dir)
    assert cmd.stdin == "hola" and cmd.argv[-1] == "-"
    i = cmd.argv.index("--sandbox")
    assert cmd.argv[i:i + 2] == ["--sandbox", "read-only"]
    assert cmd.argv[cmd.argv.index("-m") + 1] == "gpt-x"
    assert cmd.argv[cmd.argv.index("-C") + 1] == "/proyecto"


def test_agy_recibe_el_prompt_como_ultimo_argumento(env):
    cmd = runner.build_command(BACKENDS["agy"], mode="read-only", model=None,
                               cwd=Path("/proyecto"), prompt="hola", state_dir=env.state_dir)
    assert cmd.argv[-2:] == ["-p", "hola"] and cmd.stdin is None
    assert "--model" not in cmd.argv


def test_agy_con_prompt_largo_lo_pasa_por_archivo(env):
    largo = "x" * 5000
    cmd = runner.build_command(BACKENDS["agy"], mode="read-only", model=None,
                               cwd=Path("/proyecto"), prompt=largo, state_dir=env.state_dir)
    assert len(cmd.argv[-1]) < 2000 and cmd.temp_files
    assert cmd.temp_files[0].read_text() == largo
    assert str(cmd.temp_files[0]) in cmd.argv[-1]


def test_read_only_de_agy_corre_dentro_de_bwrap_sin_bindear_el_proyecto(env):
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=Path("/proyecto"),
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    assert cmd.argv[:5] == ["bwrap", "--ro-bind", "/", "/", "--dev"]
    assert "--die-with-parent" in cmd.argv
    assert cmd.argv[cmd.argv.index("--") + 1] == "agy"
    assert "/proyecto" not in cmd.argv[:cmd.argv.index("--")]


def test_write_de_agy_en_bwrap_solo_bindea_el_workspace(env):
    cmd = runner.build_command(REAL["agy"], mode="write", model=None, cwd=Path("/proyecto"),
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    pre = cmd.argv[:cmd.argv.index("--")]
    i = pre.index("/proyecto")
    assert pre[i - 1:i + 2] == ["--bind", "/proyecto", "/proyecto"]


def test_fuera_de_linux_no_hay_sandbox_y_avisa(env):
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=Path("/p"),
                               prompt="hola", state_dir=env.state_dir, platform="win32")
    assert cmd.argv[0] == "agy"
    assert cmd.warnings and "sandbox" in cmd.warnings[0]


# --- ejecución -------------------------------------------------------------------------------

def test_run_ok_con_codex(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert (r.status, r.exit_code) == ("OK", 0)
    assert r.output.startswith("PONG:")


def test_run_antepone_la_persona_y_el_modo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "ECHOARGS", proyecto)
    prompt = json.loads(r.output)["prompt"]
    assert prompt.startswith("Sos el crítico.")
    assert "SOLO LECTURA" in prompt and "ECHOARGS\n\n---\n\n" in prompt
    assert prompt.rstrip().endswith(runner.CONTRACT.splitlines()[-1])


def test_run_ok_con_agy_devuelve_el_texto_de_la_respuesta_json(env, proyecto):
    deploy(env)
    r = correr(env, "gemini-lector", "hola", proyecto)
    assert r.status == "OK" and r.output.startswith("PONG:") and "{" not in r.output


def test_status_json_distinto_de_success_es_fallo(env, proyecto):
    deploy(env)
    assert correr(env, "gemini-lector", "STATUSFAIL", proyecto).status == "FALLO_BACKEND"


def test_salida_vacia_es_fallo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "EMPTY", proyecto)
    assert r.status == "FALLO_BACKEND" and "vacía" in r.detail


def test_rate_limit(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "RATELIMIT", proyecto)
    assert (r.status, r.exit_code) == ("RATE_LIMIT", 75)


def test_mencionar_el_limite_en_una_respuesta_exitosa_no_es_rate_limit(env, proyecto):
    deploy(env)
    assert correr(env, "codex-critico", "MENTION_LIMIT", proyecto).status == "OK"


def test_timeout(env, proyecto):
    editar(env.backends, lambda d: d["backends"]["codex"]["modes"]["read-only"].update(timeout_s=1))
    deploy(env)
    r = correr(env, "codex-critico", "SLEEP:5", proyecto)
    assert (r.status, r.exit_code) == ("TIMEOUT", 124)


def test_orquesta_off_desde_un_subdirectorio(env, proyecto):
    deploy(env)
    (proyecto / ".orquesta-off").touch()
    (proyecto / "sub").mkdir()
    r = correr(env, "codex-critico", "hola", proyecto / "sub")
    assert (r.status, r.exit_code) == ("ORQUESTA_OFF", 3)


def test_write_fuera_de_un_repo_git_es_no_git(env, tmp_path):
    deploy(env)
    suelto = tmp_path / "suelto"
    suelto.mkdir()
    r = correr(env, "codex-forja", "WRITE:x.txt", suelto, task="fix")
    assert (r.status, r.exit_code) == ("NO_GIT", 4)


def test_write_con_worktree_sin_task_es_error(env, proyecto):
    deploy(env)
    assert correr(env, "codex-forja", "WRITE:x.txt", proyecto).status == "ERROR"


def test_read_only_detecta_escrituras(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "WRITE:colado.txt", proyecto)
    assert (r.status, r.exit_code) == ("VIOLACION_READ_ONLY", 7)
    assert "colado.txt" in r.detail


def test_write_en_worktree_commitea_en_su_rama_sin_tocar_el_repo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-forja", "WRITE:arreglo.txt", proyecto, task="fix promedio")
    assert r.status == "OK", r.detail
    assert r.meta["branch"] == "orquesta/codex-forja/fix-promedio"
    assert r.meta["commit"] and "arreglo.txt" in r.meta["diffstat"]
    assert not (proyecto / "arreglo.txt").exists()
    assert "arreglo.txt" in git(proyecto, "show", "--stat", "--format=", r.meta["branch"])


def test_ocupado_si_no_hay_slots_libres(env, proyecto):
    deploy(env)  # agy: max_parallel 1
    with runner.hold_slots(env, "agy", 1):
        r = correr(env, "gemini-lector", "hola", proyecto, wait_s=0.2)
    assert (r.status, r.exit_code) == ("OCUPADO", 5)


def test_agente_no_desplegado(env, proyecto):
    r = correr(env, "codex-critico", "hola", proyecto)
    assert r.status == "ERROR" and "orquesta generate" in r.detail


def test_agente_nativo_no_pasa_por_el_runner(env, proyecto):
    deploy(env)
    r = correr(env, "mantenedor", "hola", proyecto)
    assert r.status == "ERROR" and "nativo" in r.detail


def test_usa_la_config_aplicada_y_no_la_editada_sin_aplicar(env, proyecto):
    deploy(env)
    editar(env.registry, lambda d: d["agents"][2].update(prompt="PERSONA NUEVA SIN APLICAR"))
    r = correr(env, "codex-critico", "ECHOARGS", proyecto)
    assert "PERSONA NUEVA" not in json.loads(r.output)["prompt"]


def test_ping(env):
    deploy(env)
    r = runner.ping(env, "codex-critico")
    assert r.status == "OK" and "PONG" in r.output and r.latency_s >= 0


def test_formato_de_salida_con_estado_primero_si_falla(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "RATELIMIT", proyecto)
    assert runner.format_result(r).startswith("ESTADO: RATE_LIMIT")
