"""Contrato del sobre JSON y sus códigos de salida."""
from __future__ import annotations

import io
import json

import pytest

from orquesta import cli_json


ESTADOS_Y_CODIGOS = [
    ("OK", 0),
    ("NECESITO_CONFIRMACION", 3),
    ("BLOQUEADO", 3),
    ("RATE_LIMIT", 1),
    ("ORQUESTA_OFF", 1),
    ("OCUPADO", 1),
    ("ERROR", 1),
]


def test_sobre_tiene_el_contrato_exacto():
    resultado = cli_json.sobre("generate", "OK")
    assert resultado == {
        "version": 1,
        "comando": "generate",
        "estado": "OK",
        "generacion": None,
        "datos": {},
        "errores": [],
        "avisos": [],
    }


def test_sobre_conserva_los_valores_recibidos():
    assert cli_json.sobre("run", "BLOQUEADO", {"agente": "forjador"},
                          ["Sin permiso"], ["Revisá el comando"], "g1") == {
        "version": 1,
        "comando": "run",
        "estado": "BLOQUEADO",
        "generacion": "g1",
        "datos": {"agente": "forjador"},
        "errores": ["Sin permiso"],
        "avisos": ["Revisá el comando"],
    }


def test_sobre_none_y_defaults_independientes():
    primero = cli_json.sobre("run", "OK", datos=None, errores=None, avisos=None)
    primero["datos"]["agente"] = "forjador"
    primero["errores"].append("error")
    primero["avisos"].append("aviso")
    segundo = cli_json.sobre("run", "OK")
    assert segundo["datos"] == {}
    assert segundo["errores"] == []
    assert segundo["avisos"] == []


@pytest.mark.parametrize("estado,codigo", ESTADOS_Y_CODIGOS)
def test_exit_code_y_estados_validos(estado, codigo):
    assert getattr(cli_json, estado) == estado
    assert cli_json.exit_code(estado) == codigo
    assert cli_json.sobre("run", estado)["estado"] == estado


def test_exit_code_desconocido():
    assert cli_json.exit_code("DESCONOCIDO") == 1


@pytest.mark.parametrize("estado", ["DESCONOCIDO", "ok", ""])
def test_sobre_rechaza_estado_invalido(estado):
    with pytest.raises(ValueError):
        cli_json.sobre("run", estado)


@pytest.mark.parametrize("estado,codigo", ESTADOS_Y_CODIGOS)
def test_emit_json_en_una_linea_y_codigo_de_salida(estado, codigo):
    stream = io.StringIO()
    assert cli_json.emit(cli_json.sobre("run", estado, {"texto": "áé\nsegunda línea"}),
                         stream=stream) == codigo
    salida = stream.getvalue()
    assert salida.endswith("\n")
    assert len(salida.splitlines()) == 1
    assert "áé" in salida
    assert json.loads(salida) == {
        "version": 1,
        "comando": "run",
        "estado": estado,
        "generacion": None,
        "datos": {"texto": "áé\nsegunda línea"},
        "errores": [],
        "avisos": [],
    }
