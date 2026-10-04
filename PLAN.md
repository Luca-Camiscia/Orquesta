# Plan de orquesta

Plan aprobado el 2026-10-04. Decisiones: **1a** `~/orquesta/` con lo mínimo en `~/.claude/`;
**2a** codex oficial por npm (se reemplaza el snap de terceros); **3a** auditoría liviana también del
plugin oficial de Codex.

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
- [ ] 2. Plugin oficial de Codex (auditoría liviana + install)
- [ ] 3. Plugin de Antigravity (auditoría completa → install si está limpio)
- [ ] 4. Registro, generador y tests (TDD) + `uv tool install -e`
- [ ] 5. Doctor + that-it-guy
- [ ] 6. UI local
- [ ] 7. E2E (puede requerir `/exit` + `claude --continue` para cargar los agentes)
- [ ] 8. Resumen final
