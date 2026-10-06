"""Generador: qué escribe, dónde, y qué pasa cuando algo sale mal."""
from __future__ import annotations

import json

import pytest

from helpers import dump_yaml, git, load_yaml, snapshot
from orquesta import cli
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
    aplicar(env, confirm={"codex-critico"})
    despues = snapshot(env.agents_dir)
    assert "codex-critico.md" not in despues
    for nombre in ("codex-forja.md", "gemini-lector.md", "mantenedor.md"):
        assert despues[nombre] == antes[nombre]


def test_eliminar_agente_borra_su_md(env):
    aplicar(env)
    editar_registro(env, lambda d: d["agents"].pop(1))
    aplicar(env, confirm={"codex-forja"})
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
    aplicar(env, confirm={"codex-critico"})
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


def test_manifiesto_anterior_sin_effort_no_cambia_md_ni_protegidos(env):
    from orquesta.manifest import deployed_agent
    aplicar(env)
    manifest = json.loads(env.manifest.read_text())
    for entry in manifest["agents"].values():
        entry.pop("backend_effort", None)
    env.manifest.write_text(json.dumps(manifest))
    assert deployed_agent(manifest, "codex-forja").backend_effort is None
    plan = build_plan(env)
    assert plan.errors == [] and plan.violations == []
    assert all(c.path.suffix != ".md" for c in plan.changes)


def test_el_diff_muestra_rutas_legibles(env):
    diff = build_plan(env).diff()
    assert "agents/orquesta/codex-forja.md" in diff and "CLAUDE.md" in diff


def test_un_plan_obsoleto_no_se_aplica(env):
    plan = build_plan(env)
    (env.claude_dir / "CLAUDE.md").write_text("lo cambió otro proceso\n")
    with pytest.raises(GenerateError, match="cambió"):
        apply_plan(plan, "x", run_tests=ok_tests)


@pytest.mark.parametrize("case,kind", [
    ("nuevo", "alta"), ("eliminar", "baja"), ("deshabilitar", "baja"),
    ("habilitar", "alta"), ("prompt", "prompt"), ("manual", "prompt"),
    ("sin_hash", "prompt"),
    ("tools", "escalation"), ("isolation", "escalation"),
    ("extra_nueva", "escalation"), ("extra_valor", "escalation"),
    ("role", "escalation"), ("role_protegido", "protected"),
])
def test_nuevas_reglas_salen_con_3_y_confirmadas_se_aplican(env, monkeypatch, capsys, case, kind):
    if case == "habilitar":
        editar_registro(env, lambda d: d["agents"][1].update(enabled=False))
    if case == "manual":
        editar_registro(env, lambda d: d["agents"][1].update(manual=True))
        (env.manual_dir / "codex-forja.md").write_text("versión A\n")
    if case == "extra_valor":
        editar_registro(env, lambda d: d["agents"][1].update(extra_frontmatter={"maxTurns": 10}))
    aplicar(env)
    if case == "nuevo":
        editar_registro(env, lambda d: d["agents"].append(
            {"name": "nuevo", "backend": "codex", "role": "Nuevo", "tools": ["Bash"]}))
    elif case == "eliminar":
        editar_registro(env, lambda d: d["agents"].pop(1))
    elif case in ("habilitar", "deshabilitar"):
        editar_registro(env, lambda d: d["agents"][1].update(enabled=case == "habilitar"))
    elif case == "manual":
        (env.manual_dir / "codex-forja.md").write_text("versión B\n")
    elif case == "sin_hash":
        m = json.loads(env.manifest.read_text())
        m["guard"]["codex-forja"].pop("prompt_sha256", None)
        m["guard"]["codex-forja"].pop("role_hash", None)
        env.manifest.write_text(json.dumps(m))
        editar_registro(env, lambda d: d["agents"][1].update(role="Otro rol"))
    elif case == "tools":
        editar_registro(env, lambda d: d["agents"][1].update(tools=["Bash", "Write"]))
    elif case == "isolation":
        editar_registro(env, lambda d: d["agents"][1].update(isolation="none"))
    elif case in ("extra_nueva", "extra_valor"):
        editar_registro(env, lambda d: d["agents"][1].update(extra_frontmatter={"maxTurns": 20}))
    elif case in ("role", "role_protegido"):
        index = 3 if case == "role_protegido" else 1
        editar_registro(env, lambda d: d["agents"][index].update(role="Otro rol"))
    else:
        editar_registro(env, lambda d: d["agents"][1].update(prompt="nuevo prompt"))
    name = "nuevo" if case == "nuevo" else "mantenedor" if case == "role_protegido" else "codex-forja"
    assert [(v.agent, v.kind) for v in build_plan(env).violations] == [(name, kind)]
    monkeypatch.setenv("ORQUESTA_HOME", str(env.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env.claude_dir))
    # El apply real conserva escrituras y rollback; evitar recursión al correr tests desde CLI.
    real_apply = apply_plan
    monkeypatch.setattr("orquesta.generate.apply_plan",
                        lambda plan, message: real_apply(plan, message, run_tests=ok_tests))
    before = snapshot(env.claude_dir)
    assert cli.main(["generate", "-m", "cambio"]) == 3
    assert f"--confirm {name}" in capsys.readouterr().out
    assert snapshot(env.claude_dir) == before
    assert cli.main(["generate", "-m", "confirmado", "--confirm", name]) == 0
    assert "Aplicado" in capsys.readouterr().out
    assert build_plan(env).empty and build_plan(env).violations == []
    md = env.agents_dir / f"{name}.md"
    assert md.exists() == (case not in ("eliminar", "deshabilitar"))
    if case == "manual":
        assert md.read_text() == "versión B\n"
    elif case == "prompt":
        assert json.loads(env.manifest.read_text())["agents"][name]["backend_prompt"] == "nuevo prompt"


