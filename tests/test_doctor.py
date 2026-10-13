"""doctor: chequeos deterministas con todas las dependencias externas simuladas."""
from __future__ import annotations

import json
import shutil

import pytest

from helpers import REPO, dump_yaml, git, load_yaml
from orquesta import catalog, doctor, migracion, worktrees
from orquesta.generate import apply_plan, build_plan
from orquesta.runner import RunResult

TODOS = ["gemini-lector", "codex-forja", "codex-critico", "mantenedor"]


class FakeEnv(doctor.Env):
    """Responde por prefijo de comando; registra llamadas, pings e init de Claude."""

    platform = "linux"

    def __init__(self, responses=None, which=None, init=None, ping=None, http=None, plugin=None):
        self.responses = {
            "claude --version": (0, "2.1.289 (Claude Code)", ""),
            "codex --version": (0, "codex-cli 0.160.0", ""),
            "agy --version": (0, "1.2.9", ""),
            "node --version": (0, "v22.22.0", ""),
            "npm view @anthropic-ai/claude-code version": (0, "2.1.289", ""),
            "npm view @openai/codex version": (0, "0.160.0", ""),
            "agy changelog": (0, "1.2.9:\n· cosas", ""),
            "codex login status": (0, "Logged in using ChatGPT", ""),
            "agy models": (0, "gemini-3.8-flash-low\tGemini 3.8 Flash (Low)", ""),
            "bwrap": (0, "", ""),
            "pytest": (0, "165 passed", ""),
            **(responses or {}),
        }
        self.which_map = which or {}
        self.init = init if init is not None else {"agents": TODOS,
                                                   "tools": sorted(catalog.BUILTIN_TOOLS)}
        self.ping = ping or RunResult("OK", output="PONG")
        self.http = http or {
            "https://nodejs.org/dist/index.json": '[{"version": "v22.22.0", "lts": "Jod"}]',
            "https://endoflife.date/api/python.json": '[{"cycle": "3.13", "latest": "3.13.12"}]',
        }
        self.calls, self.pings, self.init_calls = [], [], 0
        self.plugin = plugin if plugin is not None else {"plugins": []}
        self.plugin_calls = []

    def run(self, cmd, timeout=60, cwd=None):
        self.calls.append(cmd)
        if cmd[0] == "git":  # git real: los chequeos de repo y worktrees lo necesitan
            return super().run(cmd, timeout, cwd)
        line = " ".join(cmd)
        if "pytest" in cmd:
            return self.responses["pytest"]
        for prefix, resp in self.responses.items():
            if line.startswith(prefix):
                return resp
        return 0, "", ""

    def which_all(self, name):
        return self.which_map.get(name, [f"/usr/bin/{name}"])

    def http_get(self, url, timeout=5):
        return self.http[url]

    def claude_init(self):
        self.init_calls += 1
        return self.init

    def claude_plugin_list(self, claude_config_dir):
        self.plugin_calls.append(claude_config_dir)
        return self.plugin

    def ping_backend(self, paths, backend):
        self.pings.append(backend)
        return self.ping


@pytest.fixture
def denv(env):
    """Entorno desplegado con el backends.yaml real (los comandos los intercepta FakeEnv)."""
    shutil.copy(REPO / "backends.yaml", env.backends)
    git(env.home, "add", "-A")
    git(env.home, "commit", "-qm", "backends reales")
    apply_plan(build_plan(env), "deploy", run_tests=None)
    return env


def correr(env, fake=None, **kw) -> doctor.Report:
    return doctor.run_doctor(env, deps=fake or FakeEnv(), **kw)


def check(report, cid):
    return next(c for c in report.checks if c.id == cid)


def test_todo_ok(denv):
    r = correr(denv)
    assert r.overall == "ok", [(c.id, c.status, c.message) for c in r.checks if c.status != "ok"]


