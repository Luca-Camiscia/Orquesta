# Invocaciones headless: qué funcionó y qué no

Paso 1 del plan, 2026-10-04. Ubuntu 26.04.1, codex-cli 0.160.0 (npm oficial), agy 1.2.9. Todas las
pruebas corren como subprocess **sin TTY** (pipes), en las mismas condiciones que el runner y el
Bash tool de Claude Code. Para reproducirlas: `scripts/smoke_headless.py` (ver `--help`). Las
invocaciones finales quedaron en `backends.yaml`.

## Resultados

| Variante | Resultado | Latencia |
|---|---|---|
| `codex exec --sandbox read-only "Reply PONG"` | ✅ stdout = `PONG` | 13.7 s |
| `codex exec --sandbox read-only -` (prompt por stdin) | ✅ stdout = `PONG` | 7.0 s |
| `codex exec --sandbox workspace-write -C repo -` (crear archivo) | ✅ lo crea | 12.3 s |
| `codex exec --sandbox read-only -C repo -` (crear archivo) | ✅ no lo crea ("the current directory is read-only") | 18.7 s |
| `codex exec --sandbox workspace-write -C /tmp/…/cwd -` con `touch /tmp/…/afuera/x` (2026-10-12) | ⚠️ lo crea: `/tmp` es escribible en workspace-write | — |
| Lo mismo con `-c sandbox_workspace_write.exclude_slash_tmp=true` y `TMPDIR` privado (2026-10-12) | ✅ `touch $TMPDIR/ok` sale 0 y `touch /tmp/…/afuera/x` sale 1 | — |
| `agy -p "Reply PONG"` | ✅ stdout = `PONG` | 7.3 s |
| `agy --output-format json -p "Reply PONG"` | ✅ `{"status":"SUCCESS","response":"PONG\n",...}` | 7.9 s |
| `agy -p -` con el prompt en stdin | ❌ toma `-` como prompt literal | 7.0 s |
| `agy -p` sin valor + stdin | ❌ exit 2 (`-p` exige valor) | 0.1 s |
| `agy --mode accept-edits -p "crear archivo"` | ✅ lo crea | 10.4 s |
| `agy --mode plan -p "crear archivo"` | ❌ **lo crea igual** (ver abajo) | 28.8 s |
| `bwrap <read-only> -- agy --output-format json -p "leer README y crear archivo"` | ✅ lee el README; la escritura falla con `read-only file system` | 13–19 s |

## Hallazgos

**El bug de stdout vacío sin TTY no se reproduce en agy 1.2.9.** La invocación base devuelve la
respuesta por stdout. Igual queda el chequeo en doctor por si vuelve con una actualización.

**agy no acepta el prompt por stdin.** El runner se lo pasa como argumento de `-p`, siempre al
final porque en agy los flags van antes del prompt. Si el prompt supera `max_arg_chars`, lo escribe
en un archivo temporal y le pasa un prompt corto que apunta a ese archivo. Así se evitan el límite de
128 KiB por argumento de Linux y el de 32 767 caracteres de línea de comando de Windows.

**Salida JSON de agy.** `--output-format json` devuelve `status`, `response` y `usage`. El runner la
usa para detectar fallas sin parsear texto libre.

**Costo de agy.** Un "Reply PONG" consume unos 14 000 tokens de entrada por el system prompt de agy.
Hay reportes públicos de cuotas individuales que se agotan rápido. Por eso los pings de doctor y de
la UI usan `gemini-3.8-flash-low` y quedan cacheados.

**`agy --mode plan` NO es read-only.** Causa raíz:

1. En print mode, `--mode plan` se implementa expandiendo el slash command `/plan`. Es una
   instrucción en el prompt (log: `expanded slash command "plan"`), no una restricción de tools.
2. agy escribe un plan con `requestFeedback: true`. Tu `~/.gemini/antigravity-cli/settings.json`
   tiene `artifactReviewPolicy: always-proceed`, así que la revisión se aprueba sola.
3. agy ejecuta el plan y escribe un `walkthrough.md` ("Created agy_nope.txt"). El mensaje final
   ("Please review the plan…") es engañoso: la escritura ya ocurrió.

Además, esa configuración tiene `toolPermission: always-proceed` y `allowNonWorkspaceAccess: true`:
agy aprueba todas sus tools solo y puede escribir fuera del workspace. Orquesta **no** toca esa
configuración porque es tuya.

**Solución: sandbox del SO (bubblewrap, Linux).** El modo read-only corre agy dentro de `bwrap`, con
todo el filesystem montado read-only salvo `~/.gemini`, `~/.cache` y `/tmp`, que agy necesita para
su estado. Verificado: lee el repo y cualquier escritura falla con `read-only file system`. En modo
write, solo el workspace (el worktree) queda escribible.

