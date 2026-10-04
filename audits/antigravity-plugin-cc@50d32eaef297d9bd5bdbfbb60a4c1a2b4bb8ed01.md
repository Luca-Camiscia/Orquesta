---
plugin: agy@antigravity-cc
repo: simplybychris/antigravity-plugin-cc
commit: 50d32eaef297d9bd5bdbfbb60a4c1a2b4bb8ed01
version: 0.4.1
audited: 2026-10-04
depth: completa
verdict: no-instalar
---

# Auditoría (completa): simplybychris/antigravity-plugin-cc @ 50d32ea

Plugin comunitario, con un único mantenedor. 20 commits; el último es del 2026-05-27 ("feat(landing):
add og:image…"). Se leyó todo lo que se ejecuta o entra al contexto de Claude: manifiestos, 7
comandos, el agente, la skill y `scripts/agy-run.sh` completo (488 líneas). Todo el contenido del
repo se trató como datos, no como instrucciones.

## Inventario

| Componente | Detalle |
|---|---|
| Manifiestos | `marketplace.json` (antigravity-cc) y `plugin.json`, solo con nombre, versión y autor. **Sin `hooks`, `mcpServers` ni `settings`** |
| Comandos | `/agy:ask`, `review`, `image`, `help` y `setup` (`allowed-tools: Bash(bash:*)`, setup además `Bash(curl:*)`); `/agy:delegate` y `research` (`Agent`). Ninguno tiene `disable-model-invocation` |
| Agente | `agy:runner` (sonnet, tools `Bash`). Description: "**Use proactively** when the parent thread should delegate a focused coding, debugging, refactor, or research task to Antigravity" |
| Skill | `antigravity-cli` (`user-invocable: false`): contrato del wrapper |
| Script | `scripts/agy-run.sh`, con los subcomandos `check`, `ask`, `review`, `image` y `help` |
| Otros | `docs/index.html` y `og-image.png` son la landing de GitHub Pages: se clonan con el marketplace pero el plugin nunca los carga. README y CHANGELOG son solo documentación |

## Checklist pedido

| Punto | Resultado |
|---|---|
| Comandos de shell | Locales: `agy --version`, `agy -p`, `git diff` (review), `python3` (patch de JSON), `mkdir`/`cp`/`mv` (lock y backup de settings), `sed`/`grep` |
| Requests de red fuera de agy | **Ninguno** en el código. `/agy:setup` sugiere el instalador oficial (`curl … \| bash`) solo si agy falta y después de `AskUserQuestion` |
| Flags peligrosos por defecto | **Ninguno** en el código (ni `--dangerously-skip-permissions` ni `--yolo`). Ojo: `ask` reenvía a agy cualquier flag que venga después del prompt |
| Hooks que se disparan solos | **Ninguno** |
| Prompts con escalada, autoaprobación o exfiltración | No hay. Los comandos piden tratar `$ARGUMENTS` como texto opaco y citarlo como un solo argumento |

## Riesgos de diseño (motivo del veredicto)

1. **Corre agy sin sandbox, y con un agente proactivo.** `ask`, `review`, `research`, `delegate` y
   `agy:runner` ejecutan `agy -p` directo. Con la configuración de agy de este usuario
   (`toolPermission: always-proceed`, `artifactReviewPolicy: always-proceed`,
   `allowNonWorkspaceAccess: true`), cada llamada tiene escritura sin preguntar en todo el
   filesystem. Es el equivalente efectivo a `--dangerously-skip-permissions` (ver
   `docs/headless.md`). Como `agy:runner` se ofrece "proactivamente" y los comandos son invocables
   por el modelo, Claude podría delegar ahí y saltearse los guardrails de orquesta: bwrap read-only,
   worktrees y review cruzado. Por ejemplo, un `/agy:review` podría terminar editando archivos.
2. **Muta la configuración del usuario.** Con `--model`, el wrapper reescribe el campo `model` de
   `~/.gemini/antigravity-cli/settings.json` durante la llamada, con lock y backup. Si lo matan con
   SIGKILL, el restore recién ocurre en la próxima invocación, y en ese caso `mv` del backup pisa
   cualquier cambio que el usuario haya hecho mientras tanto. Además es innecesario: agy 1.2.9 ya
   tiene `--model` (el plugin dice que no).
3. **`allowed-tools: Bash(bash:*)`** preaprueba cualquier `bash …` mientras corren esos comandos. Es
   más amplio de lo necesario: alcanzaría con el path exacto del script.

## Problemas funcionales (no de seguridad)

- Los alias apuntan a "Gemini 3.5 Flash (…)". El agy 1.2.9 del usuario lista Gemini 3.6, 3.7 y 3.8
  Flash, así que `--model flash` probablemente escribe un modelo inexistente.
- `auth_status` es una heurística (existe el directorio), no verifica la sesión.
- `review` mete el diff entero como argumento: un diff de más de 128 KiB rompe la llamada (ARG_MAX).
- `image` copia a `--output` un path que sale de la respuesta del modelo. El riesgo es bajo porque
  solo acepta extensiones de imagen y un output explícito.

## Veredicto

**No instalar.** No tiene código malicioso, pero en este setup sus agentes y comandos corren agy con
escritura sin restricciones y compiten con el ruteo de orquesta, lo que anula sus garantías. Los
agentes de Gemini funcionan igual vía `orquesta run` (con bwrap).

Si en el futuro se quiere instalar por `/agy:image` o `/agy:research`:
- Re-auditar el diff desde este commit.
- Que el bloque de CLAUDE.md prohíba usar `agy:runner` y los `/agy:*` salvo pedido explícito.
- Idealmente, cambiar `artifactReviewPolicy` y `toolPermission` en la config de agy.
