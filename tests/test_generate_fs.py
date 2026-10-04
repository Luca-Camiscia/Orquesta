"""Generador: qué escribe, dónde, y qué pasa cuando algo sale mal."""
from __future__ import annotations

import json

import pytest

from helpers import dump_yaml, git, load_yaml, snapshot
from orquesta.claude_md import START, extract_inner
from orquesta.generate import GenerateError, GuardError, TestsFailed, apply_plan, build_plan


def ok_tests(paths):
    return True, "todo verde"


def bad_tests(paths):
    return False, "1 failed"


def editar_registro(env, mod):
    data = load_yaml(env.registry)
    mod(data)
    dump_yaml(data, env.registry)


def aplicar(env, mensaje="deploy de prueba", confirm=()):
    plan = build_plan(env, confirmations=confirm)
    assert plan.errors == [] and plan.violations == [], (plan.errors, plan.violations)
    return apply_plan(plan, mensaje, run_tests=ok_tests)


def test_dry_run_no_escribe_nada(env):
    antes = (snapshot(env.claude_dir), snapshot(env.home))
    plan = build_plan(env)
    assert plan.changes and plan.diff()
    assert (snapshot(env.claude_dir), snapshot(env.home)) == antes


def test_apply_escribe_solo_en_su_carpeta_y_en_claude_md(env):
    (env.claude_dir / "settings.json").write_text('{"x": 1}')
    (env.claude_dir / "agents" / "otra").mkdir(parents=True)
    (env.claude_dir / "agents" / "mio.md").write_text("mío")
    (env.claude_dir / "agents" / "otra" / "x.md").write_text("x")
    antes = snapshot(env.claude_dir)
    aplicar(env)
    despues = snapshot(env.claude_dir)
    cambiados = {k for k in set(antes) | set(despues) if antes.get(k) != despues.get(k)}
    assert cambiados
    assert all(k == "CLAUDE.md" or k.startswith("agents/orquesta/") for k in cambiados), cambiados
    assert despues["agents/mio.md"] == "mío".encode()


def test_apply_genera_un_md_por_agente_habilitado(env):
    aplicar(env)
    assert sorted(p.name for p in env.agents_dir.glob("*.md")) == [
        "codex-critico.md", "codex-forja.md", "gemini-lector.md", "mantenedor.md"]


def test_deshabilitar_borra_su_md_y_deja_el_resto_igual(env):
    aplicar(env)
    antes = snapshot(env.agents_dir)
    editar_registro(env, lambda d: d["agents"][2].update(enabled=False))
    aplicar(env)
    despues = snapshot(env.agents_dir)
    assert "codex-critico.md" not in despues
    for nombre in ("codex-forja.md", "gemini-lector.md", "mantenedor.md"):
        assert despues[nombre] == antes[nombre]


def test_eliminar_agente_borra_su_md(env):
    aplicar(env)
    editar_registro(env, lambda d: d["agents"].pop(1))
    aplicar(env)
    assert not (env.agents_dir / "codex-forja.md").exists()


def test_md_huerfano_en_su_carpeta_se_borra(env):
    aplicar(env)
    (env.agents_dir / "viejo.md").write_text("de un agente que ya no existe")
    plan = build_plan(env)
    assert [c.kind for c in plan.changes if c.path.name == "viejo.md"] == ["borrar"]
    apply_plan(plan, "limpieza", run_tests=ok_tests)
    assert not (env.agents_dir / "viejo.md").exists()


def test_agente_manual_se_copia_tal_cual(env):
    manual = "---\nname: codex-critico\ndescription: hecho a mano\ntools: Bash\n---\n\nmano\n"
    (env.manual_dir / "codex-critico.md").write_text(manual)
    editar_registro(env, lambda d: d["agents"][2].update(manual=True))
    aplicar(env)
    assert (env.agents_dir / "codex-critico.md").read_text() == manual


def test_manual_deshabilitado_se_retira_pero_queda_en_manual(env):
    (env.manual_dir / "codex-critico.md").write_text("---\nname: codex-critico\n---\nx\n")
    editar_registro(env, lambda d: d["agents"][2].update(manual=True))
    aplicar(env)
    editar_registro(env, lambda d: d["agents"][2].update(enabled=False))
    aplicar(env)
    assert not (env.agents_dir / "codex-critico.md").exists()
    assert (env.manual_dir / "codex-critico.md").exists()


def test_manual_sin_archivo_es_error(env):
    editar_registro(env, lambda d: d["agents"][2].update(manual=True))
    assert any("manual/codex-critico.md" in e for e in build_plan(env).errors)


