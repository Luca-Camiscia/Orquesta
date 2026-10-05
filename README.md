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
| `orquesta plugins list` | Plugins instalados, sus skills y MCP, y qué agentes los usan |
| `orquesta plugins install <owner/repo> <plugin@mkt> --yes` | Instala solo si hay auditoría ok del commit exacto; verifica el SHA |
| `orquesta plugins uninstall <plugin> --yes` | Desinstala; se niega si algún agente lo usa |

`python generate.py` y `python doctor.py` son atajos equivalentes (con `uv run`).

## Conversaciones (UI)

En la pestaña Conversaciones, primera de la UI, hay proyectos (un nombre y un directorio) y dentro de
cada uno conversaciones individuales o reuniones. Cada mensaje corre agentes reales y gasta cuota.

| Modo de reunión | Qué hace |
|---|---|
| rondas | Responden en orden y cada uno ve lo anterior. Con `@agente` le hablás a uno solo |
| moderada | El orquestador decide a quién preguntar (hasta 4 veces) y cierra con una síntesis |
| crítica | Ronda a ciegas en paralelo, después crítica de las respuestas de los demás anonimizadas (A, B, C); veredicto del juez |
| posturas | A favor, en contra y neutral asignadas, réplica y veredicto del juez |
| seccionado | Cada uno cubre un aspecto («Aspectos: a; b; c») y el juez consolida |

- **Escritura solo en worktree.** Cuando un agente write deja cambios, un revisor de **otra familia
  de modelos** hace review automática. «Mergear» queda bloqueado si la review no aprueba, salvo que
  confirmes «mergear igual». También se detectan conflictos con la rama actual y solapamientos entre
  ramas abiertas.
- **Contrato de salida.** Los delegados cierran con `RESULTADO / CONFIANZA / EVIDENCIA / SIGUIENTE
  PASO`, y la UI muestra la confianza, los tokens y el costo de cada mensaje.
- **Controles.** Detener (Esc) mata el grupo de procesos del CLI. Hay notificaciones al terminar,
  exportación a Markdown, vista en columnas, búsqueda y paleta de comandos (Ctrl+K), autocompletado
  de @ y tema claro u oscuro.
- **Pestaña Tareas.** Muestra todos los worktrees abiertos de todos los proyectos.
- Las conversaciones se guardan en `conversaciones/` (fuera de git).

## Setup de worktrees por proyecto

Un worktree nuevo no tiene `.venv`, `node_modules` ni `.env`. Si el proyecto tiene
`.orquesta/proyecto.toml`, el runner lo prepara antes de que trabaje el agente:

```toml
[worktree]
copiar = [".env"]                 # archivos sin versionar que se copian del repo original
setup = "uv sync"                 # corre dentro del worktree; tiene ORQUESTA_PORT único y ORQUESTA_WORKTREE
archive = "docker compose down"   # opcional: corre antes de borrar el worktree
timeout_s = 600
```

Si el setup falla, la corrida termina con `ESTADO: ERROR` y no gasta cuota del backend.

## Cuota

Cada invocación queda en `state/usage.jsonl`. El doctor (chequeo `cuota`) y la pestaña Salud muestran
el uso de las últimas 5 horas por backend y un límite estimado a partir de los rate limits que hubo
antes. Avisan cuando el uso llega al 80% de ese límite.

## Plugins por agente

Cada agente `claude` puede tener `plugins: [id, ...]` en orquesta.yaml. Claude Code instala los plugins
de forma global, así que lo que se asigna por agente es lo que traen: sus skills (frontmatter `skills`,
`plugin:skill`) y sus MCP (`mcp__plugin_<plugin>_<servidor>__*` en `tools`). Agregarle un plugin a un
agente existente es una escalada y requiere `--confirm`. Los wrappers de codex y agy no usan plugins.

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
