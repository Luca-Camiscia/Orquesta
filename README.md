# orquesta

Ecosistema multi-modelo para Claude Code. Claude (Opus) orquesta y delega en GPT, vía Codex CLI, y
en Gemini, vía Antigravity CLI (`agy`). Usa tus suscripciones y no necesita API keys.

## Cómo funciona

orquesta es un **plugin local de Claude Code** (`orquesta@orquesta-local`). El registro se transforma en
un plugin que Claude Code carga desde `~/orquesta/plugin/orquesta` (carpeta generada e ignorada por git):

```
orquesta.yaml ──generate──▶ ~/orquesta/plugin/orquesta/   agentes, comandos, skill, hooks y contexto
     │                      ~/orquesta/plugin/.orquesta-manifest.json   (config aplicada: la usa el runner)
backends.yaml ──────────▶  orquesta run <agente>          (codex/agy con las reglas en código)
```

- **`orquesta.yaml`** es la fuente de verdad: moods, límites y agentes.
- **`backends.yaml`** guarda las invocaciones headless verificadas (ver `docs/headless.md`). Es el
  único lugar con comandos de backend.
- Los agentes tienen namespace `orquesta:<nombre>` (por ejemplo `orquesta:that-it-guy`).
- Un hook SessionStart inyecta las reglas del orquestador en cada sesión (con la generación del plugin) y un
  hook PreToolUse sobre Bash avisa ante comandos de confirmación (ver «Modelo de amenaza»).
- Los agentes **externos** (codex, agy) son wrappers baratos (haiku) que llaman a `orquesta run`.
  El runner aplica las reglas en código:
  - flags según el modo y sandbox de SO (bwrap) para agy;
  - worktree `orquesta/<agente>/<tarea>` para los agentes write;
  - `max_parallel`, `.orquesta-off` y timeout;
  - detección de rate limit y de escrituras en modo read-only.
- Los agentes **claude** son subagentes nativos (por ejemplo `that-it-guy`, el de mantenimiento).

## Instalación y rollback

```bash
uv tool install -e ~/orquesta   # instala el comando `orquesta`
orquesta migrar estado          # fase actual (legacy o plugin) y qué falta
orquesta migrar corte --confirm # genera e instala el plugin y retira el deploy viejo
orquesta migrar rollback --confirm # desinstala el plugin y restaura el deploy viejo desde el backup fijado
```

`migrar corte` y `migrar rollback` piden confirmación humana por TTY (ver abajo). El corte hace backup,
genera el plugin, lo instala (`claude plugin marketplace add` + `claude plugin install orquesta@orquesta-local -s user`),
verifica y recién después retira `~/.claude/agents/orquesta` y el bloque de `~/.claude/CLAUDE.md`. Es
idempotente: si se corta a medio camino, se vuelve a correr.

Después de cada `orquesta generate`, en la sesión de Claude Code corré `/reload-plugins` (o reiniciá).
Si el contexto de la sesión es de otra generación, el orquestador pide reiniciar y el doctor avisa
(`sesiones-desfasadas`).

## Comandos y skills del plugin

| Comando | Qué hace |
|---|---|
| `/orquesta:mood` | Cambia el mood de la sesión (solo presentación) |
| `/orquesta:plan` | Arma o retoma el plan (interrogador y arquitecto), lo escribe en `.orquesta/plan.md` y pide un único OK |
| `/orquesta:reunion` | Consulta varias perspectivas con `orquesta reunion` en segundo plano |
| `/orquesta:rrhh` | Remite a la sesión de Recursos Humanos (`orquesta rrhh abrir`, en tu terminal) |
| `/orquesta:tareas` | Resume el estado de las tareas con `orquesta tareas --json` |

El skill `orquesta-cli` le explica al modelo el contrato JSON de la CLI (`docs/cli-json.md`).

## Confirmaciones humanas (segunda terminal)

El modelo nunca confirma. `merge --confirm|--forzar`, `descartar --confirm`, `generate --confirm <agente>` y
`migrar corte|rollback --confirm` leen un texto exacto desde `/dev/tty`. El `!` de Claude Code **no tiene
TTY** (`not a tty`), y el Bash tool tampoco: sin TTY la CLI devuelve `estado: NECESITO_CONFIRMACION`,
exit code 3 y el comando exacto. Ese comando lo corrés vos en una **segunda terminal**. No hay variable
de entorno ni flag que saltee el freno.

## Comandos

