"""Plugins: asignación por agente (skills + MCP en el frontmatter) y gestión global auditada."""
from __future__ import annotations

import json

import pytest

from helpers import dump_yaml, git, load_yaml
from orquesta import plugins
from orquesta.generate import apply_plan, build_plan

SHA = "a" * 40


def instalar_fake(env, pid="codex@openai-codex", skills=("gpt-5-4-prompting",), mcp=(), sha=SHA):
    name = pid.split("@")[0]
    root = env.claude_dir / "plugins" / "cache" / name
    for s in skills:
        (root / "skills" / s).mkdir(parents=True, exist_ok=True)
        (root / "skills" / s / "SKILL.md").write_text(f"---\nname: {s}\n---\nx\n")
    if mcp:
        root.mkdir(parents=True, exist_ok=True)
        (root / ".mcp.json").write_text(json.dumps({"mcpServers": {m: {"command": "x"} for m in mcp}}))
    root.mkdir(parents=True, exist_ok=True)
    f = env.claude_dir / "plugins" / "installed_plugins.json"
    data = json.loads(f.read_text()) if f.exists() else {"version": 2, "plugins": {}}
    data["plugins"][pid] = [{"scope": "user", "installPath": str(root), "version": "1.0", "gitCommitSha": sha}]
    f.write_text(json.dumps(data))


def editar(env, mod):
    d = load_yaml(env.registry)
    mod(d)
    dump_yaml(d, env.registry)


def test_lista_plugins_instalados_con_skills_y_mcp(env):
    instalar_fake(env, "playwright@claude-plugins-official", skills=(), mcp=("playwright",))
    instalar_fake(env)
    inst = plugins.installed(env)
    assert inst["codex@openai-codex"].skills == ["codex:gpt-5-4-prompting"]
    assert inst["playwright@claude-plugins-official"].mcp_tools == ["mcp__plugin_playwright_playwright__*"]


def test_plugin_por_agente_se_renderiza_en_skills_y_tools(env):
    instalar_fake(env, "playwright@claude-plugins-official", skills=(), mcp=("playwright",))
    instalar_fake(env)
    editar(env, lambda d: d["agents"][3].update(plugins=["codex@openai-codex", "playwright@claude-plugins-official"]))
    plan = build_plan(env, confirmations={"mantenedor"})
    assert plan.errors == []
    md = next(c.new for c in plan.changes if c.path.name == "mantenedor.md")
    fm = md.split("---\n")[1]
    assert "skills: [\"codex:gpt-5-4-prompting\"]" in fm
    assert "mcp__plugin_playwright_playwright__*" in fm


def test_plugin_no_instalado_es_error(env):
    editar(env, lambda d: d["agents"][3].update(plugins=["fantasma@x"]))
    assert any("fantasma@x" in e and "no está instalado" in e for e in build_plan(env).errors)


def test_plugins_en_agente_externo_es_error(env):
    instalar_fake(env)
    editar(env, lambda d: d["agents"][1].update(plugins=["codex@openai-codex"]))
    assert any("codex-forja" in e and "plugins" in e for e in build_plan(env).errors)


def test_agregar_un_plugin_requiere_confirmacion_y_sacarlo_no(env):
    instalar_fake(env)
    apply_plan(build_plan(env), "deploy", run_tests=None)
    editar(env, lambda d: d["agents"][2].update(plugins=[]))  # sin cambio real
    editar(env, lambda d: d["agents"].append({"name": "claude-ayudante", "backend": "claude", "role": "r",
                                              "tools": ["Read"], "prompt": "p"}))
    apply_plan(build_plan(env), "nuevo", run_tests=None)
    editar(env, lambda d: d["agents"][4].update(plugins=["codex@openai-codex"]))
    plan = build_plan(env)
    assert [(v.agent, v.kind) for v in plan.violations] == [("claude-ayudante", "plugins")]
    apply_plan(build_plan(env, confirmations={"claude-ayudante"}), "ok", run_tests=None)
    editar(env, lambda d: d["agents"][4].update(plugins=[]))
    assert build_plan(env).violations == []


