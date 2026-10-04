"""Edición de moods (la usan la UI y that-it-guy) y su reflejo en el bloque de CLAUDE.md."""
from __future__ import annotations

import pytest

from helpers import backends_dict, registry_dict
from orquesta.moods import MoodError, add_mood, delete_mood, rename_mood, set_default, update_mood
from orquesta.render import RenderContext, render_block_inner
from orquesta.schema import parse_backends, parse_registry


def test_no_se_puede_borrar_el_mood_default():
    d = registry_dict()
    with pytest.raises(MoodError, match="default"):
        delete_mood(d, "transparente")


def test_borrar_un_mood_que_no_es_default():
    d = registry_dict()
    delete_mood(d, "debate")
    assert "debate" not in d["orchestrator"]["moods"]
    parse_registry(d)


def test_borrar_mood_inexistente_falla():
    with pytest.raises(MoodError):
        delete_mood(registry_dict(), "fantasma")


def test_renombrar_el_default_actualiza_default_mood_y_conserva_el_orden():
    d = registry_dict()
    rename_mood(d, "transparente", "claro")
    assert d["orchestrator"]["default_mood"] == "claro"
    assert list(d["orchestrator"]["moods"]) == ["silencioso", "claro", "debate"]
    parse_registry(d)


def test_renombrar_a_un_nombre_existente_falla():
    with pytest.raises(MoodError):
        rename_mood(registry_dict(), "debate", "silencioso")


@pytest.mark.parametrize("nombre", ["Con Espacios", "", "ñandú", "-x"])
def test_agregar_mood_con_nombre_invalido_falla(nombre):
    with pytest.raises(MoodError):
        add_mood(registry_dict(), nombre, "desc")


def test_agregar_mood_duplicado_o_sin_descripcion_falla():
    with pytest.raises(MoodError):
        add_mood(registry_dict(), "debate", "otra")
    with pytest.raises(MoodError):
        add_mood(registry_dict(), "nuevo", "   ")


def test_agregar_y_editar_mood():
    d = registry_dict()
    add_mood(d, "telegrafico", "Una línea por idea.")
    update_mood(d, "telegrafico", "Ultra breve.")
    assert d["orchestrator"]["moods"]["telegrafico"] == "Ultra breve."
    parse_registry(d)


def test_set_default_inexistente_falla():
    with pytest.raises(MoodError):
        set_default(registry_dict(), "fantasma")


def test_cambiar_el_default_se_refleja_en_el_bloque(env):
    d = registry_dict()
    set_default(d, "debate")
    ctx = RenderContext(paths=env, backends=parse_backends(backends_dict()))
    inner = render_block_inner(parse_registry(d), ctx)
    assert "Mood por defecto: **debate**" in inner
    assert "Mood activo al arrancar: `debate`" in inner
