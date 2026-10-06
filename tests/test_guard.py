"""Guardrails deterministas: agentes protegidos, escaladas de permisos y secretos."""
from __future__ import annotations

import pytest

from helpers import registry_dict
from orquesta.guard import baseline_from_manifest, baseline_from_registry, check, pending, scan_secrets
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


def test_agente_nuevo_write_requiere_confirmacion_por_alta():
    nuevo = {"name": "codex-nuevo", "backend": "codex", "mode": "write", "role": "r",
             "tools": ["Bash"]}
    v = check(BASE, reg(lambda d: d["agents"].append(nuevo)))
    assert [(x.agent, x.kind) for x in v] == [("codex-nuevo", "alta")]
    assert pending(v, {"codex-nuevo"}) == []


def test_permission_mode_elevado_es_violacion():
    v = check(BASE, reg(lambda d: d["agents"][2].update(
        extra_frontmatter={"permissionMode": "bypassPermissions"})))
    assert [(x.agent, x.kind) for x in v] == [("codex-critico", "permission_mode")]


def test_prompt_en_agente_no_protegido_requiere_confirmacion():
    v = check(BASE, reg(lambda d: d["agents"][1].update(prompt="nuevo")))
    assert [(x.agent, x.kind) for x in v] == [("codex-forja", "prompt")]
    assert pending(v, {"codex-forja"}) == []


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


@pytest.mark.parametrize("field,value", [
    ("route_when", ["otro caso"]), ("avoid_when", ["otro caso"]),
    ("backend_model", "otro-modelo"),
])
def test_campos_ajenos_al_prompt_no_generan_violacion(field, value):
    assert check(BASE, reg(lambda d: d["agents"][1].update({field: value}))) == []


@pytest.mark.parametrize("enabled,kind", [(False, "baja"), (True, "alta")])
def test_transiciones_enabled_requieren_confirmacion(enabled, kind):
    base = baseline_from_registry(reg(lambda d: d["agents"][1].update(enabled=not enabled)))
    v = check(base, reg(lambda d: d["agents"][1].update(enabled=enabled)))
    assert [(x.agent, x.kind) for x in v] == [("codex-forja", kind)]
    assert pending(v, {"codex-forja"}) == []


def test_eliminar_no_protegido_requiere_confirmacion_por_baja():
    v = check(BASE, reg(lambda d: d["agents"].pop(1)))
    assert [(x.agent, x.kind) for x in v] == [("codex-forja", "baja")]
    assert pending(v, {"codex-forja"}) == []


def test_deshabilitar_protegido_solo_genera_protected():
    v = check(BASE, reg(lambda d: d["agents"][3].update(enabled=False)))
    assert [(x.agent, x.kind) for x in v] == [("mantenedor", "protected")]


@pytest.mark.parametrize("change", ["manual", "prompt"])
def test_prompt_manual_no_protegido_requiere_confirmacion(change):
    manual = reg(lambda d: d["agents"][2].update(manual=True))
    base = baseline_from_registry(manual, {"codex-critico": "versión A"})
    new = (reg(lambda d: d["agents"][2].update(manual=True, prompt="otro"))
           if change == "prompt" else manual)
    text = "versión B" if change == "manual" else "versión A"
    v = check(base, new, {"codex-critico": text})
    assert [(x.agent, x.kind) for x in v] == [("codex-critico", "prompt")]
    assert pending(v, {"codex-critico"}) == []


@pytest.mark.parametrize("changed,has_entry", [(False, True), (True, True), (True, False)])
def test_linea_base_de_prompts_ausente(changed, has_entry):
    entry = {"mode": "write"}
    if has_entry:
        entry["entry_sha256"] = BASE["codex-forja"].entry_sha256
    base = baseline_from_manifest({"guard": {"codex-forja": entry}})
    # Conservar los otros agentes para aislar la migración de las altas.
    base = {**BASE, **base}
    new = reg(lambda d: d["agents"][1].update(role="Otro rol")) if changed else reg()
    v = check(base, new)
    if changed and has_entry:
        assert [(x.agent, x.kind) for x in v] == [("codex-forja", "prompt")]
        assert "versión anterior" in v[0].detail
    else:
        assert v == []


def test_cambiar_frontera_entre_prompt_y_manual_es_violacion():
    old = reg(lambda d: d["agents"][2].update(manual=True, prompt="A"))
    base = baseline_from_registry(old, {"codex-critico": "BC"})
    new = reg(lambda d: d["agents"][2].update(manual=True, prompt="AB"))
    v = check(base, new, {"codex-critico": "C"})
    assert [(x.agent, x.kind) for x in v] == [("codex-critico", "prompt")]


def test_linea_base_desplegada_vacia_exige_altas():
    v = check(baseline_from_manifest({"guard": {}, "agents": {}}), reg())
    assert [(x.agent, x.kind) for x in v] == [
        ("gemini-lector", "alta"), ("codex-forja", "alta"),
        ("codex-critico", "alta"), ("mantenedor", "alta"),
    ]


def test_agente_nuevo_deshabilitado_no_pide_alta_hasta_habilitarlo():
    def nuevo(enabled):
        def mod(d):
            extra = dict(d["agents"][1], name="codex-nuevo", enabled=enabled)
            d["agents"].append(extra)
        return mod
    assert [x.kind for x in check(BASE, reg(nuevo(False)))] == []
    assert [(x.agent, x.kind) for x in check(BASE, reg(nuevo(True)))] == [("codex-nuevo", "alta")]


