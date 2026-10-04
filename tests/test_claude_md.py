"""Bloque marcado de CLAUDE.md: solo se toca lo que está entre los marcadores."""
from __future__ import annotations

import pytest

from orquesta.claude_md import END, START, MarkerError, apply_block, extract_inner


def test_crea_contenido_si_no_hay_archivo():
    assert apply_block(None, "hola") == f"{START}\nhola\n{END}\n"


def test_agrega_al_final_sin_tocar_lo_existente():
    original = "# Mis reglas\n\nNo borres nada.\n"
    out = apply_block(original, "bloque")
    assert out.startswith(original)
    assert out == original + f"\n{START}\nbloque\n{END}\n"


def test_agrega_separando_cuando_falta_el_newline_final():
    original = "texto sin newline"
    assert apply_block(original, "b") == original + f"\n\n{START}\nb\n{END}\n"


def test_reemplaza_solo_entre_marcadores():
    antes, despues = "arriba\n", "\nabajo\n"
    original = f"{antes}{START}\nviejo\n{END}{despues}"
    assert apply_block(original, "nuevo") == f"{antes}{START}\nnuevo\n{END}{despues}"


def test_es_idempotente():
    una = apply_block("intro\n", "x\ny")
    assert apply_block(una, "x\ny") == una


def test_preserva_crlf():
    original = f"a\r\n{START}\r\nviejo\r\n{END}\r\nb\r\n"
    assert apply_block(original, "l1\nl2") == f"a\r\n{START}\r\nl1\r\nl2\r\n{END}\r\nb\r\n"


def test_preserva_la_ausencia_de_newline_final():
    original = f"x\n{START}\nv\n{END}"
    assert apply_block(original, "n") == f"x\n{START}\nn\n{END}"


@pytest.mark.parametrize("texto", [
    f"{START}\na\n{END}\n{START}\nb\n{END}\n",   # duplicados
    f"{START}\nsin cierre\n",                    # falta el fin
    f"{END}\n{START}\n",                         # invertidos
    f"{START}\n{END}\n{END}\n",                  # fin duplicado
])
def test_marcadores_rotos_fallan(texto):
    with pytest.raises(MarkerError):
        apply_block(texto, "x")


def test_extract_inner():
    assert extract_inner(f"a\n{START}\nuno\ndos\n{END}\nb") == "uno\ndos"
    assert extract_inner("sin marcadores") is None
    assert extract_inner(f"{START}\r\nwin\r\n{END}") == "win"