| Comando | Qué hace |
|---|---|
| `orquesta` / `orquesta ui [--port N] [--no-browser]` | Abre el panel local en http://127.0.0.1:8787 |
| `orquesta generate --dry-run` | Muestra el diff sin escribir |
| `orquesta generate -m "msg"` | Backup → escribe → tests → CHANGELOG + commit (si algo falla, restaura) |
| `orquesta generate --confirm <agente>` | Aplica un cambio protegido o una escalada (solo con OK explícito del usuario, en una segunda terminal) |
| `orquesta doctor [--json]` | Chequeos de salud deterministas |
| `orquesta run <agente> --cwd DIR [--task SLUG] <<'EOF' … EOF` | Delega en el backend del agente; la salida original queda en `state/salidas/` (ruta y sha256 en `salida completa:`) |
| `orquesta run <agente> --ping` | Ping mínimo por el backend |
| `orquesta reunion --modo <modo> --participantes a,b [--aspectos ...] [--cwd DIR] [--titulo T]` | Reunión desde la terminal (pregunta por stdin) |
| `orquesta tareas [--json \| --linea]` | Tareas abiertas del proyecto actual |
| `orquesta ledger anotar <tarea> --progreso\|--sin-progreso [--nota T]` | Anota una ronda; avisa estancamiento (2 sin progreso) y bloquea en el tope (4 sin progreso o 6 rondas) |
| `orquesta ledger ver \| cerrar <tarea> --motivo T` | Estado de las tareas del ledger, o cierre |
| `orquesta usage [--dias N]` | Uso por agente (llamadas, tokens, latencia, errores) como array JSON |
| `orquesta review [--repo R] <rama>` | Review de otra familia de una tarea registrada |
| `orquesta merge [--repo R] [--confirm] [--forzar] <rama>` | Mergea el SHA revisado (con confirmación por TTY) |
| `orquesta descartar [--repo R] [--confirm] <rama>` | Descarta una tarea |
| `orquesta rrhh triage\|evaluar\|catalogo\|sugerir\|reclutador\|validar\|memoria\|nota\|abrir` | Recursos Humanos |
| `orquesta migrar estado\|corte\|rollback` | Transición legacy ↔ plugin |
| `orquesta hook session-start\|pre-bash` | Hooks del plugin (leen el evento por stdin; no se corren a mano) |
| `orquesta wt list \| new \| clean <rama> \| prune` | Worktrees de agentes write |
| `orquesta plugins list` | Plugins instalados, sus skills y MCP, y qué agentes los usan |
| `orquesta plugins install <owner/repo> <plugin@mkt> --yes` | Instala solo si hay auditoría ok del commit exacto; verifica el SHA |
| `orquesta plugins uninstall <plugin> --yes` | Desinstala; se niega si algún agente lo usa |

`python generate.py` y `python doctor.py` son atajos equivalentes (con `uv run`).

## Reuniones y review

Las reuniones corren desde la terminal (`orquesta reunion`, o `/orquesta:reunion`) y gastan cuota.
Las conversaciones viejas de la UI quedan solo en `conversaciones/` y se pueden exportar.

| Modo de reunión | Qué hace |
|---|---|
| rondas | Responden en orden y cada uno ve lo anterior |
| moderada | El orquestador decide a quién preguntar (hasta 4 veces) y cierra con una síntesis |
| crítica | Ronda a ciegas en paralelo, después crítica de las respuestas de los demás anonimizadas (A, B, C); veredicto del juez |
| posturas | A favor, en contra y neutral asignadas, réplica y veredicto del juez |
| seccionado | Cada uno cubre un aspecto y el juez consolida |

- **Escritura solo en worktree.** Cuando un agente write deja cambios, `orquesta review` hace una review
  de **otra familia de modelos**. `orquesta merge` mergea el SHA revisado: exige que la punta de la rama
  sea el SHA revisado y el de los tests, y que el repo esté limpio y en la rama destino. Un commit
  nuevo invalida review y tests.
- **Contrato de salida.** Los delegados cierran con `RESULTADO / CONFIANZA / EVIDENCIA / SIGUIENTE
  PASO`.

## Panel web (UI)

`orquesta ui` abre un panel local sin chat: Agentes, Salud, Historial, Plugins y Tareas (solo lectura:
no mergea ni descarta, eso se hace por la CLI). Aplicar cambios del registro desde la UI sigue pidiendo
confirmación con token de sesión.

## Status line (opcional)

`orquesta tareas --linea` imprime una línea como `0 tareas abiertas (0 en review, 0 bloqueadas)` para el
proyecto del directorio actual. No se instala solo: enchufala a mano en `~/.claude/settings.json`:

```json
{
  "statusLine": { "type": "command", "command": "orquesta tareas --linea" }
}
```