def test_primer_deploy_sin_manifest_no_exige_confirmacion(env):
    editar_registro(env, lambda d: d["agents"][3].update(prompt="nuevo prompt"))
    plan = build_plan(env)
    assert plan.violations == []
    assert "línea base de prompts migrada" not in plan.summary  # no hay nada que migrar
    aplicar(env)


def test_migra_prompts_de_manifest_anterior_y_reporta_una_sola_vez(env):
    import hashlib
    editar_registro(env, lambda d: d["agents"][2].update(enabled=False))
    aplicar(env)
    m = json.loads(env.manifest.read_text())
    for entry in m["guard"].values():
        entry.pop("prompt_sha256", None)
        entry.pop("enabled", None)
    env.manifest.write_text(json.dumps(m))
    plan = build_plan(env)
    assert plan.violations == []
    assert "línea base de prompts migrada" in plan.summary
    result = apply_plan(plan, "migración", run_tests=ok_tests)
    assert "línea base de prompts migrada" in result.summary
    m = json.loads(env.manifest.read_text())
    for a in load_yaml(env.registry)["agents"]:
        entry = m["guard"][a["name"]]
        assert entry["prompt_sha256"] == hashlib.sha256(a["prompt"].encode("utf-8")).hexdigest()
    assert m["guard"]["codex-critico"]["enabled"] is False
    assert build_plan(env).empty
    assert "línea base de prompts migrada" not in build_plan(env).summary


def test_cambiar_solo_route_when_no_pide_confirmacion(env):
    aplicar(env)
    editar_registro(env, lambda d: d["agents"][1].update(route_when=["otro caso"]))
    assert build_plan(env).violations == []
    aplicar(env)


def test_manifiesto_roto_usa_head_y_el_guard_sigue_activo(env):
    aplicar(env)
    env.manifest.write_text("no es json", encoding="utf-8")
    editar_registro(env, lambda d: d["agents"][3].update(prompt="nuevo prompt"))
    plan = build_plan(env)
    assert plan.errors == []
    assert [v.agent for v in plan.violations] == ["mantenedor"]
    plan2 = build_plan(env, confirmations={"mantenedor"})
    assert apply_plan(plan2, "repara el manifiesto", run_tests=ok_tests).applied
    assert json.loads(env.manifest.read_text(encoding="utf-8"))["agents"]


def test_borrar_el_manifiesto_no_apaga_el_guard(env):
    aplicar(env)
    env.manifest.unlink()
    editar_registro(env, lambda d: d["agents"][3].update(prompt="nuevo prompt"))
    assert [v.agent for v in build_plan(env).violations] == ["mantenedor"]


def test_manifest_guarda_linea_base_efectiva_y_hashes(env):
    import hashlib
    editar_registro(env, lambda d: d["agents"][1].update(
        tools=["Read", "Bash"], extra_frontmatter={"maxTurns": 10}))
    aplicar(env)
    entry = json.loads(env.manifest.read_text())["guard"]["codex-forja"]
    assert entry["tools_effective"] == "Read, Bash"
    assert entry["isolation_effective"] == "worktree"
    assert entry["extra_frontmatter_dict"] == {"maxTurns": 10}
    assert entry["extra_frontmatter_sha256"] == hashlib.sha256(b'{"maxTurns": 10}').hexdigest()
    assert entry["role_hash"] == hashlib.sha256(b"Implementa fixes acotados.").hexdigest()


def test_manifest_legacy_migra_campos_sin_falsos_positivos(env):
    aplicar(env)
    m = json.loads(env.manifest.read_text())
    for entry in m["guard"].values():
        for key in ("tools_effective", "isolation_effective", "extra_frontmatter_dict", "role_hash",
                    "extra_frontmatter_sha256"):
            entry.pop(key)
    env.manifest.write_text(json.dumps(m))
    plan = build_plan(env)
    assert plan.errors == [] and plan.violations == []
    assert [c.path for c in plan.changes] == [env.manifest]
    assert apply_plan(plan, "migra la línea base", run_tests=ok_tests).applied
    assert build_plan(env).empty
