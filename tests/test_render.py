"""Render de los .md de agentes y del bloque de CLAUDE.md."""
from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from helpers import backends_dict, registry_dict
from orquesta.render import RenderContext, render_agent_md, render_block_inner
from orquesta.schema import parse_backends, parse_registry


@pytest.fixture
def ctx(env) -> RenderContext:
    return RenderContext(paths=env, backends=parse_backends(backends_dict()))


def agente(nombre: str, mod=None):
    d = registry_dict()
    if mod:
        mod(d)
    return parse_registry(d).agent(nombre)


def parse_md(md: str) -> tuple[dict, str]:
    assert md.startswith("---\n")
    end = md.index("\n---\n", 4)
    return YAML(typ="safe", pure=True).load(md[4:end]), md[end + 5:]


def test_frontmatter_es_yaml_valido_con_los_campos_generados(ctx):
    fm, _ = parse_md(render_agent_md(agente("codex-forja"), ctx))
    assert fm["name"] == "codex-forja"
    assert fm["tools"] == "Bash"
    assert fm["model"] == "haiku"
    assert "PROACTIVAMENTE" in fm["description"]
    assert "fix mecánico" in fm["description"]
    assert list(fm)[:4] == ["name", "description", "tools", "model"]


def test_descripcion_explicita_se_respeta(ctx):
    a = agente("codex-forja", lambda d: d["agents"][1].update(description="Usalo para X."))
    fm, _ = parse_md(render_agent_md(a, ctx))
    assert fm["description"] == "Usalo para X."


def test_caracteres_especiales_sobreviven_el_yaml(ctx):
    raro = 'Dice "hola": #no-es-comentario, [corchetes] {llaves} \\ barra & más'
    a = agente("gemini-lector", lambda d: d["agents"][0].update(description=raro))
    fm, _ = parse_md(render_agent_md(a, ctx))
    assert fm["description"] == raro


def test_extra_frontmatter_se_emite(ctx):
    hooks = {"Stop": [{"hooks": [{"type": "command", "command": "echo listo"}]}]}
    a = agente("mantenedor", lambda d: d["agents"][3].update(
        extra_frontmatter={"maxTurns": 20, "color": "cyan", "hooks": hooks}))
    fm, _ = parse_md(render_agent_md(a, ctx))
    assert fm["maxTurns"] == 20
    assert fm["color"] == "cyan"
    assert fm["hooks"] == hooks


def test_cuerpo_externo_delega_en_el_runner_sin_comandos_hardcodeados(ctx):
    md = render_agent_md(agente("gemini-lector"), ctx)
    _, body = parse_md(md)
    assert "orquesta run gemini-lector" in body
    assert "agy -p" not in md and "codex exec" not in md
    assert "Sos el lector" in body          # la persona que el runner antepone, visible
    assert "--task" not in body             # read-only: sin worktree


def test_cuerpo_externo_con_worktree_pide_task_y_explica_la_rama(ctx):
    _, body = parse_md(render_agent_md(agente("codex-forja"), ctx))
    assert "--task" in body
    assert "orquesta/codex-forja/" in body


def test_cuerpo_claude_es_el_prompt_con_placeholders_resueltos(ctx, env):
    _, body = parse_md(render_agent_md(agente("mantenedor"), ctx))
    assert f"Repo: {env.home}" in body
    assert str(env.agents_dir) in body
    assert "{orquesta_home}" not in body


def test_marca_de_generado_y_determinismo(ctx):
    a = agente("codex-critico")
    md = render_agent_md(a, ctx)
    assert "Generado por orquesta" in md
    assert md == render_agent_md(a, ctx)


# --- bloque de CLAUDE.md ----------------------------------------------------------------------

def bloque(ctx, mod=None) -> str:
    d = registry_dict()
    if mod:
        mod(d)
    return render_block_inner(parse_registry(d), ctx)


def test_bloque_lista_los_moods_y_el_default(ctx):
    inner = bloque(ctx)
    for nombre, desc in registry_dict()["orchestrator"]["moods"].items():
        assert f"`{nombre}`" in inner and desc in inner
    assert "Mood por defecto: **transparente**" in inner


def test_bloque_override_por_sesion(ctx):
    inner = bloque(ctx)
    assert "mood: <nombre>" in inner
    assert "listá los disponibles" in inner


def test_bloque_tabla_de_ruteo_solo_con_habilitados(ctx):
    inner = bloque(ctx, lambda d: d["agents"][2].update(enabled=False))
    assert "| `gemini-lector` | agy | read-only | none |" in inner
    assert "| `codex-forja` | codex | write | worktree |" in inner
    assert "codex-critico" not in inner


def test_bloque_incluye_las_reglas_de_orquestacion(ctx):
    inner = bloque(ctx)
    for frase in ["Sos el orquestador", "Nunca aceptes output delegado sin verificarlo",
                  "review cruzado", "contexto completo", "orquesta/<agente>/<tarea>",
                  "codex 2, agy 1, claude 3", "rate limit", ".orquesta-off",
                  "Aplicá el mood activo"]:
        assert frase in inner, frase


@pytest.mark.parametrize("tag", [True, False])
def test_bloque_tag_de_mood(ctx, tag):
    inner = bloque(ctx, lambda d: d["orchestrator"].update(show_mood_tag=tag))
    assert ("[mood: <nombre>]" in inner) is tag


def test_orquestacion_desactivada_es_una_sola_linea(ctx):
    inner = bloque(ctx, lambda d: d["orchestrator"].update(enabled=False))
    assert "\n" not in inner
    assert "desactivada" in inner.lower()


def test_bloque_incluye_reglas_basadas_en_evidencia(ctx):
    inner = bloque(ctx)
    for frase in ["evidencia ejecutable", "solo si hay desacuerdo", "brief", "umbral de escalado",
                  "juez de otra familia"]:
        assert frase in inner, frase