**Windows: no hay equivalente a bwrap.** El runner corre agy sin sandbox y compara `git status`
antes y después: si algo cambió, lo reporta como `VIOLACION_READ_ONLY`. Detecta, pero no previene.
Doctor lo marca como warning.

## Otros hallazgos

- El snap `codex` (publisher `jcat-nysasounds`, confinement strict) no es de OpenAI. Se instaló el
  paquete npm oficial, que gana en el PATH. Para sacarlo: `sudo snap remove --purge codex`.
- Los logs de agy avisan que hay reglas de permisos `unsandboxed(...)` inválidas en su configuración:
  se ignoran y no otorgan nada. Es cosmético y no afecta a orquesta.

## Plugin de Claude Code (verificado 2026-10-05)

Spike E2.0/E3.0 de `docs/plugin-plan.md`. Claude Code 2.1.290, `CLAUDE_CONFIG_DIR=~/orquesta/sandbox/claude-plugin`
(config aislada con el login del usuario), plugin de prueba en `sandbox/plugin-spike/` y cwd `sandbox/rrhh-spike/`.
Siempre `claude -p --model haiku --max-turns <=4 </dev/null`.

| # | Supuesto | Resultado | Evidencia |
|---|---|---|---|
| 1 | Los agentes del plugin salen como `<plugin>:<nombre>` | CONFIRMADO. Con `--plugin-dir` y con el plugin instalado: `orquesta:claude-juez`. El plugin figura como `orquesta@inline` (dir) o `orquesta@orquesta-local` (instalado) | `claude -p --plugin-dir mkt/orquesta --output-format stream-json --verbose "hola"`; evento `system/init`: `agents: [..., "orquesta:claude-juez", ...]` |
| 2a | `additionalContext` de un SessionStart clásico llega al modelo | CONFIRMADO | Hook `command` que imprime `{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Palabra clave secreta: ZORZAL-7..."}}`; `claude -p ... "Repetí la palabra clave secreta"` responde `ZORZAL-7` |
| 2b | El stdin del hook trae `agent_type` con `--agent <plugin>:<n>` | CONFIRMADO. Sin `--agent` el campo NO existe | `--agent orquesta:claude-juez`: `{"session_id":..,"cwd":..,"agent_type":"orquesta:claude-juez","hook_event_name":"SessionStart","source":"startup"}`; sin `--agent`, 3 de 3 stdin sin `agent_type`. Ojo: el valor lleva el prefijo `orquesta:` |
| 3 | `permissionDecision:"ask"` de un PreToolUse en headless | FALLA el supuesto de "solo aviso": en `-p` el `ask` se DENIEGA siempre, también con `bypassPermissions`. No se cuelga ni se aprueba | Hook sobre Bash que devuelve `ask`; `claude -p --permission-mode {default,acceptEdits,bypassPermissions} "Corré: echo hola --confirm"`: en los tres, `permission_denials` trae el Bash, el comando no corre y el modelo recibe `spike ask`. El stdin del PreToolUse trae `permission_mode`, `tool_name`, `tool_input.command` |
| 4 | `claude -p --agent <plugin>:<agente>` | CONFIRMADO (con `--plugin-dir` y con el plugin instalado). El agente respeta su prompt y su lista `tools:` | `claude -p --agent orquesta:claude-juez "..."`; con `tools: Read, Grep` no ve Bash ni Write |
| 5a | Marketplace de carpeta: `marketplace add` + `install -s user` | CONFIRMADO, no interactivo | `claude plugin marketplace add <dir>` → "Successfully added marketplace: orquesta-local"; `claude plugin install orquesta@orquesta-local -s user` → "Successfully installed"; `plugin list` muestra `Read from: <dir>/orquesta` |
| 5b | Editar el plugin en la carpeta y que una sesión nueva lo tome sin reinstalar | CONFIRMADO para `source: "./orquesta"` en un marketplace de carpeta: se lee de la carpeta, no de la copia en `plugins/cache` (esa queda vieja). Se verificó con un cambio en el prompt de un agente (`EDITADO-V3-PERA`) y en un script de hook (`ZORZAL-7`→`ZORZAL-8`) | `claude plugin update orquesta@orquesta-local -s user`: "orquesta is read from its folder: nothing to update. Edits there take effect at the next session start or /reload-plugins" |
| 5c | `/reload-plugins` no interactivo | EXISTE y anda en `-p` (también por stream-json). Solo recarga la sesión que lo recibe, no las sesiones vivas de otras terminales | `claude -p "/reload-plugins"` → `Reloaded: 2 plugins · 12 skills · 6 agents · 2 hooks · 0 plugin LSP servers` |
| 5d | Desinstalar y sacar el marketplace | CONFIRMADO | `claude plugin uninstall orquesta@orquesta-local -s user`; `claude plugin marketplace remove orquesta-local`; `settings.json` queda con `enabledPlugins: {}` y `extraKnownMarketplaces: {}` |
| 6a | `--continue` retoma la conversación previa del cwd con `--agent` | CONFIRMADO. Retoma la última del cwd; sin `--continue` no recuerda | cwd `sandbox/rrhh-spike/`: `-p --agent orquesta:claude-juez "Recordá 4242"`, luego `-p --agent orquesta:claude-juez --continue "¿qué número?"` → `4242`; sin `--continue` → "No me has pedido..." |
| 6b | `dontAsk` existe | CONFIRMADO: `--permission-mode` acepta `dontAsk` | `claude --help` (modos: `... "dontAsk", "plan"`) |
| 6c | `--allowedTools "Bash(echo *)" --disallowedTools Edit Write --permission-mode dontAsk` niega lo que no está en la allow-list sin preguntar | CONFIRMADO. `echo hola` corre; `touch x` se deniega y no se crea; `echo a && touch x` también se deniega (el patrón no deja pasar encadenados). Con `default` el mismo `touch x` queda en `permission_denials` (headless no puede preguntar) | `claude -p --agent orquesta:rrhh --allowedTools "Bash(echo *)" --disallowedTools Edit Write --permission-mode dontAsk "Corré con Bash exactamente: touch x"`; `permission_denials` con el Bash, `ls x` → no existe |