@pytest.mark.parametrize("fase", ["plugin", "legacy"])
@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("only", [None, "agentes", "claude-md"])
def test_manifiesto_ausente_o_corrupto_es_fail(denv, fase, live, only):
    if fase == "legacy":
        denv.manifest.write_text("invalid", encoding="utf-8")
    else:
        denv.plugin_manifest.unlink(missing_ok=True)  # generate en legacy también escribe el plugin
    migracion.set_fase(denv, fase)
    report = correr(denv, live=live, only=only)
    assert report.overall == "fail"
    c = next(c for c in report.checks if f"manifiesto de la fase {fase}" in c.message)
    assert c.status == "fail"
    assert "orquesta generate" in c.suggestion
    assert "state/migracion/" in c.suggestion


def test_archivo_de_fase_corrupto_es_fail_sin_traceback(denv):
    migracion.set_fase(denv, "legacy")
    next((denv.state_dir / "migracion").glob("*.json")).write_text("no es json", encoding="utf-8")
    report = correr(denv)
    assert report.overall == "fail"
    assert any("fase de deploy" in c.message and c.status == "fail" for c in report.checks)


def test_md_editado_a_mano_es_warn_y_sugiere_regenerar(denv):
    (denv.agents_dir / "gemini-lector.md").write_text("editado a mano")
    c = check(correr(denv), "agentes")
    assert c.status == "warn" and "gemini-lector.md" in c.message
    assert "orquesta generate" in c.suggestion


def test_md_faltante_es_fail(denv):
    (denv.agents_dir / "codex-forja.md").unlink()
    assert check(correr(denv), "agentes").status == "fail"


def test_cambios_sin_aplicar_en_el_registro(denv):
    data = load_yaml(denv.registry)
    data["agents"][1]["prompt"] = "cambiado"
    dump_yaml(data, denv.registry)
    c = check(correr(denv), "agentes")
    assert c.status == "warn" and "sin aplicar" in c.message


def test_bloque_de_claude_md_faltante_es_fail(denv):
    denv.claude_md.write_text("nada de orquesta\n")
    assert check(correr(denv), "claude-md").status == "fail"


def test_registro_invalido_es_fail(denv):
    denv.registry.write_text("orchestrator: [roto\n")
    assert check(correr(denv), "registro").status == "fail"


def test_auth_ok_queda_cacheada_10_minutos(denv):
    fake = FakeEnv()
    correr(denv, fake)
    n = sum(c[:3] == ["codex", "login", "status"] for c in fake.calls)
    correr(denv, fake)
    assert sum(c[:3] == ["codex", "login", "status"] for c in fake.calls) == n
    correr(denv, fake, use_cache=False)
    assert sum(c[:3] == ["codex", "login", "status"] for c in fake.calls) == n + 1


def test_auth_vencida_es_fail_y_da_el_comando_para_el_usuario(denv):
    fake = FakeEnv({"codex login status": (1, "Not logged in", "")})
    c = check(correr(denv, fake), "auth-codex")
    assert c.status == "fail" and "codex login" in c.suggestion


def test_version_nueva_es_warn_y_no_instala_nada(denv):
    fake = FakeEnv({"agy changelog": (0, "1.2.16:\n· cosas nuevas", "")})
    c = check(correr(denv, fake), "version-agy")
    assert c.status == "warn" and "1.2.16" in c.message and "agy update" in c.suggestion
    assert not any(cmd[:2] == ["agy", "update"] for cmd in fake.calls)


def test_codex_por_snap_es_warn(denv):
    fake = FakeEnv(which={"codex": ["/home/u/.nvm/bin/codex", "/snap/bin/codex"]})
    c = check(correr(denv, fake), "binarios")
    assert c.status == "warn" and "snap" in c.message


def test_skip_live_no_gasta_cuota(denv):
    fake = FakeEnv()
    r = correr(denv, fake, live=False)
    assert fake.pings == [] and fake.init_calls == 0 and fake.plugin_calls == []
    assert not {"agy-stdout", "claude-agentes", "plugin-instalado"} & {c.id for c in r.checks}


def test_bug_de_stdout_vacio_de_agy_es_fail(denv):
    fake = FakeEnv(ping=RunResult("FALLO_BACKEND", detail="salida vacía (¿volvió el bug de stdout sin TTY?)"))
    c = check(correr(denv, fake), "agy-stdout")
    assert c.status == "fail" and "TTY" in c.message


