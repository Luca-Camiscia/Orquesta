"""Consulta explícita: contexto local y preflight antes de consumir cuota."""
import importlib
import json

import pytest

from helpers import REPO, dump_yaml, load_yaml
from orquesta import runner
from orquesta.config import load_registry
from orquesta.generate import apply_plan, build_plan


def preparar(env):
    data = load_yaml(env.registry)
    data["agents"].append({
        "name": "gemini-reclutador", "enabled": True, "backend": "agy",
        "backend_model": "gemini-3.8-flash-high", "wrapper_model": "haiku",
        "mode": "read-only", "isolation": "none", "tools": ["Bash"],
        "role": "Recomienda equipo", "prompt": "Opinión consultiva, sin ejecutar ni editar."})
    dump_yaml(data, env.registry)
    apply_plan(build_plan(env), "deploy de prueba", run_tests=None)


@pytest.fixture
def desplegado(env):
    preparar(env)
    return env


def consultar(env, texto="Corregir typo", modelos=None):
    return importlib.import_module("orquesta.reclutador").consultar(texto, env, modelos or {})


def test_agente_del_registro_real():
    from orquesta.paths import Paths
    agent = load_registry(Paths(REPO, REPO)).agent("gemini-reclutador")
    assert agent is not None
    assert agent.enabled and not agent.protected
    assert (agent.backend, agent.backend_model, agent.effective_wrapper_model,
            agent.mode, agent.effective_isolation, agent.tools) == (
        "agy", "gemini-3.8-flash-high", "haiku", "read-only", "none", ["Bash"])


def test_contexto_local_y_una_llamada(desplegado, monkeypatch):
    calls = []
    def run(req, paths):
        calls.append(req)
        assert paths == desplegado and req.cwd == paths.home
        assert req.agent == "gemini-reclutador"
        contexto = json.loads(req.prompt.split("CONTEXTO LOCAL (JSON):\n", 1)[1])
        assert contexto["texto"] == "Corregir typo"
        assert contexto["base"]["recomendados"][0]["name"] == "codex-forja"
        assert contexto["plantel"] and contexto["catalogo"]["categorias"]
        assert contexto["modelos"]["codex"][0]["reasoning_levels"] == ["high"]
        a = next(a for a in contexto["registro"]["agents"] if a["name"] == "codex-forja")
        assert a["role"] and a["route_when"] and a["mode"] == "write"
        assert contexto["registro"]["limits"]["max_parallel"]["agy"] == 1
        for section in ("Dificultad", "Motivos", "Equipo", "Coincidencias y discrepancias"):
            assert section in req.prompt
        return runner.RunResult("OK", output="Dificultad: 1\nEquipo: codex-forja", latency_s=1.2)
    monkeypatch.setattr(runner, "run", run)
    result = consultar(desplegado, modelos={"codex": [{"id": "local", "reasoning_levels": ["high"]}]})
    assert len(calls) == 1
    assert result["texto"] == "Corregir typo" and result["estado"] == "OK"
    assert result["opinion"].startswith("Dificultad: 1") and result["latencia_s"] == 1.2
    assert result["base"]["dificultad"] <= 2 and result["error"] is None


@pytest.mark.parametrize("lado,campo,valor", [
    ("registro", "enabled", False), ("registro", "backend", "codex"),
    ("registro", "mode", "write"), ("registro", "isolation", "worktree"),
    ("registro", "backend_model", "otro"), ("registro", "wrapper_model", "sonnet"),
    ("aplicado", "backend", "codex"), ("aplicado", "mode", "write"),
    ("aplicado", "isolation", "worktree"), ("aplicado", "backend_model", "otro"),
    ("aplicado", "wrapper_model", "sonnet"), ("aplicado", "enabled", False),
])
def test_config_insegura_no_llama(desplegado, monkeypatch, lado, campo, valor):
    if lado == "registro":
        data = load_yaml(desplegado.registry)
        data["agents"][-1][campo] = valor
        dump_yaml(data, desplegado.registry)
    else:
        data = json.loads(desplegado.manifest.read_text())
        data["agents"]["gemini-reclutador"][campo] = valor
        desplegado.manifest.write_text(json.dumps(data))
    monkeypatch.setattr(runner, "run", lambda *a: pytest.fail("No debe consumir cuota"))
    result = consultar(desplegado)
    assert result["estado"] == "CONFIGURACION_INVALIDA" and result["opinion"] is None
    assert "correg" in result["error"].lower() and result["base"]["razones"]


@pytest.mark.parametrize("ausente", ["registro", "aplicado"])
def test_agente_ausente_no_hay_fallback(desplegado, monkeypatch, ausente):
    if ausente == "registro":
        data = load_yaml(desplegado.registry)
        data["agents"].pop()
        dump_yaml(data, desplegado.registry)
    else:
        desplegado.manifest.unlink()
    monkeypatch.setattr(runner, "run", lambda *a: pytest.fail("No hay fallback"))
    result = consultar(desplegado)
    assert result["opinion"] is None and result["base"]["razones"]
    assert result["estado"] == ("CONFIGURACION_INVALIDA" if ausente == "registro" else "NO_DESPLEGADO")
    assert "configur" in result["error"].lower() or "aplic" in result["error"].lower()


@pytest.mark.parametrize("texto", [None, 12, False, "", "  ", "x" * 12001])
def test_input_invalido_sin_backend(env, monkeypatch, texto):
    monkeypatch.setattr(runner, "run", lambda *a: pytest.fail("Input inválido no llama"))
    with pytest.raises(ValueError):
        consultar(env, texto)


@pytest.mark.parametrize("estado", ["RATE_LIMIT", "TIMEOUT"])
def test_error_preserva_base_sin_retry(desplegado, monkeypatch, estado):
    calls = []
    def run(*args):
        calls.append(args)
        return runner.RunResult(estado, output="respuesta incompleta", detail="falló la consulta", latency_s=3)
    monkeypatch.setattr(runner, "run", run)
    result = consultar(desplegado)
    assert result["estado"] == estado and result["latencia_s"] == 3
    assert result["opinion"] is None and result["error"] and result["base"]["razones"]
    assert len(calls) == 1


def test_orquesta_off_pasa_por_runner(desplegado, monkeypatch):
    (desplegado.home / ".orquesta-off").touch()
    monkeypatch.setattr(runner, "_execute", lambda *a: pytest.fail(".orquesta-off bloquea ejecución"))
    result = consultar(desplegado)
    assert result["estado"] == "ORQUESTA_OFF" and ".orquesta-off" in result["error"]
    assert result["base"]["razones"] and result["opinion"] is None


def test_runner_real_con_backend_falso(desplegado):
    data = load_yaml(desplegado.backends)
    data["backends"]["agy"]["prompt"]["max_arg_chars"] = 100000
    dump_yaml(data, desplegado.backends)
    result = consultar(desplegado, "ECHOARGS")
    assert result["estado"] == "OK", result
    output = json.loads(result["opinion"])
    assert output["args"][output["args"].index("--model") + 1] == "gemini-3.8-flash-high"
    assert output["cwd"] == str(desplegado.home)
    assert "MODO SOLO LECTURA" in output["prompt"]
