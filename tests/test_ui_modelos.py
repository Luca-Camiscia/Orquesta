"""Ejecuta las funciones reales del editor vanilla en Node, sin navegador ni red."""
import json
import shutil
import subprocess

import pytest

from orquesta.ui.app import INDEX


@pytest.mark.skipif(shutil.which("node") is None, reason="Node no disponible para probar el JS")
def test_selects_modelos_esfuerzo_y_badges():
    script = INDEX.read_text().split("<script>", 1)[1].split("</script>", 1)[0]
    # Las funciones se cargan sin iniciar la aplicación ni registrar acciones de la página.
    functions = script.split('document.addEventListener("click"', 1)[0]
    setup = 'const document = {querySelector: () => ({content: "test"}), addEventListener() {}};\n'
    probe = r'''
st.data = {models: ["haiku", "sonnet", "opus", "inherit"], tools: ["Bash"],
  backends: {codex: {label: "Codex"}, agy: {label: "AGY"}, claude: {label: "Claude"}},
  installed_plugins: [], frontmatter_fields: []};
st.modelos = {codex: [{id: "gpt-sol", display_name: "GPT Sol", reasoning_levels: ["low", "high"]}],
  agy: [{id: "gemini-high", display_name: "Gemini High", reasoning_levels: []}]};
st.ed = {nuevo: false};
const agent = {...agenteVacio(), name: "forja", backend_model: "gpt-sol", backend_effort: "high"};
process.stdout.write(JSON.stringify({
  model: opcionesModelo("codex", "gpt-sol"),
  unknown: opcionesModelo("codex", "old<&model"),
  effort: opcionesEsfuerzo("codex", "gpt-sol", "high"),
  unknownEffort: opcionesEsfuerzo("codex", "old-model", "ultra"),
  agyEffort: opcionesEsfuerzo("agy", "gemini-high", ""),
  editor: editorFormulario(agent),
  claudeEditor: editorFormulario({...agent, backend: "claude", wrapper_model: "claude-custom"}),
  badge: tarjeta(agent)
}));
'''
    result = subprocess.run(["node", "-"], input=setup + functions + probe,
                            text=True, capture_output=True, check=True)
    found = json.loads(result.stdout)
    assert 'value=""' in found["model"] and "default del CLI" in found["model"]
    assert 'value="gpt-sol" selected' in found["model"]
    assert 'value="old&lt;&amp;model" selected' in found["unknown"]
    assert 'value="low"' in found["effort"] and 'value="high" selected' in found["effort"]
    assert 'value="ultra" selected' in found["unknownEffort"]
    assert 'value="high"' not in found["agyEffort"]
    assert '<select id="f-bmodel" name="backend_model">' in found["editor"]
    assert '<select id="f-effort" name="backend_effort">' in found["editor"]
    assert 'value="claude-custom" selected' in found["claudeEditor"]
    assert 'for="f-wrapper">Modelo</label>' in found["claudeEditor"]
    assert 'id="f-backend-model" hidden' in found["claudeEditor"]
    assert 'id="f-backend-effort" hidden' in found["claudeEditor"]
    assert "gpt-sol · high" in found["badge"]
