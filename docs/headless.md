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
