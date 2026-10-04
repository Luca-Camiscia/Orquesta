---
plugin: codex@openai-codex
repo: openai/codex-plugin-cc
commit: db52e28f4d9ded852ab3942cea316258ae4ef346
version: 1.0.6
audited: 2026-10-04
depth: liviana
verdict: ok
---

# Auditoría (liviana): openai/codex-plugin-cc @ db52e28

Plugin oficial de OpenAI. La auditoría es liviana: manifiestos, hooks, frontmatter de comandos y
agentes, y búsqueda dirigida en los scripts. Sirve sobre todo de **línea base** para que `orquesta
doctor` detecte cuándo el commit instalado deja de ser el auditado.

Último commit: 2026-07-07, "Remove shell expansion for git commands (#447)". 63 archivos.

## Qué instala

- **Comandos** `/codex:review`, `adversarial-review`, `rescue`, `transfer`, `status`, `result`,
  `cancel` y `setup`. Todos salvo `rescue` y `setup` tienen `disable-model-invocation: true`: solo
  los invocás vos.
- **Agente** `codex-rescue` (sonnet, tools: `Bash`). Su description dice "Proactively use when
  Claude Code is stuck…" → **compite con el ruteo de orquesta** (`codex-forjador`). El bloque de
  CLAUDE.md aclara que los agentes de plugins no forman parte del ruteo salvo pedido explícito.
- **Skills internas** (`user-invocable: false`): runtime de la CLI, manejo de resultados y prompting.

## Hooks que se disparan solos

| Hook | Script | Qué hace | Riesgo |
|---|---|---|---|
| SessionStart (5 s) | `session-lifecycle-hook.mjs` | Agrega a `CLAUDE_ENV_FILE` el id de sesión, el path del transcript y el directorio de datos del plugin | Bajo: no hay red ni ejecución externa |
| SessionEnd (5 s) | idem | Apaga el broker local y limpia los jobs de la sesión | Bajo |
| Stop (900 s) | `stop-review-gate-hook.mjs` | **No-op salvo** `stopReviewGate: true` (por defecto `false`). Si lo activás, cada vez que Claude termina corre una review de Codex que puede bloquear el stop | Medio si se activa: el README avisa que "may drain usage limits quickly" |

**Recomendación:** dejar el review gate apagado. Orquesta ya define el review cruzado.

## Ejecución y red

- **Red:** el plugin no hace requests HTTP. Solo usa sockets Unix locales (`net.createConnection({path})`)
  hacia el broker `codex app-server`. Todo el tráfico de red pasa por la CLI `codex`.
- **Procesos:** lanza `codex app-server`, workers de node (`process.execPath`) y `spawnSync` genérico
  en `lib/process.mjs`.
- **Sandbox de Codex:** `read-only` por defecto, `workspace-write` solo en tareas de escritura.
  `approvalPolicy: "never"` porque no es interactivo y queda dentro del sandbox.
- **Flags peligrosos:** no aparecen `danger-full-access`, `--dangerously-bypass-approvals-and-sandbox`
  ni `--full-auto`.
- **`allowed-tools`:** `Bash(node:*)` en casi todos los comandos, más `Bash(git:*)` en reviews y
  `Bash(npm:*)` en setup. Es amplio (node puede ejecutar cualquier cosa), pero solo rige mientras
  corre el comando. `/codex:setup` puede ofrecer `npm install -g @openai/codex` si no encuentra codex.
- **Privacidad:** `/codex:transfer` manda el transcript de la sesión de Claude a Codex (OpenAI).
  Solo se ejecuta si lo invocás vos.
- **Estado:** en `CLAUDE_PLUGIN_DATA`, o en `$TMPDIR/codex-companion` como fallback.

## Veredicto

**OK para instalar.** No hay red propia, flags peligrosos ni hooks activos por defecto. Hay dos
puntos de atención: el agente proactivo `codex-rescue` (lo aclara el bloque de CLAUDE.md) y el review
gate opcional (dejarlo apagado).