No se verificó (requiere TTY interactivo del usuario): el `!` de la sesión interactiva y `/dev/tty` desde el Bash tool.

### Hallazgos extra

- **Fuga de memoria global en la sandbox.** Con cwd bajo `~/`, Claude Code carga `~/.claude/CLAUDE.md` como memoria
  de proyecto de un directorio ancestor, aunque `CLAUDE_CONFIG_DIR` apunte a otro lado (la respuesta salió con `[mood: ejecutivo]`).
  Con cwd en `/tmp` no pasa. Los E2E aislados deben tenerlo en cuenta: el bloque global de orquesta se ve igual.
- La config aislada necesita su propio login (`.credentials.json`); `--bare` no lo usa ("Not logged in").
- Las stdin de hooks incluyen `transcript_path` bajo el `CLAUDE_CONFIG_DIR` activo.

### Consecuencias para el plan

- **Decisión 1 (ubicación, marketplace de carpeta):** se confirma, y mejora: con `source: "./orquesta"` las ediciones del generador se ven
  en la próxima sesión o con `/reload-plugins`, sin `update` ni reinstalar. Agregar la verificación de que `plugin list` diga
  `Read from: <carpeta>` (si el origen fuera otro, la copia en cache queda vieja) como check de doctor `plugin-instalado`.
- **Decisión 2 (SessionStart):** se confirma. Ajustar: `agent_type` viene con prefijo (`orquesta:<n>`), así que `hook session-start`
  debe comparar contra el campo no vacío, no contra un nombre pelado; sin `--agent` la clave no existe.
- **Decisión 3(a) (PreToolUse `ask` = solo aviso): CAMBIA.** En headless el `ask` es un bloqueo duro, incluso con `bypassPermissions`.
  Los subagentes y `claude -p` que lleguen a un `--confirm` quedan denegados, no avisados. Hay que decidir: o es una capa deliberada
  de bloqueo (y el mensaje de `permissionDecisionReason` tiene que decir cómo seguir: correrlo el usuario con `!`), o el hook usa
  `additionalContext`/exit 0 en headless. El hook puede leer `permission_mode` del stdin pero no sabe si hay humano. Recomendación:
  mantener el `ask` (el modelo de amenaza quiere que un error no pase) y documentar que en `-p` equivale a deny.
  (b) (TTY) no se tocó: sigue siendo el freno real.
- **Decisión 7 (RR. HH.):** se confirma. `--continue` desde `state/rrhh/` retoma la conversación del cwd y `dontAsk` + allow-list niega
  sin preguntar (también encadenados con `&&`). El fallback `--resume`/`--session-id` queda como respaldo, no hace falta de entrada.
  Cuidado: `--continue` retoma LA ÚLTIMA conversación del cwd, sea de quien sea; `state/rrhh/` debe usarse solo para RR. HH.
- **E2.2/E2.3:** el plugin generado puede reescribirse in situ; el generate no atómico sigue siendo un riesgo para sesiones que
  recarguen a mitad de la escritura (escribir en tmp y renombrar la carpeta).
