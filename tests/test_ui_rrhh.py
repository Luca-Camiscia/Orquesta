"""Endpoints RR. HH. y comportamiento del panel vanilla, sin consumir cuota."""
import json
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from orquesta import modelos
from orquesta.chat import Store
from orquesta.ui.app import INDEX, create_app

TOKEN = "rrhh-test"


def client(env):
    return TestClient(create_app(env, token=TOKEN, run_tests=None), base_url="http://127.0.0.1:8787",
                      headers={"X-Orquesta-Token": TOKEN})


def test_endpoints_vacios_protegidos_y_sin_cli(env, monkeypatch):
    def prohibido(*args, **kwargs):
        raise AssertionError("RR. HH. no debe ejecutar descubrimiento ni backends")
    monkeypatch.setattr(modelos.subprocess, "run", prohibido)
    c = client(env)
    for path in ("/api/rrhh/evaluacion", "/api/rrhh/catalogo"):
        r = c.get(path)
        assert r.status_code == 200 and r.headers["x-frame-options"] == "DENY"
        assert c.get(path, headers={"Host": "evil.example"}).status_code == 400
        assert c.get(path, headers={"Origin": "http://evil.example"}).status_code == 403
    assert all(a["nota"] is None for a in c.get("/api/rrhh/evaluacion").json()["agentes"])
    r = c.post("/api/rrhh/triage", json={"texto": "fix typo"})
    assert r.status_code == 200 and r.json()["recomendados"][0]["name"] == "codex-forja"
    assert c.post("/api/rrhh/triage", json={"texto": "fix"},
                  headers={"X-Orquesta-Token": "incorrecto"}).status_code == 403
    for payload in ({}, {"texto": "  "}, {"texto": 12}):
        assert c.post("/api/rrhh/triage", json=payload).status_code == 422


def test_sugerencias_y_agregar_conv_individual(env, monkeypatch):
    # Participantes desplegados, sin generar ni ejecutar agentes.
    monkeypatch.setattr(Store, "participants", lambda self: ["codex-forja", "gemini-lector"])
    c = client(env)
    pr = c.post("/api/chat/projects", json={"name": "p", "dir": str(env.home)}).json()
    conv = c.post("/api/chat/conversations", json={"project_id": pr["id"],
                  "participants": ["codex-forja"]}).json()
    Store(env).update(conv["id"], lambda v: v["messages"].append(
        {"author": "usuario", "text": "leer repo entero", "meta": {}}))
    r = c.get(f'/api/rrhh/sugerencias/{conv["id"]}')
    assert r.status_code == 200 and r.json()["sugerencias"][0]["name"] == "gemini-lector"
    r = c.post(f'/api/chat/conversations/{conv["id"]}/participants',
               json={"participants": ["codex-forja", "gemini-lector"]})
    assert r.status_code == 200 and r.json()["kind"] == "reunion"
    assert c.get(f'/api/rrhh/sugerencias/{conv["id"]}').json()["sugerencias"] == []
    assert c.get("/api/rrhh/sugerencias/invalid").status_code == 422


def test_reclutador_ausente_preserva_base(env, monkeypatch):
    from orquesta import runner
    monkeypatch.setattr(runner, "run", lambda *a: (_ for _ in ()).throw(AssertionError("sin fallback")))
    r = client(env).post("/api/rrhh/reclutador", json={"texto": "fix typo"})
    assert r.status_code == 200
    assert r.json()["base"]["recomendados"][0]["name"] == "codex-forja"
    assert r.json()["opinion"] is None and r.json()["error"]


def test_reclutador_validacion_y_middleware(env, monkeypatch):
    from orquesta import runner
    monkeypatch.setattr(runner, "run", lambda *a: (_ for _ in ()).throw(AssertionError("no backend")))
    c = client(env)
    for payload in ({}, {"texto": " "}, {"texto": 12}, {"texto": False}, {"texto": "x" * 12001}):
        assert c.post("/api/rrhh/reclutador", json=payload).status_code == 422
    for headers, status in (({"X-Orquesta-Token": "incorrecto"}, 403),
                            ({"Host": "evil.example"}, 400), ({"Origin": "http://evil.example"}, 403)):
        assert c.post("/api/rrhh/reclutador", json={"texto": "fix"}, headers=headers).status_code == status


def test_reclutador_explicito_sin_discovery(env, monkeypatch):
    from test_reclutador import preparar
    from orquesta import runner
    preparar(env)
    monkeypatch.setattr(modelos.ModelDiscovery, "get_models", lambda *a: (_ for _ in ()).throw(
        AssertionError("No debe descubrir modelos remotamente")))
    calls = []
    def run(req, paths):
        calls.append(req)
        return runner.RunResult("RATE_LIMIT", detail="cuota de Gemini agotada", latency_s=2)
    monkeypatch.setattr(runner, "run", run)
    c = client(env)
    for path in ("/api/rrhh/evaluacion", "/api/rrhh/catalogo"):
        assert c.get(path).status_code == 200
    assert c.post("/api/rrhh/triage", json={"texto": "fix typo"}).status_code == 200
    assert calls == []
    r = c.post("/api/rrhh/reclutador", json={"texto": "fix typo"})
    assert r.status_code == 200 and r.headers["x-frame-options"] == "DENY"
    assert len(calls) == 1
    assert r.json()["estado"] == "RATE_LIMIT" and r.json()["latencia_s"] == 2
    assert r.json()["base"]["razones"] and r.json()["opinion"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="Node no disponible para probar el JS")
