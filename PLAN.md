# Plan de orquesta

Plan aprobado el 2026-10-04. Decisiones: **1a** `~/orquesta/` con lo mínimo en `~/.claude/`;
**2a** codex oficial por npm (se reemplaza el snap de terceros); **3a** auditoría liviana también del
plugin oficial de Codex.

> **Estado (2026-10-06):** la fase 2 (plugin de Claude Code) está implementada y en producción. Lo de
> `~/.claude/agents/orquesta` y el bloque de `~/.claude/CLAUDE.md` descritos abajo es el diseño original
> (fase 1) y ya fue retirado por `orquesta migrar corte`. El diseño vigente está en `docs/plugin-plan.md`.

## Dónde vive cada cosa

- `~/orquesta/`: repo git y paquete Python (registro, generador, doctor, runner, UI, tests,
  auditorías). También los temporales (`sandbox/`, `worktrees/`), ignorados por git.
- Fuera de la carpeta, solo lo que Claude Code necesita para cargar la orquesta:
  - `~/.claude/agents/orquesta/*.md`: subcarpeta exclusiva de orquesta.
  - `~/.claude/CLAUDE.md`: solo el bloque entre `<!-- orquesta:start -->` y `<!-- orquesta:end -->`.
  - plugins instalados con `claude plugin`, y el shim `~/.local/bin/orquesta` de `uv tool`.

## Decisiones de diseño

1. **Runner único `orquesta run <agente>`.** Los `.md` de agentes externos lo llaman. Lee
   `backends.yaml` en cada ejecución y la configuración aplicada (manifiesto del deploy). Aplica flags
   por `mode`, timeout, `max_parallel` (lock files), `.orquesta-off`, detección de rate limit y, en
   read-only, la comparación de `git status` antes y después. El prompt va por stdin.
2. **Worktrees** en `worktrees/<proyecto>-<hash>/<agente>--<tarea>`, rama `orquesta/<agente>/<tarea>`.
   El backend edita y el runner commitea en la rama. Un registro en `state/` le permite a doctor
   detectar huérfanos.
3. **`that-it-guy` con `isolation: none`.** Su red de seguridad es dry-run → diff → tests → commit.
4. **Guardrails en código.** `generate` compara contra HEAD y rechaza cambios en agentes `protected` y
   escaladas read-only → write, salvo con `--confirm <agente>`.
5. **Confirmaciones de subagentes.** El subagente devuelve `NECESITO CONFIRMACIÓN` con el diff
   propuesto. El orquestador le pregunta al usuario y lo re-invoca.
6. **Agentes `manual: true`.** Su `.md` vive en `manual/` y se copia tal cual.
7. **`enabled: false`.** La línea única del bloque también prohíbe delegar automáticamente.
8. **CHANGELOG en el mismo commit** (`generate -m`). El rollback es `git revert` + regenerar.
9. **Seguridad de la UI.** Bind a 127.0.0.1, validación de `Host`, token por sesión, hash optimista
   del YAML y `ruamel.yaml` para conservar comentarios.
10. **Doctor.** Hace primero los chequeos gratis y cachea 10 minutos. También chequea binarios no
    oficiales en PATH, `orquesta` en PATH y que Claude Code cargue los agentes.
11. **Plugins.** El SHA instalado tiene que coincidir con el auditado.
12. **Rutas de Claude Code inyectables** (respeta `CLAUDE_CONFIG_DIR`). Los tests nunca tocan `~/.claude`.

## Alcance de plataforma

Linux es la prioridad. Windows quedó fuera de las prioridades (pedido del usuario el 2026-10-04):
se escribe Python portable cuando no cuesta nada extra, pero no se diseña ni se prueba para Windows.

## Progreso

- [x] 0. Diagnóstico y carpeta — codex oficial 0.160.0 (npm), ambos autenticados
- [x] 1. Smoke test headless → `backends.yaml`, `docs/headless.md` (agy read-only vía bwrap: `--mode plan` no alcanza)
- [x] 2. Plugin oficial de Codex (auditoría liviana + install) — instalado, SHA db52e28 = auditado
- [x] 3. Plugin de Antigravity (auditoría completa) — veredicto no-instalar (corre agy sin sandbox + agente proactivo)
- [x] 4. Registro, generador y tests (TDD) + `uv tool install -e` — 165 tests, deploy inicial 04c71df, Claude Code carga los 4 agentes, pings OK
- [x] 5. Doctor + that-it-guy — 18 ok · 3 warn reales (snap de codex, parches de node y python)
- [x] 6. UI local — probada en navegador real con Playwright (copia aislada), 229 tests
- [x] 7. E2E — 7.1–7.5 OK (override de mood por sesión: lo prueba el usuario escribiendo `mood: <nombre>`)
- [x] 8. Resumen final

## Paso 7: cómo retomarlo después de reiniciar la sesión

Claude Code solo carga `~/.claude/agents/orquesta/` y el bloque de CLAUDE.md al arrancar (el directorio
de agentes es nuevo: el watcher no existía). Por eso el paso 7 corre en una sesión reiniciada con
`claude --continue`. Orden de los tests:
1. Mantenimiento: doctor → desincronizar a mano gemini-bibliotecario.md → that-it-guy lo detecta y
   lo arregla con su flujo completo → doctor ok.
2. Guardrails: pedirle a that-it-guy que se agregue WebFetch → tiene que frenar con NECESITO CONFIRMACIÓN.
3. Agentes: crear gemini-cronista (agy, read-only, documentación) → aplicar → verificar el .md y la fila
   de ruteo → deshabilitarlo → aplicar → verificar que se borra y que el resto queda igual.
4. Moods: default = ejecutivo desde la UI → verificar el bloque. El usuario prueba `mood: debate` y un
   mood inexistente.
5. Flujo real en ~/orquesta/sandbox/empanometro (repo git): bibliotecario diagnostica, forjador
   arregla en su worktree, abogado del diablo revisa, el orquestador verifica con pytest y mergea.
   Presentado en mood debate.

## Fase 2: plugin de Claude Code (implementada el 2026-10-06)

Aprobada el 2026-10-05. Brief en `docs/plugin-brief.md`; plan, decisiones técnicas, modelo de amenaza,
resultados del spike, riesgos y progreso por etapas en `docs/plugin-plan.md`. Orquesta es hoy el plugin
local `orquesta@orquesta-local` (generado en `~/orquesta/plugin/orquesta`, agentes `orquesta:<nombre>`,
comandos `/orquesta:mood|plan|reunion|rrhh|tareas`). Corte real hecho por el usuario el 2026-10-06. La UI
quedó como panel sin chat. Resumen para el usuario en `README.md`.
