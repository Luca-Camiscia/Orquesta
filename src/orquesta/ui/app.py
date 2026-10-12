"""UI local de orquesta: FastAPI + un solo index.html (vanilla JS, sin build).

Escucha SOLO en 127.0.0.1. Defensas: Host permitido (anti DNS-rebinding), token de sesión en toda
escritura (anti-CSRF), Origin permitido, CSP y X-Frame-Options. La UI nunca ejecuta agentes: hace
pings por backend y, para that_it_guy, solo muestra el comando para copiar.
"""
from __future__ import annotations

import re
import secrets
import shlex
import subprocess
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ruamel.yaml import YAML

from orquesta import catalog, doctor, editing
from orquesta import manifest as mf
from orquesta import chat, migracion, modelos, moods, reclutador, rrhh, runner
from orquesta import worktrees as worktrees_mod
from orquesta import plugins as plugins_mod
from orquesta.config import load_backends, load_registry, read_manual
from orquesta.generate import GenerateError, apply_plan, build_plan, run_test_suite
from orquesta.paths import Paths, default_paths
from orquesta.render import RenderContext, render_agent_md, render_block_inner
from orquesta.schema import Agent, ConfigError, check_registry, parse_registry

RESTART_NOTE = "Reiniciá la sesión de Claude Code para cargar los cambios."
INDEX = Path(__file__).with_name("index.html")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
COMPUTED = {"effective_isolation", "effective_wrapper_model", "deployed", "pending"}
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; connect-src 'self'; img-src 'self' data:; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _frontmatter(text: str) -> dict:
    m = re.match(r"---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    try:
        data = YAML(typ="safe", pure=True).load(m.group(1))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _clean_agent(raw: dict) -> dict:
    return {k: v for k, v in (raw or {}).items() if k not in COMPUTED}


def _with_defaults(raw: dict) -> dict:
    out = {k: raw.get(k, editing.DEFAULTS.get(k)) for k in editing.FIELD_ORDER}
    out["enabled"] = raw.get("enabled", True)
    out["mode"] = raw.get("mode", "read-only")
    out["tools"] = raw.get("tools") or []
    return out


def _agentes_desplegados(paths: Paths) -> Path:
    """Dónde quedan los .md desplegados: el plugin en fase `plugin`, ~/.claude/agents en `legacy`."""
    try:
        en_plugin = migracion.fase(paths) == "plugin"
    except ValueError:
        en_plugin = False
    return paths.plugin_dir / "agents" if en_plugin else paths.agents_dir


def create_app(paths: Paths | None = None, *, port: int = 8787, token: str | None = None,
               run_tests=run_test_suite, doctor_deps: doctor.Env | None = None) -> FastAPI:
    paths = paths or default_paths()
    token = token or secrets.token_urlsafe(24)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    app = FastAPI(title="orquesta", docs_url=None, redoc_url=None, openapi_url=None)
    model_discovery = modelos.ModelDiscovery()

    @app.middleware("http")
    async def security(request: Request, call_next):
        if request.headers.get("host", "") not in hosts:
            return JSONResponse({"detail": f"Host no permitido: abrí la UI desde http://127.0.0.1:{port}"},
                                status_code=400)
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
        if request.method not in SAFE_METHODS and request.headers.get("x-orquesta-token") != token:
            return JSONResponse({"detail": "Falta el token de sesión o no coincide: recargá la página."},
                                status_code=403)
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(editing.Conflict)
    async def _conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(mf.ManifestError)
    async def _manifest(_, exc):
        return JSONResponse({"errors": [f"{exc}; corré `orquesta doctor`"]}, status_code=500)

    @app.exception_handler(editing.NeedsConfirmation)
    async def _confirm(_, exc):
        return JSONResponse({"detail": str(exc), "needs_confirmation": True}, status_code=409)

    @app.exception_handler(editing.EditError)
    async def _edit(_, exc):
        return JSONResponse({"errors": exc.errors}, status_code=422)

    @app.exception_handler(chat.ChatError)
    async def _chat(_, exc):
        return JSONResponse({"errors": [str(exc)]}, status_code=422)

    @app.exception_handler(moods.MoodError)
    async def _mood(_, exc):
        return JSONResponse({"errors": [str(exc)]}, status_code=422)

    def render_ctx() -> RenderContext:
        return RenderContext(paths=paths, backends=load_backends(paths))

    def plan_json(plan) -> dict:
        return {"empty": plan.empty, "diff": plan.diff(), "summary": plan.summary,
                "errors": plan.errors,
                "violations": [{"agent": v.agent, "kind": v.kind, "detail": v.detail, "text": str(v)}
                               for v in plan.violations]}

    # --- página ------------------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = INDEX.read_text(encoding="utf-8").replace("__ORQUESTA_TOKEN__", token)
        return HTMLResponse(html, headers={"Content-Security-Policy": CSP})

    # --- estado ------------------------------------------------------------------------------

    @app.get("/api/modelos")
    def get_models():
        return model_discovery.get_models()

    @app.get("/api/state")
    def state():
        doc, yaml_hash = editing.read(paths)
        plain = editing.to_plain(doc) or {}
        errors = editing.validate(paths, doc)
        try:
            backends = {n: {"label": b.label, "native": b.native, "modes": list(b.modes)}
                        for n, b in load_backends(paths).backends.items()}
        except ConfigError as exc:
            backends, errors = {}, errors + exc.errors
        deployed = set(((mf.load(paths) or {}).get("agents") or {}).keys())
        plan = build_plan(paths)
        pending = {c.path.name for c in plan.changes}
        agents = []
        for raw in plain.get("agents") or []:
            a = _with_defaults(raw)
            try:
                parsed = Agent.model_validate(raw)
                a["effective_isolation"] = parsed.effective_isolation
                a["effective_wrapper_model"] = parsed.effective_wrapper_model
            except Exception:
                a["effective_isolation"] = raw.get("isolation") or "none"
                a["effective_wrapper_model"] = raw.get("wrapper_model") or "?"
            a["deployed"] = a["name"] in deployed
            a["pending"] = f"{a['name']}.md" in pending
            agents.append(a)
        return {
            "yaml_hash": yaml_hash,
            "orchestrator": plain.get("orchestrator") or {},
            "limits": plain.get("limits") or {},
            "agents": agents,
            "backends": backends,
            "tools": sorted(catalog.KNOWN_TOOLS),
            "frontmatter_fields": sorted(catalog.EXTRA_ALLOWED),
            "models": sorted(catalog.MODEL_ALIASES),
            "installed_plugins": sorted(plugins_mod.installed(paths)),
            "errors": errors,
            "pending": {"changes": len(plan.changes), "summary": plan.summary,
                        "violations": [v.agent for v in plan.violations]},
            "paths": {"home": paths.pretty(paths.home), "agents_dir": paths.pretty(_agentes_desplegados(paths)),
                      "claude_md": paths.pretty(paths.claude_md)},
        }

    # --- agentes -----------------------------------------------------------------------------

    @app.put("/api/agents/{name}")
    def put_agent(name: str, body: dict = Body(...)):
        with editing.session(paths, body.get("expected_hash")) as doc:
            editing.upsert_agent(doc, name, _clean_agent(body.get("agent")), body.get("confirm"))
        return {"ok": True, "yaml_hash": editing.current_hash(paths)}

    @app.delete("/api/agents/{name}")
    def delete_agent(name: str, body: dict = Body(...)):
        with editing.session(paths, body.get("expected_hash")) as doc:
            editing.delete_agent(doc, name, body.get("confirm"))
        return {"ok": True, "yaml_hash": editing.current_hash(paths)}

    @app.post("/api/agents/{name}/duplicate")
    def duplicate(name: str, body: dict = Body(...)):
        with editing.session(paths, body.get("expected_hash")) as doc:
            new = editing.duplicate_agent(doc, name)
        return {"ok": True, "name": new, "yaml_hash": editing.current_hash(paths)}

    @app.post("/api/agents/{name}/rename")
    def rename(name: str, body: dict = Body(...)):
        new = body.get("new_name", "")
        with editing.session(paths, body.get("expected_hash")) as doc:
            editing.rename_agent(doc, paths, name, new, body.get("confirm"))
        return {"ok": True, "name": new, "yaml_hash": editing.current_hash(paths)}

    @app.get("/api/agents/{name}/raw")
    def get_raw(name: str):
        doc, _ = editing.read(paths)
        idx = editing.find(doc, name)
        if idx is None:
            return JSONResponse({"detail": f"no existe el agente {name!r}"}, status_code=404)
        raw = editing.to_plain(doc["agents"][idx])
        rendered = ""
        try:
            rendered = render_agent_md(Agent.model_validate(raw), render_ctx())
        except Exception:
            pass
        manual = read_manual(paths, name) if raw.get("manual") else None
        deployed_file = _agentes_desplegados(paths) / f"{name}.md"
        deployed = deployed_file.read_text(encoding="utf-8") if deployed_file.exists() else None
        return {"manual": bool(raw.get("manual")), "rendered": rendered, "deployed": deployed,
                "content": manual if manual is not None else (deployed or rendered)}

    @app.post("/api/agents/{name}/raw")
    def post_raw(name: str, body: dict = Body(...)):
        with editing.session(paths, body.get("expected_hash")) as doc:
            editing.save_manual(doc, paths, name, body.get("content", ""), body.get("confirm"))
        return {"ok": True, "yaml_hash": editing.current_hash(paths),
                "warning": "El agente pasó a manual: el generador ya no lo arma desde el formulario, "
                           "copia este .md tal cual. Aplicá para desplegarlo."}

    @app.post("/api/agents/{name}/unmanual")
    def unmanual(name: str, body: dict = Body(...)):
        with editing.session(paths, body.get("expected_hash")) as doc:
            editing.unset_manual(doc, name, body.get("confirm"))
        return {"ok": True, "yaml_hash": editing.current_hash(paths),
                "warning": "El agente vuelve a generarse desde el formulario: al aplicar, el .md "
                           "manual deja de usarse (queda guardado en manual/)."}

    @app.post("/api/agents/{name}/test")
    def test_agent(name: str):
        r = runner.ping(paths, name)
        return {"status": r.status, "latency_s": r.latency_s, "output": r.output, "detail": r.detail,
                "meta": r.meta}

    @app.post("/api/preview/agent")
    def preview_agent(body: dict = Body(...)):
        data = _clean_agent(body.get("agent"))
        doc, _ = editing.read(paths)
        plain = editing.to_plain(doc)
        others = [a for a in plain.get("agents") or [] if a.get("name") != data.get("name")]
        plain["agents"] = [*others, data]
        try:
            reg = parse_registry(plain)
            ctx = render_ctx()
        except ConfigError as exc:
            return {"md": "", "errors": exc.errors}
        errors = [e for e in check_registry(reg, ctx.backends) if f"'{data.get('name')}'" in e]
        return {"md": render_agent_md(reg.agent(data["name"]), ctx), "errors": errors}

    # --- moods y orquestación ----------------------------------------------------------------

    @app.put("/api/orchestrator")
    def put_orchestrator(body: dict = Body(...)):
        with editing.session(paths, body.get("expected_hash")) as doc:
            editing.set_orchestrator(doc, body)
        return {"ok": True, "yaml_hash": editing.current_hash(paths)}

    @app.post("/api/moods")
    def post_moods(body: dict = Body(...)):
        action, name = body.get("action"), body.get("name", "")
        with editing.session(paths, body.get("expected_hash")) as doc:
            if action == "add":
                moods.add_mood(doc, name, body.get("description", ""))
            elif action == "update":
                moods.update_mood(doc, name, body.get("description", ""))
            elif action == "rename":
                moods.rename_mood(doc, name, body.get("new_name", ""))
            elif action == "delete":
                moods.delete_mood(doc, name)
            elif action == "default":
                moods.set_default(doc, name)
            else:
                raise editing.EditError([f"acción desconocida: {action!r}"])
        return {"ok": True, "yaml_hash": editing.current_hash(paths)}

    @app.post("/api/preview/block")
    def preview_block(body: dict = Body(default={})):
        doc, _ = editing.read(paths)
        plain = editing.to_plain(doc)
        plain["orchestrator"] = {**plain.get("orchestrator", {}), **(body.get("orchestrator") or {})}
        try:
            reg = parse_registry(plain)
            inner = render_block_inner(reg, render_ctx())
        except ConfigError as exc:
            return {"block": "", "errors": exc.errors}
        return {"block": f"<!-- orquesta:start -->\n{inner}\n<!-- orquesta:end -->", "errors": []}

    # --- aplicar -----------------------------------------------------------------------------

    @app.post("/api/apply/dry-run")
    def dry_run(body: dict = Body(default={})):
        return plan_json(build_plan(paths, confirmations=body.get("confirm") or []))

    @app.post("/api/apply")
    def apply(body: dict = Body(...)):
        plan = build_plan(paths, confirmations=body.get("confirm") or [])
        if plan.errors:
            return JSONResponse({"errors": plan.errors}, status_code=422)
        if plan.violations:
            return JSONResponse({"needs_confirmation": True, **plan_json(plan)}, status_code=409)
        if plan.empty:
            return {"applied": False, "note": "No había cambios para aplicar."}
        try:
            res = apply_plan(plan, body.get("message") or "Aplicar cambios desde la UI",
                             run_tests=run_tests)
        except GenerateError as exc:
            return JSONResponse({"errors": [str(exc)]}, status_code=422)
        return {"applied": True, "commit": res.commit, "summary": res.summary,
                "tests_output": res.tests_output, "note": RESTART_NOTE}

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=paths.home, capture_output=True, text=True)

    @app.post("/api/discard")
    def discard(body: dict = Body(...)):
        if body.get("confirm") is not True:
            raise editing.EditError(["descartar los cambios sin aplicar requiere confirmación"])
        git("checkout", "HEAD", "--", "orquesta.yaml")
        if git("ls-files", "--", "manual").stdout.strip():
            git("checkout", "HEAD", "--", "manual")
        return {"ok": True, "yaml_hash": editing.current_hash(paths)}

    # --- salud -------------------------------------------------------------------------------

    @app.get("/api/health")
    def health():
        return doctor.run_doctor(paths, deps=doctor_deps, live=False, tests=False).to_json()

    @app.post("/api/health/refresh")
    def health_refresh():
        return doctor.run_doctor(paths, deps=doctor_deps, use_cache=False, live=True).to_json()

    @app.get("/api/usage")
    def usage_window():
        from orquesta import calibration, usage
        return {"backends": usage.window(paths), "calibracion": calibration.table(paths)}

    @app.post("/api/that-it-guy")
    def that_it_guy(body: dict = Body(...)):
        prompt = (f"Usá el subagent that-it-guy para resolver: {body.get('name', '')} — "
                  f"{body.get('message', '')}")
        return {"command": "claude " + shlex.quote(prompt)}

    # --- historial ---------------------------------------------------------------------------

    @app.get("/api/history")
    def history():
        out = git("log", "-n", "60", "--format=%H%x1f%h%x1f%cI%x1f%s").stdout
        commits = []
        for line in out.splitlines():
            sha, short, date, msg = line.split("\x1f", 3)
            commits.append({"sha": sha, "short": short, "date": date, "message": msg})
        return {"commits": commits}

    @app.post("/api/history/{sha}/restore")
    def restore(sha: str, body: dict = Body(...)):
        if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
            raise editing.EditError(["sha inválido"])
        if body.get("expected_hash") != editing.current_hash(paths):
            raise editing.Conflict("orquesta.yaml cambió desde que lo cargaste: recargá.")
        old = git("show", f"{sha}:orquesta.yaml")
        if old.returncode != 0:
            raise editing.EditError([f"ese commit no tiene orquesta.yaml ({old.stderr.strip()})"])
        paths.registry.write_text(old.stdout, encoding="utf-8")
        if git("ls-tree", "-d", sha, "manual").stdout.strip():
            git("checkout", sha, "--", "manual")
        return {**plan_json(build_plan(paths)), "yaml_hash": editing.current_hash(paths)}

    @app.get("/api/changelog")
    def changelog():
        p = paths.changelog
        return {"content": p.read_text(encoding="utf-8") if p.exists() else ""}

    def editing_registry():
        from orquesta.config import load_registry
        return load_registry(paths)

    # --- plugins --------------------------------------------------------------

    @app.get("/api/plugins")
    def plugins():
        import json as _json
        try:
            installed = _json.loads((paths.plugins_dir / "installed_plugins.json").read_text())
            installed = installed.get("plugins", {})
        except (FileNotFoundError, ValueError):
            installed = {}
        audits = doctor._audits(paths)
        out = []
        for pid, entries in sorted(installed.items()):
            e = (entries or [{}])[0]
            sha = str(e.get("gitCommitSha") or "")
            audit = next((a for a in audits if a["plugin"] == pid and a["commit"] == sha), None)
            agents = []
            install = Path(e.get("installPath") or "/nonexistent")
            for f in sorted((install / "agents").rglob("*.md")) if (install / "agents").is_dir() else []:
                fm = _frontmatter(f.read_text(encoding="utf-8"))
                if fm.get("name"):
                    agents.append({"name": f"{pid.split('@')[0]}:{fm['name']}",
                                   "description": fm.get("description", ""),
                                   "model": fm.get("model", "inherit"), "tools": fm.get("tools", "")})
            try:
                used = plugins_mod.users(editing_registry(), pid)
            except Exception:
                used = []
            out.append({"id": pid, "version": e.get("version"), "sha": sha[:7], "used_by": used,
                        "audit": audit["verdict"] if audit else None,
                        "official": pid.endswith("@claude-plugins-official"), "agents": agents})
        return {"plugins": out}

    @app.post("/api/plugins/install")
    def plugin_install(body: dict = Body(...)):
        pid = body.get("id", "")
        if body.get("confirm") != pid:
            return JSONResponse({"needs_confirmation": True, "detail": "Instalar un plugin requiere "
                                 "confirmación explícita (escribí su id)."}, status_code=409)
        try:
            info = plugins_mod.install(paths, body.get("repo", ""), pid)
        except plugins_mod.PluginError as exc:
            return JSONResponse({"errors": [str(exc)]}, status_code=422)
        return {"ok": True, "id": info.id, "sha": info.sha[:12], "note": RESTART_NOTE}

    @app.post("/api/plugins/uninstall")
    def plugin_uninstall(body: dict = Body(...)):
        pid = body.get("id", "")
        if body.get("confirm") != pid:
            return JSONResponse({"needs_confirmation": True, "detail": "Desinstalar un plugin requiere "
                                 "confirmación explícita (escribí su id)."}, status_code=409)
        try:
            plugins_mod.uninstall(paths, pid)
        except plugins_mod.PluginError as exc:
            return JSONResponse({"errors": [str(exc)]}, status_code=422)
        return {"ok": True, "note": RESTART_NOTE}

    # --- conversaciones guardadas (solo lectura) ----------------------------------------------

    store = chat.Store(paths)

    # --- recursos humanos (base local y opinión explícita opcional) --------------------------

    @app.get("/api/rrhh/evaluacion")
    def rrhh_evaluacion():
        return {"agentes": rrhh.evaluar(paths)}

    @app.get("/api/rrhh/catalogo")
    def rrhh_catalogo():
        return rrhh.catalogar(load_registry(paths))

    @app.post("/api/rrhh/triage")
    def rrhh_triage(body: dict = Body(...)):
        texto = body.get("texto")
        if not isinstance(texto, str) or not texto.strip():
            return JSONResponse({"errors": ["texto debe ser una cadena no vacía"]}, status_code=422)
        return rrhh.triage(texto.strip(), load_registry(paths), model_discovery.local_models())

    @app.post("/api/rrhh/reclutador")
    def rrhh_reclutador(body: dict = Body(...)):
        try:
            return reclutador.consultar(body.get("texto"), paths, model_discovery.local_models())
        except ValueError as exc:
            return JSONResponse({"errors": [str(exc)]}, status_code=422)
        except ConfigError as exc:
            return JSONResponse({"errors": ["Corregí la configuración: " + str(exc)]}, status_code=422)

    @app.get("/api/rrhh/sugerencias/{cid}")
    def rrhh_sugerencias(cid: str):
        return rrhh.sugerir_participantes(store.get_conversation(cid), load_registry(paths))

    @app.get("/api/chat/projects")
    def chat_projects():
        return {"projects": store.list_projects()}

    @app.get("/api/chat/conversations")
    def chat_conversations(project: str | None = None):
        if project in ("sin-proyecto", "none", ""):
            return {"conversations": store.list_conversations(project_id=None)}
        return {"conversations": store.list_conversations(project) if project else store.list_conversations()}

    @app.get("/api/worktrees")
    def all_worktrees():
        orphan = {o.branch: o.reason for o in worktrees_mod.orphans(paths)}
        out = []
        for w in worktrees_mod.list_worktrees(paths):
            item = {"branch": w.branch, "agent": w.agent, "task": w.task, "repo": str(w.repo),
                    "created_at": w.created_at, "huerfano": orphan.get(w.branch)}
            try:
                item["diffstat"] = worktrees_mod.diffstat(w)
                item["conflictos"] = worktrees_mod.conflicts(paths, w.branch, repo=w.repo)
            except worktrees_mod.WorktreeError:
                item["diffstat"], item["conflictos"] = "", None
            out.append(item)
        return {"worktrees": out}

    @app.get("/api/chat/conversations/{cid}/export.md")
    def chat_export(cid: str):
        from fastapi.responses import Response
        md = chat.export_markdown(store, cid)
        name = re.sub(r"[^a-z0-9-]+", "-", store.get_conversation(cid)["title"].lower()).strip("-") or "conversacion"
        return Response(md, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}.md"'})

    return app