def test_panel_reclutador_explicito_stale_cuota_y_escape():
    script = INDEX.read_text().split("<script>", 1)[1].split("</script>", 1)[0]
    script = script.rsplit("cargar().then(", 1)[0]
    setup = r'''
const assert = require("node:assert/strict");
const listeners = {}, nodes = {};
const document = {
  documentElement: {removeAttribute() {}, setAttribute() {}, getAttribute() {return null;}},
  querySelector: (key) => nodes[key] ||= {content: "test", innerHTML: "", value: "",
    addEventListener() {}, classList: {add() {}, remove() {}}},
  querySelectorAll: () => [],
  addEventListener: (event, fn) => (listeners[event] ||= []).push(fn)
};
'''
    probe = r'''
(async () => {
st.tab = "rrhh"; st.data = {agents: []};
st.rrhh.evaluacion = []; st.rrhh.catalogo = {categorias: {}, huecos: [], solapamientos: []};
renderPanel = () => {};
const calls = [], pending = [];
api = (method, path, body) => {
  calls.push({method, path, body});
  if (method === "GET") return Promise.resolve(path.endsWith("evaluacion") ? {agentes: []} : st.rrhh.catalogo);
  return new Promise((resolve, reject) => pending.push({resolve, reject}));
};
const input = async (text) => {
  document.querySelector("#rrhh-texto").value = text;
  for (const fn of listeners.input) await fn({target: {id: "rrhh-texto", value: text}});
};
const click = () => listeners.click[0]({target: {closest: () => ({dataset: {action: "rrhh-reclutador"}})}});
const submit = () => listeners.submit[0]({target: {id: "rrhh-triage"}, preventDefault() {}});
const base = {dificultad: 1, razones: ["Base vigente"], recomendados: [], avisos: [], revisor: null};
let html = panelRRHH();
assert.match(html, /type="button"[^>]*data-action="rrhh-reclutador"/);
assert.match(html, /Consume cuota de Gemini\. El triage determinista sigue siendo la base\./);
await cargarRRHH();
assert.equal(calls.filter(c => c.path.endsWith("reclutador")).length, 0);
await input("Tarea A");
const triageA = submit();
assert.equal(calls.at(-1).path, "/api/rrhh/triage");
const opinionA = click(); await click();
assert.equal(calls.filter(c => c.path.endsWith("reclutador")).length, 1);
assert.match(panelRRHH(), /data-action="rrhh-reclutador"[^>]*disabled/);
await input("Tarea B");
pending[1].resolve({texto: "Tarea A", base, opinion: "OPINION VIEJA", estado: "OK", latencia_s: 1, error: null});
await opinionA;
assert.equal(st.rrhh.triage, null);
assert.ok(!panelRRHH().includes("OPINION VIEJA"));
assert.equal(st.rrhh.consultas[0].texto, "Tarea A");
assert.equal(st.rrhh.consultas[0].estado, "OK");
const opinionB = click();
pending[2].resolve({texto: "Tarea B", base, opinion: "<img src=x onerror=evil()>&", estado: "OK", latencia_s: 2, error: null});
await opinionB;
pending[0].resolve({...base, razones: ["TRIAGE VIEJO"]}); await triageA;
html = panelRRHH();
assert.match(html, /Base vigente/); assert.ok(!html.includes("TRIAGE VIEJO"));
assert.ok(!html.includes("<img src=x")); assert.match(html, /&lt;img/);
await input("Tarea C");
assert.equal(st.rrhh.opinion, null);
assert.equal(document.querySelector("#rrhh-resultados").innerHTML, "");
const triageC = submit(); pending[3].resolve(base); await triageC;
assert.equal(calls.filter(c => c.path.endsWith("reclutador")).length, 2);
const quota = click();
pending[4].resolve({texto: "Tarea C", base, opinion: null, estado: "RATE_LIMIT", latencia_s: 3, error: "Cuota agotada <quota>"});
await quota; html = panelRRHH();
assert.match(html, /Base vigente/); assert.match(html, /RATE_LIMIT/);
assert.match(html, /Cuota agotada &lt;quota&gt;/);
assert.ok(!html.includes("&lt;img"));
const failure = click(); pending[5].reject(new Error("Red <offline>")); await failure;
html = panelRRHH(); assert.match(html, /Red &lt;offline&gt;/); assert.match(html, /Base vigente/);
assert.equal(st.rrhh.consultando, false);
process.stdout.write(JSON.stringify({consultas: st.rrhh.consultas.map(c => c.texto)}));
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    result = subprocess.run(["node", "-"], input=setup + script + probe, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["consultas"] == ["Tarea A", "Tarea B", "Tarea C", "Tarea C"]
