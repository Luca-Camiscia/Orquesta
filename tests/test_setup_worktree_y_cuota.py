"""Setup de worktrees por proyecto y medidor de cuota por ventana de 5 h."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from helpers import git
from orquesta import usage, worktrees
from orquesta.generate import apply_plan, build_plan
from orquesta import runner


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proy"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("a\n")
    (r / ".gitignore").write_text(".env\n")
    git(r, "add", "-A")
    git(r, "commit", "-qm", "init")
    (r / ".env").write_text("SECRETO_LOCAL=1\n")
    return r


def configurar(repo, setup="echo listo > setup.ok; echo $ORQUESTA_PORT > puerto.txt", archive=None):
    (repo / ".orquesta").mkdir(exist_ok=True)
    txt = f'[worktree]\nsetup = "{setup}"\ncopiar = [".env"]\n'
    if archive:
        txt += f'archive = "{archive}"\n'
    (repo / ".orquesta" / "proyecto.toml").write_text(txt)


def test_setup_copia_archivos_y_corre_el_script(env, repo):
    configurar(repo)
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="x")
    worktrees.prepare(env, wt)
    assert (wt.path / ".env").read_text() == "SECRETO_LOCAL=1\n"
    assert (wt.path / "setup.ok").exists()
    puerto = int((wt.path / "puerto.txt").read_text())
    assert 20000 <= puerto < 30000


def test_setup_que_falla_corta_la_corrida(env, repo):
    configurar(repo, setup="exit 3")
    apply_plan(build_plan(env), "deploy", run_tests=None)
    r = runner.run(runner.RunRequest(agent="codex-forja", cwd=repo, prompt="WRITE:x.txt", task="t"), env)
    assert r.status == "ERROR" and "setup" in r.detail


def test_archive_corre_antes_de_borrar(env, repo, tmp_path):
    marca = tmp_path / "archivado.txt"
    configurar(repo, archive=f"echo chau > {marca}")
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="x")
    worktrees.clean(env, wt.branch, force=True)
    assert marca.read_text().strip() == "chau"


def test_sin_config_no_hace_nada(env, repo):
    wt = worktrees.create(env, repo=repo, agent="codex-forja", task="x")
    assert worktrees.prepare(env, wt) is None


# --- medidor de cuota ---

def test_ventana_de_5h_y_riesgo(env):
    now = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    viejo = now - timedelta(hours=30)
    for i in range(10):  # ventana vieja que terminó en rate limit con 100k tokens
        usage.record(env, backend="agy", agent="g", status="OK", tokens_in=10000, tokens_out=0, ts=viejo + timedelta(minutes=i))
    usage.record(env, backend="agy", agent="g", status="RATE_LIMIT", ts=viejo + timedelta(minutes=11))
    for i in range(9):  # ventana actual: 90k
        usage.record(env, backend="agy", agent="g", status="OK", tokens_in=10000, tokens_out=0, ts=now - timedelta(minutes=30 - i))
    usage.record(env, backend="codex", agent="c", status="OK", latency_s=3, ts=now - timedelta(hours=6))
    w = usage.window(env, now=now)
    assert w["agy"]["llamadas"] == 9 and w["agy"]["tokens"] == 90000
    assert w["agy"]["limite_estimado"] == 100000 and w["agy"]["riesgo"] == "alto"
    assert w["codex"]["llamadas"] == 0                    # fuera de la ventana
    assert w["agy"]["ultimo_rate_limit"]


def test_el_runner_registra_cada_invocacion(env, tmp_path):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    runner.run(runner.RunRequest(agent="codex-critico", cwd=tmp_path, prompt="hola"), env)
    runner.run(runner.RunRequest(agent="codex-critico", cwd=tmp_path, prompt="RATELIMIT"), env)
    regs = usage.load(env)
    assert [r["status"] for r in regs[-2:]] == ["OK", "RATE_LIMIT"]
    assert regs[-1]["backend"] == "codex" and regs[-1]["agent"] == "codex-critico"