def test_cuota_de_agy_agotada_es_warn(denv):
    fake = FakeEnv(ping=RunResult("RATE_LIMIT", detail="límite de cuota"))
    assert check(correr(denv, fake), "agy-stdout").status == "warn"


def test_claude_no_cargo_un_agente_es_fail(denv):
    fake = FakeEnv(init={"agents": TODOS[:2], "tools": sorted(catalog.BUILTIN_TOOLS)})
    c = check(correr(denv, fake), "claude-agentes")
    assert c.status == "fail" and "codex-critico" in c.message


def test_catalogo_de_tools_desactualizado_es_warn(denv):
    fake = FakeEnv(init={"agents": TODOS, "tools": sorted(catalog.BUILTIN_TOOLS | {"Glob"})})
    c = check(correr(denv, fake), "catalogo-tools")
    assert c.status == "warn" and "Glob" in c.message


def _plugin(denv, plugin_id, sha):
    denv.plugins_dir.mkdir(parents=True, exist_ok=True)
    (denv.plugins_dir / "installed_plugins.json").write_text(json.dumps({
        "version": 2, "plugins": {plugin_id: [{"scope": "user", "version": "1.0", "gitCommitSha": sha}]}}))


def _audit(denv, plugin_id, sha, verdict="ok"):
    denv.audits_dir.mkdir(exist_ok=True)
    (denv.audits_dir / f"x@{sha}.md").write_text(
        f"---\nplugin: {plugin_id}\ncommit: {sha}\nverdict: {verdict}\n---\n# auditoría\n")


def test_plugin_con_commit_distinto_al_auditado_es_warn(denv):
    _plugin(denv, "codex@openai-codex", "b" * 40)
    _audit(denv, "codex@openai-codex", "a" * 40)
    c = check(correr(denv), "plugin-codex@openai-codex")
    assert c.status == "warn" and "aaaaaaa" in c.message and "bbbbbbb" in c.message


def test_plugin_auditado_e_instalado_en_el_mismo_commit_es_ok(denv):
    _plugin(denv, "codex@openai-codex", "a" * 40)
    _audit(denv, "codex@openai-codex", "a" * 40)
    assert check(correr(denv), "plugin-codex@openai-codex").status == "ok"


def test_plugin_con_veredicto_no_instalar_instalado_es_fail(denv):
    _plugin(denv, "agy@antigravity-cc", "c" * 40)
    _audit(denv, "agy@antigravity-cc", "c" * 40, verdict="no-instalar")
    assert check(correr(denv), "plugin-agy@antigravity-cc").status == "fail"


def test_plugin_oficial_de_anthropic_sin_auditoria_es_ok(denv):
    _plugin(denv, "frontend-design@claude-plugins-official", "d" * 40)
    assert check(correr(denv), "plugins-oficiales").status == "ok"


def test_plugin_de_terceros_sin_auditoria_es_warn(denv):
    _plugin(denv, "algo@otro-market", "e" * 40)
    assert check(correr(denv), "plugin-algo@otro-market").status == "warn"


def test_plugin_propio_de_orquesta_no_pide_auditoria(denv):
    _plugin(denv, "orquesta@orquesta-local", "")
    assert not [c for c in correr(denv).checks if c.id == "plugin-orquesta@orquesta-local"]


def test_repo_sucio_es_warn(denv):
    (denv.home / "suelto.txt").write_text("x")
    c = check(correr(denv), "git")
    assert c.status == "warn" and "suelto.txt" in c.message


def test_tests_fallando_es_fail(denv):
    fake = FakeEnv({"pytest": (1, "1 failed, 164 passed", "")})
    assert check(correr(denv, fake), "tests").status == "fail"


def test_worktree_huerfano_es_warn(denv, tmp_path):
    repo = tmp_path / "proy"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("a")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "i")
    wt = worktrees.create(denv, repo=repo, agent="codex-forja", task="x")
    shutil.rmtree(wt.path)
    c = check(correr(denv), "worktrees")
    assert c.status == "warn" and wt.branch in c.message


