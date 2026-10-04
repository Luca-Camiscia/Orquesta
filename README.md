# orquesta

Ecosistema multi-modelo para Claude Code. Claude (Opus) orquesta y delega en GPT, vía Codex CLI, y
en Gemini, vía Antigravity CLI (`agy`). Usa tus suscripciones y no necesita API keys.

## Cómo funciona

```
orquesta.yaml ──generate──▶ ~/.claude/agents/orquesta/*.md   (subagentes de Claude Code)
     │                      ~/.claude/CLAUDE.md              (solo el bloque entre marcadores)
     │                      .orquesta-manifest.json          (config aplicada: la usa el runner)
backends.yaml ──────────▶  orquesta run <agente>             (codex/agy con las reglas en código)
```

- **`orquesta.yaml`** es la fuente de verdad: moods, límites y agentes.
- **`backends.yaml`** guarda las invocaciones headless verificadas (ver `docs/headless.md`). Es el
  único lugar con comandos de backend.
- Los agentes **externos** (codex, agy) son wrappers baratos (haiku) que llaman a `orquesta run`.
  El runner aplica las reglas en código:
  - flags según el modo y sandbox de SO (bwrap) para agy;
  - worktree `orquesta/<agente>/<tarea>` para los agentes write;
  - `max_parallel`, `.orquesta-off` y timeout;
  - detección de rate limit y de escrituras en modo read-only.
- Los agentes **claude** son subagentes nativos (por ejemplo `that-it-guy`, el de mantenimiento).

## Comandos

| Comando | Qué hace |
|---|---|
| `orquesta` | Abre la UI local en http://127.0.0.1:8787 |
| `orquesta generate --dry-run` | Muestra el diff sin escribir |
| `orquesta generate -m "msg"` | Backup → escribe → tests → CHANGELOG + commit (si algo falla, restaura) |
| `orquesta generate --confirm <agente>` | Aplica un cambio protegido o una escalada (solo con OK explícito del usuario) |
| `orquesta doctor [--json]` | Chequeos de salud deterministas |
| `orquesta run <agente> --cwd DIR [--task SLUG] <<'EOF' … EOF` | Delega en el backend del agente |
| `orquesta run <agente> --ping` | Ping mínimo por el backend |
| `orquesta wt list \| new \| clean <rama> \| prune` | Worktrees de agentes write |

`python generate.py` y `python doctor.py` son atajos equivalentes (con `uv run`).

## Guardrails

- Solo escribe en `~/.claude/agents/orquesta/` y en el bloque marcado de `~/.claude/CLAUDE.md`.
- Los agentes `protected` y las escaladas (read-only → write, permissionMode elevado) requieren
  `--confirm`. La línea base es lo **desplegado**, así que commitear a mano no saltea el guard.
- No escribe nada que parezca un secreto o un token.
- Los moods solo cambian la presentación, nunca la verificación.
- Un archivo `.orquesta-off` en la raíz de un proyecto desactiva la delegación en ese proyecto.

## Estructura

```
orquesta.yaml  backends.yaml  CHANGELOG.md  PLAN.md
src/orquesta/   paths, catalog, schema, config, render, claude_md, guard, manifest,
                generate, runner, worktrees, doctor, cli, ui/
tests/          suite de pytest (usa directorios temporales: nunca toca ~/.claude)
audits/         auditorías de plugins (<repo>@<sha>.md)
manual/         .md de agentes manual: true
docs/           headless.md (evidencia del paso 1)
scripts/        smoke_headless.py
state/ backups/ worktrees/ sandbox/   (ignorados por git)
```

## Desarrollo

```bash
uv run pytest                      # suite completa
uv tool install -e ~/orquesta      # instala el comando `orquesta` (editable)
```

## Desinstalar (todo lo que quedó fuera de esta carpeta)

```bash
uv tool uninstall orquesta
rm -r ~/.claude/agents/orquesta
# borrar el bloque entre <!-- orquesta:start --> y <!-- orquesta:end --> de ~/.claude/CLAUDE.md
claude plugin uninstall codex@openai-codex && claude plugin marketplace remove openai-codex
rm -r ~/orquesta
```