def test_apply_hace_backup_y_commit_con_changelog_en_el_mismo_commit(env):
    res = aplicar(env, "Primer deploy")
    assert res.applied and res.commit
    assert git(env.home, "log", "-1", "--format=%s") == "Primer deploy"
    log = (env.home / "CHANGELOG.md").read_text()
    assert "Primer deploy" in log and "+ agente `codex-forja`" in log
    assert git(env.home, "status", "--porcelain") == ""
    assert res.backup and res.backup.exists()


def test_si_fallan_los_tests_restaura_todo_y_no_commitea(env):
    (env.claude_dir / "CLAUDE.md").write_text("mis reglas\n")
    antes = snapshot(env.claude_dir)
    head = git(env.home, "rev-parse", "HEAD")
    plan = build_plan(env)
    with pytest.raises(TestsFailed):
        apply_plan(plan, "no debería quedar", run_tests=bad_tests)
    assert snapshot(env.claude_dir) == antes
    assert git(env.home, "rev-parse", "HEAD") == head
    assert not (env.home / "CHANGELOG.md").exists()


def test_segundo_apply_sin_cambios_no_hace_nada(env):
    aplicar(env)
    head = git(env.home, "rev-parse", "HEAD")
    plan = build_plan(env)
    assert plan.empty
    assert apply_plan(plan, "nada", run_tests=ok_tests).applied is False
    assert git(env.home, "rev-parse", "HEAD") == head


def test_cambio_en_protegido_sin_confirmacion_bloquea(env):
    aplicar(env)
    editar_registro(env, lambda d: d["agents"][3].update(prompt="nuevo prompt"))
    plan = build_plan(env)
    assert [v.agent for v in plan.violations] == ["mantenedor"]
    with pytest.raises(GuardError, match="--confirm mantenedor"):
        apply_plan(plan, "x", run_tests=ok_tests)
    plan2 = build_plan(env, confirmations={"mantenedor"})
    assert plan2.violations == []
    assert apply_plan(plan2, "confirmado por el usuario", run_tests=ok_tests).applied


def test_el_guard_usa_lo_desplegado_aunque_se_commitee_a_mano(env):
    aplicar(env)
    editar_registro(env, lambda d: d["agents"][3].update(prompt="cambio colado"))
    git(env.home, "commit", "-qam", "cambio a mano")
    assert [v.agent for v in build_plan(env).violations] == ["mantenedor"]


def test_secreto_en_un_prompt_bloquea_el_deploy(env):
    editar_registro(env, lambda d: d["agents"][1].update(
        prompt="usá la clave sk-proj-abcdefghijklmnopqrstuvwxyz123456"))
    plan = build_plan(env)
    assert any("secreto" in e for e in plan.errors)
    with pytest.raises(GenerateError):
        apply_plan(plan, "x", run_tests=ok_tests)


def test_conserva_lo_que_habia_en_claude_md(env):
    (env.claude_dir / "CLAUDE.md").write_text("# Mis reglas\nNo toques esto.\n")
    aplicar(env)
    texto = (env.claude_dir / "CLAUDE.md").read_text()
    assert texto.startswith("# Mis reglas\nNo toques esto.\n")
    assert START in texto


def test_orquestacion_desactivada_deja_una_linea(env):
    editar_registro(env, lambda d: d["orchestrator"].update(enabled=False))
    aplicar(env)
    inner = extract_inner((env.claude_dir / "CLAUDE.md").read_text())
    assert "\n" not in inner and "desactivada" in inner


def test_marcadores_rotos_en_claude_md_son_error(env):
    (env.claude_dir / "CLAUDE.md").write_text(f"{START}\nsin cierre\n")
    assert any("marcador" in e for e in build_plan(env).errors)


def test_registro_invalido_da_error_legible(env):
    editar_registro(env, lambda d: d["agents"][0].update(name="Mal Nombre"))
    assert any("name" in e for e in build_plan(env).errors)


def test_el_manifiesto_describe_lo_desplegado(env):
    aplicar(env)
    m = json.loads(env.manifest.read_text())
    forja = m["agents"]["codex-forja"]
    assert (forja["backend"], forja["mode"], forja["isolation"]) == ("codex", "write", "worktree")
    assert forja["backend_prompt"] == "Sos la forja."
    assert m["limits"]["max_parallel"]["codex"] == 2
    assert m["agents"]["mantenedor"]["protected"] is True


def test_el_diff_muestra_rutas_legibles(env):
    diff = build_plan(env).diff()
    assert "agents/orquesta/codex-forja.md" in diff and "CLAUDE.md" in diff


def test_un_plan_obsoleto_no_se_aplica(env):
    plan = build_plan(env)
    (env.claude_dir / "CLAUDE.md").write_text("lo cambió otro proceso\n")
    with pytest.raises(GenerateError, match="cambió"):
        apply_plan(plan, "x", run_tests=ok_tests)