def test_sin_bwrap_es_fail(denv):
    fake = FakeEnv(which={"bwrap": []})
    c = check(correr(denv, fake), "sandbox")
    assert c.status == "fail" and "bubblewrap" in c.suggestion


def test_json_y_render(denv):
    r = correr(denv)
    d = r.to_json()
    assert d["overall"] == "ok" and d["generated_at"]
    assert all({"id", "name", "status", "message", "suggestion"} <= set(c) for c in d["checks"])
    assert "Resumen" in r.render()



def test_tests_no_duplica_el_flag_q_de_addopts(denv):
    # addopts ya trae -q; con -q otra vez pytest pasa a -qq y no imprime el resumen
    fake = FakeEnv()
    correr(denv, fake)
    pytest_calls = [c for c in fake.calls if "pytest" in " ".join(c)]
    assert pytest_calls and all("-q" not in c for c in pytest_calls)


def test_tests_usa_la_linea_de_resumen_aunque_no_sea_la_ultima(denv):
    salida = "........ [100%]\n16 passed in 0.5s\nwarning: algo al final\n"
    fake = FakeEnv({"pytest": (0, salida, "")})
    assert check(correr(denv, fake), "tests").message == "16 passed in 0.5s"


def test_only_no_corre_los_chequeos_que_no_se_pidieron(denv):
    fake = FakeEnv()
    r = correr(denv, fake, only="git")
    assert [c.id for c in r.checks] == ["git"]
    assert fake.pings == [] and fake.init_calls == 0
    assert not any(c[:2] == ["codex", "login"] for c in fake.calls)


def test_sin_tests_no_corre_la_suite(denv):
    fake = FakeEnv()
    r = correr(denv, fake, tests=False)
    assert not any("pytest" in " ".join(c) for c in fake.calls)
    assert "tests" not in {c.id for c in r.checks}


def test_cuota_en_riesgo_alto_es_warn(denv):
    from datetime import datetime, timedelta, timezone
    from orquesta import usage
    now = datetime.now(timezone.utc)
    usage.record(denv, backend="agy", agent="g", status="OK", tokens_in=100, ts=now - timedelta(hours=20))
    usage.record(denv, backend="agy", agent="g", status="RATE_LIMIT", ts=now - timedelta(hours=19, minutes=50))
    usage.record(denv, backend="agy", agent="g", status="OK", tokens_in=95, ts=now - timedelta(minutes=5))
    c = check(correr(denv), "cuota")
    assert c.status == "warn" and "agy" in c.message


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_plugin_generado_sincronizado(denv, fase):
    migracion.set_fase(denv, fase)
    c = check(correr(denv, only="plugin-generado"), "plugin-generado")
    assert c.status == "ok" and c.message == "sincronizado (hash ok)"
    assert c.suggestion == ""


@pytest.mark.parametrize("fase,status", [("legacy", "warn"), ("plugin", "fail")])
@pytest.mark.parametrize("problema", ["faltante", "modificado", "extra", "registro", "binario"])
def test_plugin_generado_diferencias(denv, fase, status, problema):
    migracion.set_fase(denv, fase)
    path = denv.plugin_dir / "agents" / "codex-forja.md"
    if problema == "faltante":
        path.unlink()
    elif problema == "modificado":
        path.write_text("editado a mano")
    elif problema == "binario":
        path.write_bytes(b"\xff\x00")
    elif problema == "extra":
        path = denv.plugin_dir / "agents" / "extra.md"
        path.write_text("sobra")
    else:
        data = load_yaml(denv.registry)
        data["agents"][1]["prompt"] = "cambio sin aplicar"
        dump_yaml(data, denv.registry)
    c = check(correr(denv, only="plugin-generado"), "plugin-generado")
    assert c.status == status and path.name in c.message
    assert "orquesta generate" in c.suggestion


