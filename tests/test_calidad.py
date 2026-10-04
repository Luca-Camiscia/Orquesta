"""Tanda 2: anti-disparadores (avoid_when) y contrato de salida de los delegados."""
from __future__ import annotations

import json

import pytest

from helpers import backends_dict, dump_yaml, load_yaml, registry_dict
from orquesta import runner
from orquesta.generate import apply_plan, build_plan
from orquesta.guard import entry_hash
from orquesta.render import RenderContext, render_agent_md, render_block_inner
from orquesta.schema import parse_backends, parse_registry


def reg_con(mod):
    d = registry_dict()
    mod(d)
    return parse_registry(d)


def test_avoid_when_va_a_la_description_y_al_ruteo(env):
    reg = reg_con(lambda d: d["agents"][1].update(avoid_when=["el cambio altera el comportamiento"]))
    ctx = RenderContext(paths=env, backends=parse_backends(backends_dict()))
    md = render_agent_md(reg.agent("codex-forja"), ctx)
    assert "NO usar si el cambio altera el comportamiento" in md.split("---\n")[1]
    assert "NO usar si el cambio altera el comportamiento" in render_block_inner(reg, ctx)


def test_avoid_when_vacio_no_cambia_el_hash():
    a = parse_registry(registry_dict()).agent("mantenedor")
    b = reg_con(lambda d: d["agents"][3].update(avoid_when=[])).agent("mantenedor")
    assert entry_hash(a) == entry_hash(b)


@pytest.mark.parametrize("texto,esperado", [
    ("bla\nRESULTADO: OK\nCONFIANZA: 85\nEVIDENCIA: a.py:3\nSIGUIENTE PASO: nada", ("OK", 85)),
    ("RESULTADO: parcial\nCONFIANZA: 40%", ("PARCIAL", 40)),
    ("sin contrato", (None, None)),
    ("CONFIANZA: 300", (None, None)),
])
def test_parse_contrato(texto, esperado):
    c = runner.parse_contract(texto)
    assert (c.get("resultado"), c.get("confianza")) == esperado


def test_el_runner_pide_el_contrato_y_lo_guarda_en_meta(env, tmp_path):
    apply_plan(build_plan(env), "deploy", run_tests=None)
    r = runner.run(runner.RunRequest(agent="codex-critico", cwd=tmp_path, prompt="ECHOARGS"), env)
    prompt = json.loads(r.output)["prompt"]
    assert "RESULTADO:" in prompt and "CONFIANZA:" in prompt
    r2 = runner.run(runner.RunRequest(agent="codex-critico", cwd=tmp_path,
                                      prompt="hola\nRESULTADO: OK\nCONFIANZA: 70"), env)
    assert "contrato" in r2.meta
