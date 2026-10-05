"""Guardrails deterministas: agentes protegidos, escaladas de permisos y secretos."""
from __future__ import annotations

import pytest

from helpers import registry_dict
from orquesta.guard import baseline_from_registry, check, pending, scan_secrets
from orquesta.schema import parse_registry


def reg(mod=None):
    d = registry_dict()
    if mod:
        mod(d)
    return parse_registry(d)


BASE = baseline_from_registry(reg())


def test_cambio_en_agente_protegido_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"][3].update(prompt="otro prompt")))
    assert [(x.agent, x.kind) for x in v] == [("mantenedor", "protected")]


def test_la_confirmacion_cubre_la_violacion():
    v = check(BASE, reg(lambda d: d["agents"][3].update(prompt="otro prompt")))
    assert pending(v, {"mantenedor"}) == []
    assert pending(v, {"otro"}) == v


def test_eliminar_agente_protegido_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"].pop(3)))
    assert [(x.agent, x.kind) for x in v] == [("mantenedor", "protected")]


def test_renombrar_agente_protegido_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"][3].update(name="mantenedor-2")))
    assert ("mantenedor", "protected") in [(x.agent, x.kind) for x in v]


def test_desproteger_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"][3].update(protected=False)))
    assert [x.agent for x in v] == ["mantenedor"]


def test_escalada_de_read_only_a_write_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"][0].update(mode="write")))
    assert [(x.agent, x.kind) for x in v] == [("gemini-lector", "escalation")]


def test_agente_nuevo_write_no_es_violacion():
    nuevo = {"name": "codex-nuevo", "backend": "codex", "mode": "write", "role": "r",
             "tools": ["Bash"]}
    assert check(BASE, reg(lambda d: d["agents"].append(nuevo))) == []


def test_permission_mode_elevado_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"][2].update(
        extra_frontmatter={"permissionMode": "bypassPermissions"})))
    assert [(x.agent, x.kind) for x in v] == [("codex-critico", "permission_mode")]


def test_cambio_en_agente_no_protegido_no_es_violacion():
    assert check(BASE, reg(lambda d: d["agents"][1].update(prompt="nuevo"))) == []


def test_sin_baseline_no_hay_violaciones():
    assert check(None, reg()) == []


def test_cambio_del_md_manual_de_un_protegido_es_violacion():
    base = baseline_from_registry(reg(), manual_texts={"mantenedor": "versión A"})
    v = check(base, reg(), manual_texts={"mantenedor": "versión B"})
    assert [x.agent for x in v] == ["mantenedor"]


def test_la_violacion_explica_como_confirmar():
    v = check(BASE, reg(lambda d: d["agents"][3].update(prompt="x")))
    assert "--confirm mantenedor" in str(v[0])


@pytest.mark.parametrize("secreto", [
    "sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123",
    "AIzaSyA1234567890abcdefghijklmnopqrstuv",
    "ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "xoxb-1234567890-abcdefghijkl",
    "AKIAABCDEFGHIJKLMNOP",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
    "ya29.a0AfH6SMBabcdefghijklmnopqrstu",
])
def test_scan_secrets_detecta(secreto):
    assert scan_secrets(f"texto antes {secreto} texto después")


def test_scan_secrets_sin_falsos_positivos():
    texto = ("task-runner-with-a-very-long-name-and-more, risk-free-abcdefghijklmnopqrstu, "
             "orquesta/codex-forjador/fix-promedio, sha 50d32eaef297d9bd5bdbfbb60a4c1a2b4bb8ed01")
    assert scan_secrets(texto) == []


def test_campos_nuevos_en_default_no_cambian_el_hash():
    # Agregar `plugins` al esquema no puede convertir en "cambio protegido" a un agente que no cambió.
    import hashlib
    import json
    from orquesta.guard import entry_hash
    a = reg().agent("mantenedor")
    viejo = {k: v for k, v in a.model_dump(mode="json").items()
             if k not in ("plugins", "avoid_when", "backend_effort")}
    esperado = hashlib.sha256(json.dumps(viejo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert entry_hash(a) == esperado
