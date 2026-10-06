"""Sobre JSON versionado y códigos de salida de la CLI."""
from __future__ import annotations

import json
import sys


OK = "OK"
NECESITO_CONFIRMACION = "NECESITO_CONFIRMACION"
BLOQUEADO = "BLOQUEADO"
RATE_LIMIT = "RATE_LIMIT"
ORQUESTA_OFF = "ORQUESTA_OFF"
OCUPADO = "OCUPADO"
ERROR = "ERROR"

ESTADOS_VALIDOS = frozenset({
    OK, NECESITO_CONFIRMACION, BLOQUEADO, RATE_LIMIT, ORQUESTA_OFF, OCUPADO, ERROR,
})


def exit_code(estado: str) -> int:
    if estado == OK:
        return 0
    if estado in (NECESITO_CONFIRMACION, BLOQUEADO):
        return 3
    return 1


def sobre(comando: str, estado: str, datos=None, errores=None, avisos=None,
          generacion=None) -> dict:
    if estado not in ESTADOS_VALIDOS:
        raise ValueError(f"Estado desconocido: {estado}")
    return {
        "version": 1,
        "comando": comando,
        "estado": estado,
        "generacion": generacion,
        "datos": {} if datos is None else datos,
        "errores": [] if errores is None else errores,
        "avisos": [] if avisos is None else avisos,
    }


def emit(sobre: dict, stream=None) -> int:
    # sys.stdout se resuelve al llamar, no al importar: si no, queda atado a un stdout que ya no existe.
    print(json.dumps(sobre, ensure_ascii=False), file=stream or sys.stdout)
    return exit_code(sobre["estado"])
