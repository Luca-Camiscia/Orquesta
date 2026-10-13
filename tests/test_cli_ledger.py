"""CLI del ledger con proyecto git real y estado aislado."""
import json

import pytest

from orquesta import cli, ledger, worktrees


@pytest.fixture
def proyecto(env, monkeypatch):
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.chdir(env.home)
    return env.home


def salida(capsys, comando):
    texto = capsys.readouterr().out
    assert len(texto.splitlines()) == 1
    respuesta = json.loads(texto)
    assert respuesta["version"] == 1 and respuesta["comando"] == comando
    return respuesta


def test_anotar_umbrales_y_persistencia(proyecto, capsys):
    for n, (codigo, estado, accion) in enumerate([
        (0, "OK", "seguir"), (0, "OK", "replanificar"),
        (0, "OK", "replanificar"), (3, "BLOQUEADO", "escalar"),
    ], 1):
        assert cli.main(["ledger", "anotar", "fix", "--sin-progreso", "--nota", "sin avance"]) == codigo
        respuesta = salida(capsys, "ledger anotar")
        assert respuesta["estado"] == estado
        assert respuesta["datos"]["accion"] == accion
        assert respuesta["datos"]["intentos"] == n
        if accion == "replanificar":
            assert respuesta["avisos"] == [
                f"estancamiento: {n} intentos sin progreso; replanificá con un brief nuevo (sin historial), p. ej. con el arquitecto"]
        if accion == "escalar":
            assert respuesta["errores"] == ["tope alcanzado: escalá al usuario antes de otro intento"]
    assert cli.main(["ledger", "ver"]) == 0
    assert salida(capsys, "ledger ver")["datos"]["tareas"][0]["intentos"] == 4


def test_tope_seis_incluso_con_progreso(proyecto, capsys):
    for n in range(1, 7):
        assert cli.main(["ledger", "anotar", "fix", "--progreso"]) == (3 if n == 6 else 0)
        respuesta = salida(capsys, "ledger anotar")
    assert respuesta["estado"] == "BLOQUEADO" and respuesta["datos"]["intentos"] == 6
    assert respuesta["datos"]["sin_progreso"] == 0


def test_ver_muestra_corridas_del_runner(env, proyecto, capsys):
    ledger.reservar_corrida(worktrees.project_id(proyecto), "fix", paths=env)
    assert cli.main(["ledger", "ver"]) == 0
    estado = salida(capsys, "ledger ver")["datos"]["tareas"][0]
    assert estado["corridas"] == 1 and estado["intentos"] == 0


def test_cerrar_y_validacion(proyecto, capsys):
    assert cli.main(["ledger", "anotar", "fix", "--progreso"]) == 0
    salida(capsys, "ledger anotar")
    assert cli.main(["ledger", "cerrar", "fix", "--motivo", "resuelto"]) == 0
    assert salida(capsys, "ledger cerrar")["datos"]["cerrada"] is True
    assert cli.main(["ledger", "anotar", "fix", "--progreso"]) == 1
    assert salida(capsys, "ledger anotar")["estado"] == "ERROR"


@pytest.mark.parametrize("args,comando", [
    (["anotar", "../fix", "--progreso"], "ledger anotar"),
    (["cerrar", "../fix", "--motivo", "resuelto"], "ledger cerrar"),
    (["cerrar", "inexistente", "--motivo", "resuelto"], "ledger cerrar"),
])
def test_validacion_error(proyecto, capsys, args, comando):
    assert cli.main(["ledger", *args]) == 1
    assert salida(capsys, comando)["estado"] == "ERROR"


def test_repo_explicito_y_subdirectorio(env, proyecto, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ledger", "anotar", "fix", "--progreso", "--repo", str(proyecto)]) == 0
    salida(capsys, "ledger anotar")
    subdir = proyecto / "subdir"
    subdir.mkdir()
    monkeypatch.chdir(subdir)
    assert cli.main(["ledger", "ver"]) == 0
    assert salida(capsys, "ledger ver")["datos"]["tareas"][0]["tarea"] == "fix"
    assert (env.state_dir / worktrees.project_id(proyecto) / "ledger.json").exists()


def test_desde_worktree_usa_proyecto_original(env, proyecto, monkeypatch, capsys):
    wt = worktrees.create(env, repo=proyecto, agent="codex-forjador", task="fix")
    monkeypatch.chdir(wt.path)
    assert cli.main(["ledger", "anotar", "fix", "--progreso"]) == 0
    salida(capsys, "ledger anotar")
    monkeypatch.chdir(proyecto)
    assert cli.main(["ledger", "ver"]) == 0
    assert salida(capsys, "ledger ver")["datos"]["tareas"][0]["intentos"] == 1


def test_sin_repo_git(proyecto, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["ledger", "ver"]) == 1
    assert salida(capsys, "ledger ver")["estado"] == "ERROR"


def test_json_corrupto(env, proyecto, capsys):
    archivo = env.state_dir / worktrees.project_id(proyecto) / "ledger.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text("{roto")
    assert cli.main(["ledger", "ver"]) == 1
    assert salida(capsys, "ledger ver")["estado"] == "ERROR"


@pytest.mark.parametrize("flags", [[], ["--progreso", "--sin-progreso"]])
def test_progreso_obligatorio_y_excluyente(flags):
    with pytest.raises(SystemExit) as exc:
        cli.build_parser().parse_args(["ledger", "anotar", "fix", *flags])
    assert exc.value.code == 2