class FakeCmd:
    def __init__(self, env, remote_sha=SHA, installed_sha=SHA):
        self.env, self.remote_sha, self.installed_sha, self.calls = env, remote_sha, installed_sha, []

    def __call__(self, cmd, timeout=300):
        self.calls.append(cmd)
        if cmd[:2] == ["git", "ls-remote"]:
            return 0, f"{self.remote_sha}\tHEAD\n", ""
        if cmd[:3] == ["claude", "plugin", "install"]:
            instalar_fake(self.env, cmd[3], sha=self.installed_sha)
        if cmd[:3] == ["claude", "plugin", "uninstall"]:
            f = self.env.claude_dir / "plugins" / "installed_plugins.json"
            d = json.loads(f.read_text())
            d["plugins"].pop(cmd[3], None)
            f.write_text(json.dumps(d))
        return 0, "ok", ""


def auditar(env, pid, sha=SHA, verdict="ok"):
    env.audits_dir.mkdir(exist_ok=True)
    (env.audits_dir / f"x@{sha}.md").write_text(f"---\nplugin: {pid}\ncommit: {sha}\nverdict: {verdict}\n---\n")


def test_install_sin_auditoria_del_commit_exacto_se_niega(env):
    fake = FakeCmd(env)
    with pytest.raises(plugins.PluginError, match="audit"):
        plugins.install(env, "owner/repo", "algo@market", run=fake)
    assert not any(c[:3] == ["claude", "plugin", "install"] for c in fake.calls)


def test_install_con_auditoria_no_instalar_se_niega(env):
    auditar(env, "algo@market", verdict="no-instalar")
    with pytest.raises(plugins.PluginError, match="no-instalar"):
        plugins.install(env, "owner/repo", "algo@market", run=FakeCmd(env))


def test_install_auditado_instala_y_verifica_sha(env):
    auditar(env, "algo@market")
    fake = FakeCmd(env)
    plugins.install(env, "owner/repo", "algo@market", run=fake)
    assert ["claude", "plugin", "install", "algo@market"] in fake.calls
    assert "algo@market" in plugins.installed(env)


def test_install_con_sha_distinto_al_auditado_desinstala(env):
    auditar(env, "algo@market")
    fake = FakeCmd(env, installed_sha="b" * 40)
    with pytest.raises(plugins.PluginError, match="no coincide"):
        plugins.install(env, "owner/repo", "algo@market", run=fake)
    assert ["claude", "plugin", "uninstall", "algo@market"] in fake.calls


def test_uninstall_en_uso_se_niega(env):
    instalar_fake(env)
    editar(env, lambda d: d["agents"][3].update(plugins=["codex@openai-codex"]))
    with pytest.raises(plugins.PluginError, match="mantenedor"):
        plugins.uninstall(env, "codex@openai-codex", run=FakeCmd(env))


def test_uninstall_libre_desinstala(env):
    instalar_fake(env)
    fake = FakeCmd(env)
    plugins.uninstall(env, "codex@openai-codex", run=fake)
    assert "codex@openai-codex" not in plugins.installed(env)


def test_cli_install_exige_yes(env, monkeypatch, capsys):
    from orquesta import cli
    monkeypatch.setenv("ORQUESTA_HOME", str(env.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env.claude_dir))
    assert cli.main(["plugins", "install", "owner/repo", "algo@market"]) == 2
    assert "--yes" in capsys.readouterr().out


def test_cli_list(env, monkeypatch, capsys):
    from orquesta import cli
    instalar_fake(env)
    editar(env, lambda d: d["agents"][3].update(plugins=["codex@openai-codex"]))
    monkeypatch.setenv("ORQUESTA_HOME", str(env.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env.claude_dir))
    assert cli.main(["plugins", "list"]) == 0
    out = capsys.readouterr().out
    assert "codex@openai-codex" in out and "mantenedor" in out


def test_api_plugins_uninstall_pide_confirmacion(env, monkeypatch):
    from fastapi.testclient import TestClient
    from orquesta.ui.app import create_app
    instalar_fake(env)
    c = TestClient(create_app(env, port=8787, token="t", run_tests=None), base_url="http://127.0.0.1:8787",
                   headers={"X-Orquesta-Token": "t"})
    assert c.post("/api/plugins/uninstall", json={"id": "codex@openai-codex"}).status_code == 409
    monkeypatch.setattr("orquesta.plugins._run", FakeCmd(env))
    r = c.post("/api/plugins/uninstall", json={"id": "codex@openai-codex", "confirm": "codex@openai-codex"})
    assert r.status_code == 200, r.text
    assert c.get("/api/state").json()["installed_plugins"] == []
