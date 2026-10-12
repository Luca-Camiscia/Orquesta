"""Validación pura de revisores elegidos explícitamente."""
from copy import deepcopy

import pytest

from orquesta import revision, tareas


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


@pytest.mark.parametrize("writer,esperado", [
    ("codex-forjador", "claude-centinela"),
    ("claude-autor", "codex-abogado-del-diablo"),
])
def test_elegir_revisor_descarta_backend_no_disponible(writer, esperado):
    manifest = {"agents": {
        "codex-forjador": {"backend": "codex", "mode": "write"},
        "claude-autor": {"backend": "claude", "mode": "write"},
        "gemini-bibliotecario": {"backend": "agy", "mode": "read-only"},
        "claude-centinela": {"backend": "claude", "mode": "read-only"},
        "codex-abogado-del-diablo": {"backend": "codex", "mode": "read-only"},
    }}
    anterior = deepcopy(manifest)
    assert revision.elegir_revisor(manifest, writer, disponible=lambda b: b != "agy") == esperado
    assert manifest == anterior


def test_elegir_revisor_sin_filtro_conserva_preferencia():
    manifest = {"agents": {
        "autor": {"backend": "codex", "mode": "write"},
        "claude-centinela": {"backend": "claude", "mode": "read-only"},
        "gemini-bibliotecario": {"backend": "agy", "mode": "read-only"},
    }}
    assert revision.elegir_revisor(manifest, "autor") == "gemini-bibliotecario"
    assert revision.elegir_revisor(manifest, "autor", disponible=lambda b: False) is None


def test_review_aprobada_recortada_bloquea_con_medidas_y_archivos():
    registro = tareas.a_registro_review({
        "rama": "orquesta/autor/fix", "veredicto": "APROBAR", "diff_recortado": True,
        "diff_mostrado_chars": 20000, "diff_chars": 30000,
        "diff_archivos": ["a_largo.txt", "z-danger.py"],
    })
    razones = revision.razones_bloqueo(registro)
    assert len(razones) == 1
    assert "diff recortado (20000 de 30000 caracteres)" in razones[0]
    assert "a_largo.txt" in razones[0] and "z-danger.py" in razones[0]
    assert "confirmalo explícitamente" in razones[0]
