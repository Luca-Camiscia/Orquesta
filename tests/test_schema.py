"""Validación de orquesta.yaml y backends.yaml."""
from __future__ import annotations

import pytest

from helpers import REPO, backends_dict, load_yaml, registry_dict
from orquesta.schema import ConfigError, check_registry, parse_backends, parse_registry


def errores(data: dict | None = None, backends: dict | None = None) -> list[str]:
    data = registry_dict() if data is None else data
    backends = backends_dict() if backends is None else backends
    try:
        reg = parse_registry(data)
    except ConfigError as exc:
        return exc.errors
    return check_registry(reg, parse_backends(backends))


def test_registro_valido_no_tiene_errores():
    assert errores() == []


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max", "ultra", None])
def test_backend_effort_valido(effort):
    data, backends = registry_dict(), backends_dict()
    data["agents"][1]["backend_effort"] = effort
    backends["backends"]["codex"]["effort_flag"] = ["-c", "model_reasoning_effort={effort}"]
    assert errores(data, backends) == []


@pytest.mark.parametrize("effort", ["invalid", "", "HIGH"])
def test_backend_effort_invalido_se_rechaza(effort):
    data = registry_dict()
    data["agents"][1]["backend_effort"] = effort
    with pytest.raises(ConfigError, match="backend_effort"):
        parse_registry(data)


@pytest.mark.parametrize("index", [0, 1, 3])
def test_backend_effort_requiere_effort_flag(index):
    data = registry_dict()
    data["agents"][index]["backend_effort"] = "high"
    assert any("backend_effort" in e and "effort_flag" in e for e in errores(data))


@pytest.mark.parametrize("nombre", ["Codex", "codex_forja", "-codex", "codex-", "con espacio",
                                    "a" * 65, "codex--forja", ""])
def test_nombre_invalido_se_rechaza(nombre):
    d = registry_dict()
    d["agents"][0]["name"] = nombre
    errs = errores(d)
    assert errs and any("name" in e for e in errs), errs


def test_nombres_duplicados_se_rechazan():
    d = registry_dict()
    d["agents"][1]["name"] = d["agents"][0]["name"]
    assert any("duplicad" in e and "gemini-lector" in e for e in errores(d))


def test_backend_inexistente_se_rechaza():
    d = registry_dict()
    d["agents"][0]["backend"] = "ollama"
    assert any("ollama" in e and "backends.yaml" in e for e in errores(d))


def test_tool_desconocida_se_rechaza():
    # Glob/Grep no existen en Claude Code 2.1.289: un nombre así fallaría en silencio.
    d = registry_dict()
    d["agents"][3]["tools"] = ["Read", "Glob"]
    assert any("Glob" in e for e in errores(d))


def test_tools_mcp_y_specifiers_son_validas():
    d = registry_dict()
    d["agents"][3]["tools"] = ["Read", "mcp__playwright__browser_click", "mcp__context7",
                               "mcp__context7__*", "Agent(worker, researcher)"]
    assert errores(d) == []


def test_tools_vacio_se_rechaza():
    d = registry_dict()
    d["agents"][0]["tools"] = []
    assert any("tools" in e for e in errores(d))


def test_campo_de_frontmatter_desconocido_sugiere_el_correcto():
    d = registry_dict()
    d["agents"][3]["extra_frontmatter"] = {"maxturns": 5}
    assert any("maxturns" in e and "maxTurns" in e for e in errores(d))


def test_frontmatter_reservado_se_rechaza():
    d = registry_dict()
    d["agents"][1]["extra_frontmatter"] = {"isolation": "worktree"}
    assert any("isolation" in e for e in errores(d))


@pytest.mark.parametrize("extra", [{"color": "fucsia"}, {"maxTurns": "diez"},
                                   {"permissionMode": "yolo"}, {"background": "si"}])
def test_valor_de_frontmatter_invalido_se_rechaza(extra):
    d = registry_dict()
    d["agents"][3]["extra_frontmatter"] = extra
    campo = next(iter(extra))
    assert any(campo in e for e in errores(d))


def test_frontmatter_valido_se_acepta():
    d = registry_dict()
    d["agents"][3]["extra_frontmatter"] = {"maxTurns": 20, "color": "cyan", "effort": "high"}
    assert errores(d) == []


def test_campo_mal_escrito_en_agente_se_rechaza():
    d = registry_dict()
    d["agents"][0]["toolz"] = ["Bash"]
    assert any("toolz" in e for e in errores(d))


def test_default_mood_inexistente_se_rechaza():
    d = registry_dict()
    d["orchestrator"]["default_mood"] = "festivo"
    assert any("festivo" in e for e in errores(d))


def test_mood_sin_descripcion_se_rechaza():
    d = registry_dict()
    d["orchestrator"]["moods"]["vacio"] = "  "
    assert any("vacio" in e for e in errores(d))


def test_defaults_de_modo_aislamiento_y_modelo():
    reg = parse_registry(registry_dict())
    lector, forja, mant = (reg.agent("gemini-lector"), reg.agent("codex-forja"),
                           reg.agent("mantenedor"))
    assert lector.mode == "read-only"
    assert lector.effective_isolation == "none"
    assert lector.effective_wrapper_model == "haiku"
    assert forja.effective_isolation == "worktree"
    assert mant.effective_isolation == "none"
    assert mant.effective_wrapper_model == "sonnet"


def test_worktree_requiere_modo_write():
    d = registry_dict()
    d["agents"][0]["isolation"] = "worktree"
    assert any("worktree" in e for e in errores(d))


def test_read_only_con_tools_de_escritura_se_rechaza():
    d = registry_dict()
    d["agents"][3]["mode"] = "read-only"
    assert any("read-only" in e and "Write" in e for e in errores(d))


@pytest.mark.parametrize("modelo,ok", [("gpt-5", False), ("claude-opus-5-5", True),
                                       ("fable", True), ("inherit", True)])
def test_wrapper_model(modelo, ok):
    d = registry_dict()
    d["agents"][0]["wrapper_model"] = modelo
    assert (errores(d) == []) is ok


def test_limits_con_backend_inexistente_se_rechaza():
    d = registry_dict()
    d["limits"]["max_parallel"]["ollama"] = 1
    assert any("ollama" in e for e in errores(d))


def test_limits_tiene_que_ser_positivo():
    d = registry_dict()
    d["limits"]["max_parallel"]["codex"] = 0
    assert errores(d)


def test_backends_yaml_real_es_valido():
    cfg = parse_backends(load_yaml(REPO / "backends.yaml"))
    assert set(cfg.backends) == {"codex", "agy", "claude"}
    assert cfg.backends["agy"].modes["read-only"].os_sandbox == "readonly"
    assert cfg.backends["claude"].native is True
