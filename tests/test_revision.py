"""Validación pura de revisores elegidos explícitamente."""
from copy import deepcopy

import pytest

from orquesta import revision


@pytest.mark.parametrize("nombre,motivo", [
    ("inexistente", "no existe"),
    ("claude-centinela", None),
    ("claude-escritor", "read-only"),
    ("codex-critico", "otra familia"),
])
def test_validar_revisor(nombre, motivo):
    manifest = {"agents": {
        "codex-forjador": {"backend": "codex", "mode": "write"},
        "claude-centinela": {"backend": "claude", "mode": "read-only"},
        "claude-escritor": {"backend": "claude", "mode": "write"},
        "codex-critico": {"backend": "codex", "mode": "read-only"},
    }}
    anterior = deepcopy(manifest)
    rechazo = revision.validar_revisor(manifest, "codex-forjador", nombre)
    if motivo is None:
        assert rechazo is None
    else:
        assert motivo in rechazo
    assert manifest == anterior