Ese archivo es tuyo: orquesta no lo toca. Claude Code le pasa JSON por stdin al comando; este lo ignora.

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

- Solo escribe en `~/orquesta/plugin/` (generado). `~/.claude` no se toca a mano: el deploy viejo
  (`~/.claude/agents/orquesta` y el bloque de `~/.claude/CLAUDE.md`) lo retira `orquesta migrar corte`.
- Los agentes `protected` y las escaladas (read-only → write, tools de escritura, `isolation: none`,
  `permissionMode` elevado, cambios de prompt, altas y bajas) requieren `--confirm` por TTY. La línea
  base es lo **desplegado**, así que commitear a mano no saltea el guard.
- No escribe nada que parezca un secreto o un token.
- Los moods solo cambian la presentación, nunca la verificación.
- Un archivo `.orquesta-off` en la raíz de un proyecto desactiva la delegación en ese proyecto.

## Modelo de amenaza

El adversario es un **modelo que sigue reglas pero se equivoca**, no uno malicioso. Los controles buscan
que un error no termine en un merge o en un cambio del registro sin que lo veas, y que una evasión
deliberada deje rastro. Controles en capas:

1. **Hook PreToolUse (aviso).** Ante `--confirm`, `--forzar` y patrones de evasión (`python -c …orquesta`,
   `-m orquesta`, `.venv/bin/orquesta`, `script`, `unbuffer`, `pty`) devuelve `ask`. En sesiones
   interactivas te pregunta, salvo en auto mode, donde lo resuelve el clasificador sin preguntarte
   (decisión deliberada); en `claude -p` (delegados headless) `ask` equivale a denegar.
2. **Freno real por `/dev/tty`.** Las confirmaciones leen un texto exacto (rama, agente o «corte») de la
   terminal. Sin TTY no hay forma de confirmar: devuelve `NECESITO_CONFIRMACION` (exit 3). Sin variables
   de entorno que lo saltee.
3. **Merge por SHA revisado.** Lo que se mergea es el commit que revisó otra familia y sobre el que
   pasaron los tests; un commit nuevo invalida ambos.

**Qué NO cubre.** Un modelo con Bash bajo el mismo uid puede evadir cualquier control local, por ejemplo
abriendo un pty para engañar al freno de TTY o editando archivos del repo y del plugin. Ya pasaba antes
con `generate --confirm`. Eso queda fuera del modelo de amenaza; el rastro queda en git, el CHANGELOG y
el manifiesto, y el doctor detecta desfasajes entre lo generado y lo instalado. Detalle de la decisión en
`docs/plugin-plan.md`.

### Sandbox de Bash de Claude Code (opción c): no se usa en v1

El sandbox de Bash de Claude Code no está activado ni configurado en v1 (decisión del 2026-10-05, solo
documentado). Motivos: (a) el riesgo modelado es el error, y ese lo cubren el hook, el freno de TTY y el
merge por SHA; (b) habría que configurarlo en `~/.claude/settings.json`, fuera del alcance de orquesta;
(c) no se evaluó cómo convive con `/dev/tty`, los worktrees y `state/` (hipótesis sin verificar). Queda
como mejora futura. El sandbox del runner para agy (bwrap) es otra cosa y sigue vigente.

## Estructura

```
orquesta.yaml  backends.yaml  CHANGELOG.md  PLAN.md  AGENTS.md
src/orquesta/   registro y generador (schema, config, render, plugin_render, guard, manifest, generate),
                runner (runner, worktrees, procs), CLI (cli, cli_json, confirmar, hooks), tareas y revisión
                (tareas, revision), migración (migracion, migracion_cmd), doctor, rrhh, chat, ui/
tests/          suite de pytest (usa directorios temporales: nunca toca ~/.claude)
audits/         auditorías de plugins (<repo>@<sha>.md)
manual/         .md de agentes manual: true
docs/           headless.md, cli-json.md, plugin-brief.md, plugin-plan.md, investigacion.md
scripts/        smoke_headless.py
plugin/         plugin generado (ignorado por git)
state/ backups/ worktrees/ sandbox/   (ignorados por git)
```

## Desarrollo

```bash
uv run pytest                      # suite completa
PYTHONPATH=$PWD/src uv run pytest  # dentro de un worktree
```

## Desinstalar

```bash
orquesta migrar rollback --confirm  # desinstala el plugin y el marketplace, y restaura el deploy viejo
uv tool uninstall orquesta
claude plugin uninstall codex@openai-codex && claude plugin marketplace remove openai-codex
rm -r ~/orquesta
```
