"""API de la UI local: seguridad, edición del registro (preservando comentarios) y flujos."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from helpers import dump_yaml, git, load_yaml
from orquesta import chat, doctor, migracion
from orquesta.generate import apply_plan, build_plan
from orquesta.ui.app import INDEX, create_app

TOKEN = "token-de-prueba"
BASE = "http://127.0.0.1:8787"


@pytest.fixture
def app_env(env):
    comentario = "# comentario del usuario que no se tiene que perder\n"
    env.registry.write_text(comentario + env.registry.read_text())
    git(env.home, "commit", "-qam", "comentario")
    apply_plan(build_plan(env), "deploy", run_tests=None)
    return env


@pytest.fixture
def client(app_env):
    app = create_app(app_env, port=8787, token=TOKEN, run_tests=None)
    return TestClient(app, base_url=BASE, headers={"X-Orquesta-Token": TOKEN})


def estado(client):
    r = client.get("/api/state")
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_manifest_error_devuelve_json_sin_traceback(client, app_env, fase):
    if fase == "legacy":
        app_env.manifest.write_text("invalid", encoding="utf-8")
    if fase == "plugin":
        app_env.plugin_manifest.unlink(missing_ok=True)
    migracion.set_fase(app_env, fase)
    # generate tiene que poder reparar un manifiesto roto (línea base desde HEAD)
    assert client.post("/api/apply/dry-run", json={}).status_code == 200
    r = client.get("/api/state")
    assert r.status_code == 500
    errores = r.json()["errors"]
    assert f"manifiesto de la fase {fase}" in errores[0] and "Traceback" not in errores[0]
    assert r.headers["x-frame-options"] == "DENY"


def agente(client, nombre):
    return next(a for a in estado(client)["agents"] if a["name"] == nombre)


def guardar(client, a, **extra):
    s = estado(client)
    return client.put(f"/api/agents/{a['name']}", json={"agent": a, "expected_hash": s["yaml_hash"], **extra})


# --- seguridad -------------------------------------------------------------------------------

def test_host_ajeno_se_rechaza(client):
    assert client.get("/api/state", headers={"Host": "evil.example:8787"}).status_code == 400


def test_index_inyecta_el_token_y_bloquea_iframes(client):
    r = client.get("/")
    assert r.status_code == 200 and TOKEN in r.text
    assert r.headers["x-frame-options"] == "DENY"


def test_escritura_sin_token_se_rechaza(app_env):
    c = TestClient(create_app(app_env, port=8787, token=TOKEN, run_tests=None), base_url=BASE)
    r = c.post("/api/apply/dry-run", json={})
    assert r.status_code == 403 and "token" in r.json()["detail"]


def test_origin_ajeno_se_rechaza(client):
    r = client.post("/api/apply/dry-run", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


# --- estado y edición ------------------------------------------------------------------------

def test_modelos_endpoint_con_token_y_protecciones(client, tmp_path, monkeypatch):
    from orquesta import modelos
    cache = tmp_path / "models_cache.json"
    cache.write_text(json.dumps({"models": [{"slug": "gpt-6.1-sol",
        "display_name": "GPT Sol", "supported_reasoning_levels": [{"effort": "high"}],
        "default_reasoning_level": "high"}]}))
    discovery = modelos.ModelDiscovery(codex_cache=cache,
        run=lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "gemini-high\tGemini High\n"))
    monkeypatch.setattr(modelos.ModelDiscovery, "get_models", discovery.get_models)
    r = client.get("/api/modelos")
    assert r.status_code == 200, r.text
    assert r.json()["codex"][0] == {"id": "gpt-6.1-sol", "display_name": "GPT Sol",
        "reasoning_levels": ["high"], "default_reasoning_level": "high"}
    assert r.json()["agy"][0]["id"] == "gemini-high"
    assert {m["id"] for m in r.json()["claude"]} >= {"haiku", "sonnet", "opus", "inherit"}
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/modelos", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/modelos", headers={"Origin": "http://evil.example"}).status_code == 403


def test_ui_guarda_backend_effort_en_yaml(client, app_env):
    backends = load_yaml(app_env.backends)
    backends["backends"]["codex"]["effort_flag"] = ["-c", "model_reasoning_effort={effort}"]
    dump_yaml(backends, app_env.backends)
    a = agente(client, "codex-critico")
    a.update(backend_model="gpt-6.1-sol", backend_effort="high")
    r = guardar(client, a)
    assert r.status_code == 200, r.text
    assert agente(client, a["name"])["backend_effort"] == "high"
    assert load_yaml(app_env.registry)["agents"][2]["backend_effort"] == "high"


def test_state_trae_registro_hash_y_catalogos(client):
    s = estado(client)
    assert s["yaml_hash"] and s["orchestrator"]["default_mood"] == "transparente"
    assert {a["name"] for a in s["agents"]} == {"gemini-lector", "codex-forja", "codex-critico", "mantenedor"}
    assert "Read" in s["tools"] and "codex" in s["backends"]
    forja = agente(client, "codex-forja")
    assert forja["effective_isolation"] == "worktree" and forja["deployed"] is True


def test_editar_agente_preserva_comentarios(client, app_env):
    a = agente(client, "codex-critico")
    a["role"] = "Review adversarial, ahora más malo."
    r = guardar(client, a)
    assert r.status_code == 200, r.text
    texto = app_env.registry.read_text()
    assert "# comentario del usuario que no se tiene que perder" in texto
    assert load_yaml(app_env.registry)["agents"][2]["role"] == "Review adversarial, ahora más malo."


def test_crear_agente_nuevo(client, app_env):
    nuevo = {"name": "gemini-cronista", "backend": "agy", "role": "Documentación.", "tools": ["Bash"],
             "mode": "read-only", "enabled": True, "route_when": ["hay que documentar"],
             "prompt": "Sos el cronista.\nEscribí docs claras."}
    r = guardar(client, nuevo)
    assert r.status_code == 200, r.text
    data = load_yaml(app_env.registry)
    assert data["agents"][-1]["name"] == "gemini-cronista"
    assert data["agents"][-1]["prompt"] == "Sos el cronista.\nEscribí docs claras."
    assert "prompt: |" in app_env.registry.read_text()


def test_agente_invalido_devuelve_errores_legibles(client):
    a = agente(client, "codex-critico")
    a["tools"] = ["Bash", "Glob"]
    r = guardar(client, a)
    assert r.status_code == 422 and any("Glob" in e for e in r.json()["errors"])


def test_hash_viejo_da_conflicto(client):
    a = agente(client, "codex-critico")
    r = client.put("/api/agents/codex-critico", json={"agent": a, "expected_hash": "viejo"})
    assert r.status_code == 409 and "cambió" in r.json()["detail"]


def test_editar_protegido_requiere_confirmacion(client):
    a = agente(client, "mantenedor")
    a["role"] = "Otro rol"
    r = guardar(client, a)
    assert r.status_code == 409 and r.json()["needs_confirmation"] is True
    assert guardar(client, a, confirm="mantenedor").status_code == 200


def test_toggle_enabled(client, app_env):
    a = agente(client, "codex-critico")
    a["enabled"] = False
    assert guardar(client, a).status_code == 200
    assert load_yaml(app_env.registry)["agents"][2]["enabled"] is False


def test_duplicar_y_renombrar(client, app_env):
    s = estado(client)
    r = client.post("/api/agents/codex-critico/duplicate", json={"expected_hash": s["yaml_hash"]})
    assert r.status_code == 200 and r.json()["name"] == "codex-critico-copia"
    copia = agente(client, "codex-critico-copia")
    assert copia["enabled"] is False and not copia.get("protected")
    s = estado(client)
    r = client.post("/api/agents/codex-critico-copia/rename",
                    json={"new_name": "codex-critico-dos", "expected_hash": s["yaml_hash"]})
    assert r.status_code == 200
    assert "codex-critico-dos" in [a["name"] for a in load_yaml(app_env.registry)["agents"]]


def test_renombrar_a_nombre_invalido_falla(client):
    s = estado(client)
    r = client.post("/api/agents/codex-critico/rename",
                    json={"new_name": "Nombre Malo", "expected_hash": s["yaml_hash"]})
    assert r.status_code == 422


def test_eliminar_protegido_requiere_confirmacion(client, app_env):
    s = estado(client)
    r = client.request("DELETE", "/api/agents/mantenedor", json={"expected_hash": s["yaml_hash"]})
    assert r.status_code == 409
    r = client.request("DELETE", "/api/agents/mantenedor",
                       json={"expected_hash": s["yaml_hash"], "confirm": "mantenedor"})
    assert r.status_code == 200
    assert "mantenedor" not in [a["name"] for a in load_yaml(app_env.registry)["agents"]]


def test_preview_del_md_en_vivo(client):
    a = agente(client, "codex-forja")
    a["role"] = "Rol de prueba para preview"
    r = client.post("/api/preview/agent", json={"agent": a})
    assert r.status_code == 200 and "Rol de prueba para preview" in r.json()["md"]
    assert r.json()["md"].startswith("---\nname: codex-forja\n")


def test_raw_md_marca_manual(client, app_env):
    s = estado(client)
    contenido = "---\nname: codex-critico\ndescription: hecho a mano\ntools: Bash\n---\n\nCuerpo manual\n"
    r = client.post("/api/agents/codex-critico/raw",
                    json={"content": contenido, "expected_hash": s["yaml_hash"]})
    assert r.status_code == 200 and "manual" in r.json()["warning"]
    assert (app_env.manual_dir / "codex-critico.md").read_text() == contenido
    assert load_yaml(app_env.registry)["agents"][2]["manual"] is True
    assert client.get("/api/agents/codex-critico/raw").json()["content"] == contenido


def test_raw_md_con_otro_name_se_rechaza(client):
    s = estado(client)
    r = client.post("/api/agents/codex-critico/raw",
                    json={"content": "---\nname: otro\n---\nx\n", "expected_hash": s["yaml_hash"]})
    assert r.status_code == 422


# --- moods y orquestación --------------------------------------------------------------------

def test_no_se_puede_borrar_el_mood_default(client):
    s = estado(client)
    r = client.post("/api/moods", json={"action": "delete", "name": "transparente",
                                        "expected_hash": s["yaml_hash"]})
    assert r.status_code == 422 and "default" in r.json()["errors"][0]


def test_cambiar_default_y_preview_del_bloque(client, app_env):
    s = estado(client)
    r = client.put("/api/orchestrator", json={"default_mood": "debate", "expected_hash": s["yaml_hash"]})
    assert r.status_code == 200
    assert load_yaml(app_env.registry)["orchestrator"]["default_mood"] == "debate"
    block = client.post("/api/preview/block", json={}).json()["block"]
    assert "Mood por defecto: **debate**" in block


def test_agregar_y_renombrar_mood(client, app_env):
    s = estado(client)
    assert client.post("/api/moods", json={"action": "add", "name": "telegrafico",
                                           "description": "Una línea.",
                                           "expected_hash": s["yaml_hash"]}).status_code == 200
    s = estado(client)
    assert client.post("/api/moods", json={"action": "rename", "name": "telegrafico",
                                           "new_name": "breve",
                                           "expected_hash": s["yaml_hash"]}).status_code == 200
    assert "breve" in load_yaml(app_env.registry)["orchestrator"]["moods"]


def test_toggle_de_orquestacion(client, app_env):
    s = estado(client)
    assert client.put("/api/orchestrator", json={"enabled": False,
                                                  "expected_hash": s["yaml_hash"]}).status_code == 200
    assert load_yaml(app_env.registry)["orchestrator"]["enabled"] is False


# --- aplicar ---------------------------------------------------------------------------------

def test_dry_run_y_aplicar(client, app_env):
    a = agente(client, "codex-critico")
    a["enabled"] = False
    guardar(client, a)
    d = client.post("/api/apply/dry-run", json={}).json()
    assert not d["empty"] and "codex-critico.md" in d["diff"] and [v["kind"] for v in d["violations"]] == ["baja"]
    r = client.post("/api/apply", json={"message": "Deshabilito el crítico", "confirm": ["codex-critico"]})
    assert r.status_code == 200 and r.json()["applied"] is True
    assert "Reiniciá la sesión de Claude Code" in r.json()["note"]
    assert not (app_env.agents_dir / "codex-critico.md").exists()
    assert git(app_env.home, "log", "-1", "--format=%s") == "Deshabilito el crítico"


def test_aplicar_cambio_protegido_pide_confirmacion(client):
    a = agente(client, "mantenedor")
    a["role"] = "Rol nuevo"
    guardar(client, a, confirm="mantenedor")
    d = client.post("/api/apply/dry-run", json={}).json()
    assert [v["agent"] for v in d["violations"]] == ["mantenedor"]
    r = client.post("/api/apply", json={"message": "x"})
    assert r.status_code == 409
    r = client.post("/api/apply", json={"message": "confirmado", "confirm": ["mantenedor"]})
    assert r.status_code == 200 and r.json()["applied"]


def test_descartar_cambios_sin_aplicar(client, app_env):
    a = agente(client, "codex-critico")
    a["role"] = "cambio que voy a descartar"
    guardar(client, a)
    assert client.post("/api/discard", json={"confirm": True}).status_code == 200
    assert load_yaml(app_env.registry)["agents"][2]["role"] == "Review adversarial."


# --- otros paneles ---------------------------------------------------------------------------

def test_probar_agente_usa_el_runner(client):
    r = client.post("/api/agents/codex-critico/test")
    assert r.status_code == 200
    assert r.json()["status"] == "OK" and "PONG" in r.json()["output"]


def test_historial_y_restaurar_version(client, app_env):
    commits = client.get("/api/history").json()["commits"]
    assert commits[0]["message"] == "deploy"
    viejo = next(c for c in commits if c["message"] == "init")
    a = agente(client, "codex-critico")
    a["role"] = "Cambio aplicado"
    guardar(client, a)
    # Cambiar el role pide confirmación (va al .md del agente): en la UI la da el clic del usuario.
    assert client.post("/api/apply", json={"message": "cambio"}).status_code == 409
    assert client.post("/api/apply", json={"message": "cambio", "confirm": ["codex-critico"]}).json()["applied"]
    s = estado(client)
    r = client.post(f"/api/history/{viejo['sha']}/restore", json={"expected_hash": s["yaml_hash"]})
    assert r.status_code == 200 and "Cambio aplicado" in r.json()["diff"]
    assert load_yaml(app_env.registry)["agents"][2]["role"] == "Review adversarial."


def test_changelog(client):
    assert "deploy" in client.get("/api/changelog").json()["content"]


def test_plugins_lista_sus_agentes(client, app_env):
    inst = app_env.claude_dir / "plugins" / "cache" / "x" / "codex" / "1.0"
    (inst / "agents").mkdir(parents=True)
    (inst / "agents" / "codex-rescue.md").write_text(
        "---\nname: codex-rescue\ndescription: Rescata\nmodel: sonnet\ntools: Bash\n---\ncuerpo\n")
    (app_env.claude_dir / "plugins" / "installed_plugins.json").write_text(json.dumps(
        {"version": 2, "plugins": {"codex@openai-codex": [
            {"installPath": str(inst), "version": "1.0", "gitCommitSha": "a" * 40}]}}))
    p = client.get("/api/plugins").json()["plugins"][0]
    assert p["id"] == "codex@openai-codex"
    assert p["agents"][0]["name"] == "codex:codex-rescue"


def test_comando_para_llamar_a_that_it_guy(client):
    r = client.post("/api/that-it-guy", json={"name": "Auth de codex",
                                              "message": "codex: sin sesión o vencida"})
    cmd = r.json()["command"]
    assert cmd.startswith("claude ")
    assert "Usá el subagent that-it-guy para resolver: Auth de codex — codex: sin sesión" in cmd


def test_salud_usa_doctor(client, monkeypatch):
    rep = doctor.Report([doctor.Check("git", "Repo", "warn", "sucio", "commiteá")], "ahora")
    monkeypatch.setattr("orquesta.doctor.run_doctor", lambda *a, **k: rep)
    d = client.get("/api/health").json()
    assert d["overall"] == "warn" and d["checks"][0]["id"] == "git"
    assert client.post("/api/health/refresh").json()["overall"] == "warn"


def test_reordenar_las_mismas_tools_no_cambia_el_yaml(client, app_env):
    antes = app_env.registry.read_text()
    a = agente(client, "mantenedor")
    a["tools"] = sorted(a["tools"])  # mismo conjunto, otro orden (como lo manda un formulario)
    assert guardar(client, a, confirm="mantenedor").status_code == 200
    assert app_env.registry.read_text() == antes


# --- tareas y conversaciones de solo lectura -------------------------------------------------

@pytest.mark.parametrize("method", ["POST", "DELETE", "PATCH", "PUT"])
@pytest.mark.parametrize("path", [
    "/api/chat/projects", "/api/chat/projects/orquesta",
    "/api/projects", "/api/projects/orquesta",
    "/api/chat/conversations", "/api/chat/conversations/vieja",
    "/api/chat/conversations/vieja/participants", "/api/chat/conversations/vieja/messages",
    "/api/chat/conversations/vieja/cancel", "/api/chat/conversations/vieja/export.md",
    "/api/chat/conversations/vieja/worktrees/merge",
    "/api/chat/conversations/vieja/worktrees/discard",
    "/api/worktrees/merge", "/api/worktrees/discard",
])
def test_endpoints_de_escritura_eliminados(client, method, path):
    r = client.request(method, path, json={"confirm": True})
    assert r.status_code in (404, 405), r.text


@pytest.mark.parametrize("path", [
    "/api/chat/participants", "/api/chat/search?q=vieja", "/api/chat/conversations/vieja",
])
def test_get_exclusivo_del_chat_eliminado(client, path):
    assert client.get(path).status_code == 404


def test_listados_y_export_de_conversaciones_viejas(client, app_env):
    store = chat.Store(app_env)
    pr = store.create_project("Proyecto viejo", str(app_env.home))
    conv = store.create_conversation(pr["id"], "Charla vieja", "individual", ["codex-critico"], origen="ui")
    sin_proyecto = store.create_conversation(None, "Sin proyecto", "individual", ["codex-critico"])
    store.update(conv["id"], lambda v: v["messages"].append(
        {"author": "usuario", "text": "Mensaje guardado", "meta": {}}))
    projects = client.get("/api/chat/projects")
    assert projects.status_code == 200
    assert pr["id"] in {p["id"] for p in projects.json()["projects"]}
    lista = client.get("/api/chat/conversations")
    assert lista.status_code == 200
    assert {v["id"] for v in lista.json()["conversations"]} == {conv["id"], sin_proyecto["id"]}
    filtrada = client.get("/api/chat/conversations", params={"project": pr["id"]})
    assert [v["id"] for v in filtrada.json()["conversations"]] == [conv["id"]]
    for filtro in ("sin-proyecto", "none", ""):
        r = client.get("/api/chat/conversations", params={"project": filtro})
        assert [v["id"] for v in r.json()["conversations"]] == [sin_proyecto["id"]]
    export = client.get(f'/api/chat/conversations/{conv["id"]}/export.md')
    assert export.status_code == 200
    assert export.text.startswith("# Charla vieja") and "Mensaje guardado" in export.text
    assert export.headers["content-type"].startswith("text/markdown")
    assert 'attachment; filename="charla-vieja.md"' == export.headers["content-disposition"]
    assert client.get("/api/chat/conversations/inexistente/export.md").status_code == 422


def test_tareas_se_pueden_listar(client):
    r = client.get("/api/worktrees")
    assert r.status_code == 200 and r.json() == {"worktrees": []}


@pytest.mark.skipif(shutil.which("node") is None, reason="Node no disponible para probar el JS")
def test_tareas_default_comandos_y_copia_sin_escritura():
    script = INDEX.read_text().split("<script>", 1)[1].split("</script>", 1)[0]
    script = script.rsplit("cargar().then(", 1)[0]
    setup = r'''
const assert = require("node:assert/strict");
const listeners = {}, nodes = {}, copiados = [];
const document = {
  documentElement: {removeAttribute() {}, setAttribute() {}, getAttribute() {return null;}},
  querySelector: (key) => nodes[key] ||= {content: "test", innerHTML: "", value: "",
    addEventListener() {}, classList: {add() {}, remove() {}}},
  querySelectorAll: () => [],
  addEventListener: (event, fn) => (listeners[event] ||= []).push(fn)
};
Object.defineProperty(globalThis, "navigator", {value: {clipboard: {
  writeText: async (text) => copiados.push(text)
}}});
'''
    probe = r'''
(async () => {
st.data = {agents: [{name: "forja", backend: "codex"}], pending: {changes: 0}};
renderPestanas();
assert.match(nodes["#pestanas"].innerHTML, /aria-selected="true" data-tab="tareas"/);
assert.ok(!nodes["#pestanas"].innerHTML.includes("Conversaciones"));
assert.ok(!comandosPaleta().some(c => /conversaci/i.test(c.etiqueta)));
st.tareas = [{branch: "orquesta/forja/cambio", agent: "forja", task: "Cambio <viejo>",
  repo: "/tmp/repo de Luca's $(echo sorpresa)", huerfano: null, diffstat: "1 file changed",
  conflictos: {merge_conflict: false, overlaps: []}}];
const html = panelTareas();
assert.match(html, /Cambio &lt;viejo&gt;/);
assert.ok(!html.includes('data-action="t-merge"'));
assert.ok(!html.includes('data-action="t-descartar"'));
const botones = [...html.matchAll(/<button[^>]*data-action="t-copiar"[^>]*data-command="([^"]+)"[^>]*>Copiar comando<\/button>/g)];
assert.equal(botones.length, 2);
const decode = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
api = async () => { throw new Error("Copiar no debe llamar al backend"); };
toast = () => {};
for (const [, encoded] of botones) {
  const command = decode(encoded);
  assert.ok(html.includes(`<code class="cmd">${encoded}</code>`));
  await listeners.click[0]({target: {closest: () => ({dataset: {action: "t-copiar", command}})}});
}
st.tareas = []; assert.match(panelTareas(), /No hay tareas abiertas/);
process.stdout.write(JSON.stringify(copiados));
})().catch(e => {console.error(e); process.exitCode = 1;});
'''
    result = subprocess.run(["node", "-"], input=setup + script + probe, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert [shlex.split(c) for c in json.loads(result.stdout)] == [
        ["orquesta", "review", "orquesta/forja/cambio", "--repo", "/tmp/repo de Luca's $(echo sorpresa)"],
        ["orquesta", "merge", "orquesta/forja/cambio", "--repo", "/tmp/repo de Luca's $(echo sorpresa)", "--confirm"],
    ]