@pytest.mark.parametrize("fase,status", [("legacy", "warn"), ("plugin", "fail")])
def test_plugin_generado_sin_directorio(denv, fase, status):
    migracion.set_fase(denv, fase)
    shutil.rmtree(denv.plugin_root)
    c = check(correr(denv, only="plugin-generado"), "plugin-generado")
    assert c.status == status and "faltan:" in c.message
    assert "orquesta generate" in c.suggestion


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_plugin_instalado_ok(denv, fase):
    migracion.set_fase(denv, fase)
    fake = FakeEnv(plugin={"plugins": [{"id": "orquesta@orquesta-local", "enabled": True,
                                         "readFromFolder": str(denv.plugin_dir)}]})
    c = check(correr(denv, fake, only="plugin-instalado"), "plugin-instalado")
    assert c.status == "ok" and "instalado y habilitado" in c.message
    assert str(denv.plugin_dir) in c.message and c.suggestion == ""
    assert fake.plugin_calls == [denv.claude_dir]


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
@pytest.mark.parametrize("problema", ["ausente", "deshabilitado", "otro-origen", "sin-respuesta"])
def test_plugin_instalado_problemas(denv, fase, problema):
    migracion.set_fase(denv, fase)
    entry = {"id": "orquesta@orquesta-local", "enabled": problema != "deshabilitado",
             "readFromFolder": "/otro/orquesta" if problema == "otro-origen" else str(denv.plugin_dir)}
    fake = FakeEnv(plugin={"plugins": [] if problema == "ausente" else [entry]})
    if problema == "sin-respuesta":
        fake.plugin = None
    c = check(correr(denv, fake, only="plugin-instalado"), "plugin-instalado")
    status = "fail" if fase == "plugin" else "ok" if problema == "ausente" else "warn"
    assert c.status == status
    detail = {"ausente": "no instalado", "deshabilitado": "deshabilitado",
              "otro-origen": "Read from", "sin-respuesta": "no pude consultar"}[problema]
    assert detail in c.message
    assert "orquesta migrar corte" in c.suggestion
    if fase == "legacy" and problema == "ausente":
        assert c.message == "todavía no instalado: se instala con `orquesta migrar corte`"


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_deploy_viejo_ok(denv, fase):
    migracion.set_fase(denv, fase)
    if fase == "plugin":
        shutil.rmtree(denv.agents_dir)
        denv.claude_md.write_text("memoria personal\n")
    c = check(correr(denv, only="deploy-viejo"), "deploy-viejo")
    assert c.status == "ok" and c.suggestion == ""
    assert ("legacy" if fase == "legacy" else "sin restos") in c.message


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
@pytest.mark.parametrize("resto", ["agentes", "bloque", "marcador-roto"])
def test_deploy_viejo_restos(denv, fase, resto):
    migracion.set_fase(denv, fase)
    if resto == "agentes":
        denv.claude_md.unlink()
    else:
        shutil.rmtree(denv.agents_dir)
        if resto == "marcador-roto":
            denv.claude_md.write_text("<!-- orquesta:start -->\nsin cierre\n")
    c = check(correr(denv, only="deploy-viejo"), "deploy-viejo")
    assert c.status == ("fail" if fase == "plugin" else "ok")
    if fase == "plugin":
        assert "restos del deploy viejo: `orquesta migrar corte` los retira" in c.message
        assert "orquesta migrar corte" in c.suggestion
        assert ("codex-forja.md" if resto == "agentes" else "CLAUDE.md") in c.message


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_manifiesto_fase_ok(denv, fase):
    migracion.set_fase(denv, fase)
    c = check(correr(denv, only="manifiesto-fase"), "manifiesto-fase")
    assert c.status == "ok" and c.message == f"Fase: {fase}" and c.suggestion == ""


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
def test_manifiesto_fase_corrupto(denv, fase):
    migracion.set_fase(denv, fase)
    path = denv.legacy_manifest if fase == "legacy" else denv.plugin_manifest
    path.write_text("no es json")
    c = check(correr(denv, only="manifiesto-fase"), "manifiesto-fase")
    assert c.status == "fail" and f"Fase: {fase}" in c.message
    assert f"manifiesto de la fase {fase} ausente o corrupto" in c.message
    assert "orquesta generate" in c.suggestion and "state/migracion/" in c.suggestion


