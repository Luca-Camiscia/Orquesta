"""Descubrimiento de modelos sin red ni CLIs reales."""
import json
import subprocess

import pytest

from orquesta import modelos


def test_codex_cache_modelos_y_niveles(tmp_path):
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [
        {"slug": "gpt-6.1-sol", "display_name": "GPT Sol",
         "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}],
         "default_reasoning_level": "high"},
        {"slug": "gpt-other"}]}))
    assert modelos.read_codex_models(cache) == [
        {"id": "gpt-6.1-sol", "display_name": "GPT Sol", "reasoning_levels": ["low", "high"],
         "default_reasoning_level": "high"},
        {"id": "gpt-other", "display_name": "gpt-other", "reasoning_levels": [],
         "default_reasoning_level": None}]


@pytest.mark.parametrize("content", [None, "invalid JSON", '{"models": null}'])
def test_codex_cache_ausente_o_invalido(tmp_path, content):
    cache = tmp_path / "models_cache.json"
    if content is not None:
        cache.write_text(content)
    assert modelos.read_codex_models(cache) == []


def test_agy_parsea_tabs_e_ignora_avisos():
    assert modelos.parse_agy_models("Aviso sin tab\ngemini-pro-high\tGemini Pro High\n\n") == [
        {"id": "gemini-pro-high", "display_name": "Gemini Pro High", "reasoning_levels": [],
         "default_reasoning_level": None}]


def test_agy_timeout_comando_inyectado_cache_y_expiracion(tmp_path):
    calls = []
    now = [0.0]
    def run(command, **kwargs):
        assert command == ["fake-agy", "models"]
        assert kwargs == {"capture_output": True, "text": True, "timeout": 20, "check": True}
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, f"gemini-{len(calls)}\tGemini\n")
    discovery = modelos.ModelDiscovery(codex_cache=tmp_path / "missing",
        agy_command=["fake-agy", "models"], run=run, clock=lambda: now[0])
    assert discovery.get_models()["agy"][0]["id"] == "gemini-1"
    now[0] = 599
    assert discovery.get_models()["agy"][0]["id"] == "gemini-1"
    now[0] = 600
    assert discovery.get_models()["agy"][0]["id"] == "gemini-2"
    assert len(calls) == 2


@pytest.mark.parametrize("error", [FileNotFoundError(), subprocess.TimeoutExpired("agy", 20),
                                   subprocess.CalledProcessError(1, "agy")])
def test_agy_no_disponible_no_rompe_otros_catalogos(tmp_path, error):
    def run(*args, **kwargs):
        raise error
    discovery = modelos.ModelDiscovery(codex_cache=tmp_path / "missing", run=run)
    found = discovery.get_models()
    assert found["agy"] == [] and found["codex"] == []
    assert {m["id"] for m in found["claude"]} >= {"haiku", "sonnet", "opus", "inherit"}
