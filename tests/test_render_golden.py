"""Golden de bytes UTF-8: registro compartido y rutas fijas, sin normalizar la salida."""
from __future__ import annotations

from pathlib import Path

import pytest

from helpers import backends_dict, registry_dict
from orquesta.paths import Paths
from orquesta.render import RenderContext, render_agent_md, render_block_inner
from orquesta.schema import parse_backends, parse_registry

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture
def contexto_golden():
    # Renderizar no escribe: estas rutas son entradas literales, independientes del host.
    paths = Paths(home=Path("/orquesta"), claude_dir=Path("/claude"))
    return RenderContext(paths=paths, backends=parse_backends(backends_dict()))


@pytest.mark.parametrize("nombre", ["gemini-lector", "codex-forja", "codex-critico", "mantenedor"])
def test_render_agent_md_coincide_byte_a_byte(contexto_golden, nombre):
    agente = parse_registry(registry_dict()).agent(nombre)
    actual = render_agent_md(agente, contexto_golden).encode("utf-8")
    assert actual == (GOLDEN / f"{nombre}.md").read_bytes()


def test_render_block_inner_coincide_byte_a_byte(contexto_golden):
    actual = render_block_inner(parse_registry(registry_dict()), contexto_golden).encode("utf-8")
    assert actual == (GOLDEN / "block_inner.md").read_bytes()