@pytest.mark.parametrize("tool", ["Write", "Edit", "NotebookEdit", "MultiEdit", "Bash", "*"])
@pytest.mark.parametrize("old_tools", [["Read"], []])
def test_agregar_tools_de_escritura_es_escalada(tool, old_tools):
    # [] en una línea base efectiva significa ninguna tool, no un dato ausente.
    from dataclasses import replace
    base = {**BASE, "codex-forja": replace(BASE["codex-forja"], tools_effective=old_tools)}
    new = reg(lambda d: d["agents"][1].update(tools=["Read", tool]))
    v = check(base, new)
    assert [(x.kind, x.title) for x in v] == [("escalation", "tools")]
    assert tool in v[0].detail
    assert pending(v, {"codex-forja"}) == []


def test_tools_se_comparan_individualmente_aunque_ya_tenga_bash():
    v = check(BASE, reg(lambda d: d["agents"][1].update(tools=["Bash", "Write"])))
    assert [(x.kind, x.title) for x in v] == [("escalation", "tools")]


@pytest.mark.parametrize("old,new", [(["*"], ["Read"]), (["Write"], ["Read"]),
                                      (["Read"], ["Read", "WebSearch"])])
def test_reducir_tools_o_agregar_lectura_no_es_escalada(old, new):
    base = baseline_from_registry(reg(lambda d: d["agents"][1].update(tools=old)))
    assert check(base, reg(lambda d: d["agents"][1].update(tools=new))) == []


def test_tools_de_extra_frontmatter_pisan_las_del_agente():
    old = reg(lambda d: d["agents"][1].update(extra_frontmatter={"tools": ["Read"]}))
    base = baseline_from_registry(old)
    assert base["codex-forja"].tools_effective == ["Read"]
    v = check(base, reg(lambda d: d["agents"][1].update(extra_frontmatter={"tools": "*"})))
    assert {(x.kind, x.title) for x in v} == {
        ("escalation", "tools"), ("escalation", "extra_frontmatter")}


@pytest.mark.parametrize("tools", [None, [], "*", ["Read"], ["Agent(a,b)"]])
def test_tools_effective_coincide_con_el_frontmatter(env, tools):
    from helpers import backends_dict
    from orquesta.render import RenderContext, _tools_value, render_agent_md
    from orquesta.schema import parse_backends
    from ruamel.yaml import YAML
    registry = reg()
    agent = registry.agent("codex-forja")
    # Probar None y "*" en el normalizador sin ampliar el esquema de Agent.
    if not tools or tools == "*":
        assert _tools_value(tools) == "*"
    agent.tools = [] if tools is None else [tools] if isinstance(tools, str) else tools
    ctx = RenderContext(paths=env, backends=parse_backends(backends_dict()))
    fm = YAML(typ="safe", pure=True).load(render_agent_md(agent, ctx).split("---")[1])
    base = baseline_from_registry(registry)[agent.name]
    assert base.tools_effective == fm["tools"]
    if not tools or tools == "*":
        assert base.tools_effective == "*"


@pytest.mark.parametrize("mode,expected", [("write", True), ("read-only", False)])
def test_quitar_worktree_solo_es_escalada_en_write(mode, expected):
    old = reg(lambda d: d["agents"][1].update(mode=mode, isolation="worktree"))
    new = reg(lambda d: d["agents"][1].update(mode=mode, isolation="none"))
    v = check(baseline_from_registry(old), new)
    assert [(x.kind, x.title) for x in v] == ([("escalation", "isolation")] if expected else [])
    assert pending(v, {"codex-forja"}) == []


@pytest.mark.parametrize("old,new", [({}, {"maxTurns": 10}),
                                      ({"maxTurns": 10}, {"maxTurns": 20}),
                                      ({"hooks": {"Stop": []}}, {"hooks": {"Stop": ["nuevo"]}})])
def test_frontmatter_nuevo_o_cambiado_es_escalada(old, new):
    base = baseline_from_registry(reg(lambda d: d["agents"][1].update(extra_frontmatter=old)))
    v = check(base, reg(lambda d: d["agents"][1].update(extra_frontmatter=new)))
    assert [(x.kind, x.title) for x in v] == [("escalation", "extra_frontmatter")]
    assert pending(v, {"codex-forja"}) == []


def test_role_no_protegido_es_escalada_de_prompt():
    v = check(BASE, reg(lambda d: d["agents"][1].update(role="Otro rol")))
    assert [(x.kind, x.title) for x in v] == [("escalation", "role")]
    assert "prompt" in v[0].detail
    assert pending(v, {"codex-forja"}) == []


def test_role_protegido_solo_genera_protected():
    v = check(BASE, reg(lambda d: d["agents"][3].update(role="Otro rol")))
    assert [(x.agent, x.kind) for x in v] == [("mantenedor", "protected")]


@pytest.mark.parametrize("change", [{"tools": ["Bash", "Write"]}, {"isolation": "none"},
                                    {"extra_frontmatter": {"maxTurns": 10}}, {"role": "Otro rol"}])
def test_manifest_legacy_sin_campos_nuevos_no_genera_escaladas(change):
    from dataclasses import asdict
    entries = {name: asdict(base) for name, base in BASE.items()}
    for entry in entries.values():
        for key in ("tools_effective", "isolation_effective", "extra_frontmatter_dict", "role_hash",
                    "extra_frontmatter_sha256"):
            entry.pop(key, None)
    base = baseline_from_manifest({"guard": entries})
    assert check(base, reg(lambda d: d["agents"][1].update(change))) == []
