"""Ledger determinista: persistencia, estancamiento y cierre de tareas."""
import json
from datetime import datetime, timedelta

import pytest


@pytest.fixture
def ledger():
    from orquesta import ledger
    return ledger


def test_conteos_y_progreso_reinicia_la_racha(env, ledger):
    assert ledger.listar("proyecto", paths=env) == []
    for progreso, esperado in [(False, 1), (False, 2), (True, 0), (False, 1)]:
        resultado = ledger.anotar("proyecto", "fix", progreso=progreso, paths=env)
        assert resultado["sin_progreso"] == esperado
    assert resultado == {"tarea": "fix", "intentos": 4, "sin_progreso": 1,
                         "cerrada": False, "accion": "seguir"}
    assert ledger.listar("proyecto", paths=env) == [resultado]
    ledger.anotar("proyecto", "otra", progreso=True, paths=env)
    assert [r["tarea"] for r in ledger.listar("proyecto", paths=env)] == ["fix", "otra"]
    datos = json.loads((env.state_dir / "proyecto" / "ledger.json").read_text())
    assert [i["n"] for i in datos[0]["intentos"]] == [1, 2, 3, 4]
    assert datetime.fromisoformat(datos[0]["intentos"][0]["ts"]).utcoffset() == timedelta(0)


@pytest.mark.parametrize("progresos,accion,racha", [
    ([False], "seguir", 1),
    ([False, False], "replanificar", 2),
    ([False, False, False], "replanificar", 3),
    ([False, False, False, False], "escalar", 4),
    ([True] * 5, "seguir", 0),
    ([True] * 6, "escalar", 0),
    ([True] * 4 + [False, False], "escalar", 2),
    ([True] * 7, "escalar", 0),
])
def test_umbrales(env, ledger, progresos, accion, racha):
    for progreso in progresos:
        resultado = ledger.anotar("proyecto", "fix", progreso=progreso, paths=env)
    assert resultado["intentos"] == len(progresos)
    assert resultado["sin_progreso"] == racha
    assert resultado["accion"] == accion


def test_estado_sin_intentos(ledger):
    assert ledger.estado({"tarea": "fix", "intentos": [], "cerrada": False}) == {
        "tarea": "fix", "intentos": 0, "sin_progreso": 0, "cerrada": False, "accion": "seguir"}


def test_cerrar_impide_anotar_y_conserva_motivo(env, ledger):
    ledger.anotar("proyecto", "fix", progreso=True, paths=env)
    ledger.cerrar("proyecto", "fix", motivo="resuelto", paths=env)
    assert ledger.listar("proyecto", paths=env)[0]["cerrada"] is True
    archivo = env.state_dir / "proyecto" / "ledger.json"
    anterior = archivo.read_text()
    assert json.loads(anterior)[0]["motivo"] == "resuelto"
    with pytest.raises(ValueError, match="cerrada"):
        ledger.anotar("proyecto", "fix", progreso=False, paths=env)
    assert archivo.read_text() == anterior


def test_cerrar_inexistente(env, ledger):
    with pytest.raises(ValueError, match="no hay"):
        ledger.cerrar("proyecto", "fix", motivo="resuelto", paths=env)


@pytest.mark.parametrize("tarea", ["", "Fix", "../fix", "a/b", ".fix", "-fix", "a b", "a" * 65, "fix\n"])
def test_slug_invalido(env, ledger, tarea):
    with pytest.raises(ValueError, match="tarea inválida"):
        ledger.anotar("proyecto", tarea, progreso=True, paths=env)
    with pytest.raises(ValueError, match="tarea inválida"):
        ledger.cerrar("proyecto", tarea, motivo="resuelto", paths=env)


@pytest.mark.parametrize("pid", ["", ".", "..", "../otro", "a/b"])
def test_project_id_invalido(env, ledger, pid):
    with pytest.raises(ValueError, match="id de proyecto inválido"):
        ledger.listar(pid, paths=env)


@pytest.mark.parametrize("texto", [
    "{roto", "{}", "[1]",
    '[{"tarea": "fix", "intentos": {}, "cerrada": false}]',
    '[{"tarea": "fix", "intentos": [], "cerrada": "false"}]',
    '[{"tarea": "../fix", "intentos": [], "cerrada": false}]',
    '[{"tarea": "fix", "intentos": [{}], "cerrada": false}]',
    '[{"tarea": "fix", "intentos": [{"n": 1, "progreso": "false", "nota": "", "ts": "2026-10-09T00:00:00+00:00"}], "cerrada": false}]',
])
def test_json_corrupto_es_valueerror_sin_sobrescribir(env, ledger, texto):
    archivo = env.state_dir / "proyecto" / "ledger.json"
    archivo.parent.mkdir(parents=True)
    archivo.write_text(texto)
    with pytest.raises(ValueError):
        ledger.listar("proyecto", paths=env)
    with pytest.raises(ValueError):
        ledger.anotar("proyecto", "fix", progreso=True, paths=env)
    assert archivo.read_text() == texto


def test_nota_recortada_y_slug_limite(env, ledger):
    tarea = "a" + "._-" * 21
    ledger.anotar("proyecto", tarea, progreso=True, nota="ñ" * 501, paths=env)
    datos = json.loads((env.state_dir / "proyecto" / "ledger.json").read_text())
    assert datos[0]["intentos"][0]["nota"] == "ñ" * 500