def test_manifiesto_fase_legacy_por_default_sin_manifiesto(denv):
    denv.manifest.unlink()
    c = check(correr(denv, only="manifiesto-fase"), "manifiesto-fase")
    assert c.status == "ok" and c.message == "Fase: legacy"


def test_manifiesto_fase_plugin_ausente(denv):
    migracion.set_fase(denv, "plugin")
    denv.plugin_manifest.unlink()
    c = check(correr(denv, only="manifiesto-fase"), "manifiesto-fase")
    assert c.status == "fail" and "manifiesto de la fase plugin ausente" in c.message


@pytest.mark.parametrize("fase", ["legacy", "plugin"])
@pytest.mark.parametrize("falta", [False, True])
def test_claude_agentes_namespace_segun_fase(denv, fase, falta):
    migracion.set_fase(denv, fase)
    agentes = [f"orquesta:{a}" if fase == "plugin" else a for a in TODOS]
    fake = FakeEnv(init={"agents": TODOS if fase == "plugin" and falta else agentes[:-1] if falta else agentes,
                         "tools": sorted(catalog.BUILTIN_TOOLS)})
    c = check(correr(denv, fake, only="claude-agentes"), "claude-agentes")
    assert c.status == ("fail" if falta else "ok")
    if falta:
        assert agentes[-1] in c.message and "orquesta generate --dry-run" in c.suggestion


@pytest.mark.parametrize("json_output", [True, False])
def test_env_plugin_list_json_y_fallback(denv, json_output):
    prefix = f"env CLAUDE_CONFIG_DIR={denv.claude_dir} claude plugin list"
    entry = {"id": "orquesta@orquesta-local", "enabled": True, "readFromFolder": str(denv.plugin_dir)}
    text = f"Installed plugins:\n  ❯ orquesta@orquesta-local\n    Status: ✔ enabled\n    Read from: {denv.plugin_dir}\n"
    fake = FakeEnv(responses={
        prefix + " --json": (0, json.dumps([entry]), "") if json_output else (1, "", "unknown option '--json'"),
        prefix: (0, text, ""),
    })
    result = doctor.Env.claude_plugin_list(fake, denv.claude_dir)
    assert result == {"plugins": [entry]}
    assert fake.calls[0] == ["env", f"CLAUDE_CONFIG_DIR={denv.claude_dir}", "claude", "plugin", "list", "--json"]
    assert len(fake.calls) == (1 if json_output else 2)


def test_env_plugin_list_falla_sin_binarios_reales(denv):
    fake = FakeEnv(responses={f"env CLAUDE_CONFIG_DIR={denv.claude_dir} claude plugin list": (127, "", "no existe")})
    assert doctor.Env.claude_plugin_list(fake, denv.claude_dir) is None


def test_orden_chequeos_plugin():
    names = [fn.__name__ for fn, _, _ in doctor.CHECKS]
    i = names.index("_plugins")
    assert names[i - 4:i] == ["_plugin_generado", "_plugin_instalado", "_deploy_viejo", "_manifiesto_fase"]


@pytest.mark.parametrize("read_from,status", [(True, "ok"), (False, "fail")])
def test_plugin_instalado_verifica_read_from_no_install_path(denv, read_from, status):
    migracion.set_fase(denv, "plugin")
    entry = {"id": "orquesta@orquesta-local", "enabled": True,
             "installPath": "/cache/orquesta" if read_from else str(denv.plugin_dir)}
    if read_from:
        entry["readFromFolder"] = str(denv.plugin_dir)
    c = check(correr(denv, FakeEnv(plugin={"plugins": [entry]}), only="plugin-instalado"), "plugin-instalado")
    assert c.status == status and "Read from" in c.message


def test_clon_sin_instalar_avisa_como_instalar_y_no_falla(env):
    """Un clon recién bajado (nada desplegado) no es una falla: el doctor indica el paso de instalación."""
    shutil.copy(REPO / "backends.yaml", env.backends)
    r = correr(env, live=False)
    agentes, bloque = check(r, "agentes"), check(r, "claude-md")
    assert agentes.status == "warn" and "migrar corte" in agentes.suggestion
    assert bloque.status == "ok"
