# Plan: orquesta como plugin local de Claude Code (capa fina)

Aprobado por el usuario el 2026-10-05. Entrada: `docs/plugin-brief.md` (brief del interrogador tras dos
rondas de preguntas). Autor: `claude-arquitecto`; review adversarial: `codex-abogado-del-diablo`
(12 hallazgos, todos incorporados). Decisiones del usuario sobre las preguntas del arquitecto:

- Cambio de `prompt` de un agente no protegido = violación del guard (`--confirm`); solo `route_when`/`avoid_when`/`description` no.
- `enabled: true→false` = baja; `false→true` = alta. `descartar` exige confirmación.
- `/login` en los `CLAUDE_CONFIG_DIR` aislados lo hace el usuario; nunca se copian credenciales.
- Status line: `orquesta tareas --linea`, enchufada a mano en `statusLine`. La API de hooks early access queda fuera de v1.
- Conversaciones viejas: solo en disco y export.
- Sandbox de Bash de Claude Code (opción c): no en v1, solo documentado.
- Escaladas que también exigen `--confirm` (E1.1b, decisión 2026-10-06): agregar tools de escritura (`Write`, `Edit`, `NotebookEdit`, `Bash`) o `"*"`, `isolation: worktree → none`, claves nuevas o cambiadas en `extra_frontmatter`, y cambios de `role`.
- `!` no tiene TTY (probado por el usuario: `not a tty`): las confirmaciones se corren en una segunda terminal. Sin bandeja web.

## Modelo de amenaza

El adversario es un modelo que sigue reglas pero se equivoca, no uno malicioso. Un modelo con Bash bajo
el mismo uid puede saltear cualquier control local (el hueco ya existía con `generate --confirm`). Los
controles garantizan que un error no termine en un merge o un cambio del registro sin que el usuario lo
vea, y que una evasión deliberada deje rastro.

## Decisiones técnicas

1. **Ubicación.** `~/orquesta/plugin/` (gitignoreado) es un marketplace de carpeta (`orquesta-local`)
   con el plugin en `plugin/orquesta/` y el manifiesto en `plugin/.orquesta-manifest.json`. Se instala
   una vez (`claude plugin marketplace add` + `claude plugin install orquesta@orquesta-local -s user`)
   dentro de `orquesta migrar corte`; cada `generate` llega con `/reload-plugins` o reinicio.
2. **Reglas del orquestador.** Hook clásico SessionStart → `<orquesta> hook session-start`: con
   `agent_type` no inyecta; con `.orquesta-off` inyecta `DISABLED_LINE`; si no, `additionalContext` =
   `context/orquestador.md` (generado, con hash). El contexto lleva `Generación: <sha8>`; todo sobre JSON
   trae `generacion`; si no coinciden, el orquestador pide reiniciar. doctor: `sesiones-desfasadas`.
3. **Confirmaciones en capas.** (a) Hook PreToolUse `ask` ante `--confirm`/`--forzar` y patrones de
   evasión (`python -c …orquesta`, `-m orquesta`, `.venv/bin/orquesta`, `script`, `unbuffer`, `pty`):
   solo aviso. (b) Freno real: `merge --confirm|--forzar`, `descartar --confirm`, `generate --confirm`,
   `migrar corte|rollback --confirm` y la confirmación de línea base leen de `/dev/tty` un texto
   (rama, agente o "corte"). Sin TTY (el Bash tool no tiene) → `NECESITO_CONFIRMACION`, exit 3 y el
   comando exacto para que el usuario lo corra en una segunda terminal (`!` no tiene TTY). Sin variable de entorno que lo saltee; en tests el
   lector se inyecta. `/api/apply` de la UI conserva su `confirm` (clic del usuario + token).
4. **CLI.** Sobre `{"version":1,"comando","estado","generacion","datos","errores","avisos"}`, exit
   0/1/3 (`docs/cli-json.md`). Comandos: `reunion`, `review`, `merge`, `descartar`,
   `tareas [--json|--linea]`, `rrhh triage|evaluar|catalogo|sugerir|reclutador|validar|memoria|nota|abrir`,
   `hook session-start|pre-bash`, `migrar estado|corte|rollback`.
5. **chat.py.** Reuniones sobre el Store actual (`Store.project_for_dir`, `origen=terminal`). Review y
   merge extraídos a `revision.py` + `tareas.py`. Registro por tarea: `repo`, `rama`, `base_sha`,
   `destino`, `sha_revisado`, `tests_sha`, `tests_ok`, `veredicto`, `revisor`, `status`. `merge` exige
   tip = `sha_revisado` = `tests_sha`, `base_sha` ancestro del destino, repo en `destino` y árbol limpio,
   y mergea el SHA. Un commit nuevo invalida review y tests. Identidad de tarea = repo + rama; ambigüedad
   → error. Reuniones: `.orquesta-off` antes de ejecutar y slot de claude en `default_executor`; si el
   juez es de una familia participante, aviso (estado OK).
6. **Manifiesto y fase.** Fase por claude_dir en `state/migracion/<sha12(claude_dir)>.json`, backup
   fijado fuera de la rotación. `manifest.load` por fase, sin fallback; ausente o corrupto →
   `ManifestError` (runner `ESTADO: ERROR`, doctor fail), salvo el primer deploy legacy. Línea base de
   prompts explícita: sin `prompt_sha256` y con `entry_sha256` cambiado → violación "línea base ausente".
7. **RR. HH.** Consulta silenciosa determinista (`rrhh triage --json` + `rrhh memoria --json`), sin
   SendMessage. `rrhh abrir` usa `subprocess` con cwd `state/rrhh/` y fallback `--continue` →
   `--resume <id>` → `--session-id` nuevo; flags `--allowedTools "Bash(orquesta rrhh *)"`,
   `--disallowedTools Edit Write NotebookEdit`, `--permission-mode dontAsk` (a validar). Memoria solo por
   `rrhh nota`. El agente `rrhh` es protegido, read-only, sin Edit/Write.

## Etapas

Reglas comunes: worktree `orquesta/<agente>/<tarea>`, `PYTHONPATH=$PWD/src uv run pytest`, tmp dirs y
`CLAUDE_CONFIG_DIR` inyectado, español rioplatense, sin dependencias nuevas, `uv run pytest` verde en main.

| Paso | Agente | Qué | Depende de |
|---|---|---|---|
| E0.1 | codex-tester | Caracterización: `NeedsForce` (3 causas), regex de veredicto, `_reviewer_for`, `_judge_for` (incluye 3 familias), golden de `render_agent_md`/`render_block_inner` | — |
| E0.2 | codex-refactorista | Extraer `revision.py` sin cambio de comportamiento | E0.1 |
| E1.1 | codex-forjador | Guard: `alta`, `baja`, `prompt`, enabled, línea base explícita | — |
| E1.1b | codex-forjador | Guard: escaladas por tools, isolation, extra_frontmatter y role | E1.1 |
| E1.2 | codex-forjador | `cli_json.py`, `docs/cli-json.md`, `confirmar.py` (TTY) | — |
| E1.2b | codex-forjador | `generate --confirm` pasa por `confirmar.py` | E1.2 |
| E1.3 | codex-forjador | `tareas.py`, `_find` con repo, merge por SHA, CLI review/merge/descartar/tareas | E0.2, E1.2 |
| E1.4 | codex-forjador | `Store.project_for_dir`, CLI reunion con `.orquesta-off`, slots y aviso de juez | E1.2 |
| E1.5 | codex-forjador | CLI rrhh (validar no escribe) | E1.2 |
| E2.0 | that-it-guy | Spike en config aislado: namespace, `additionalContext`, `agent_type`, `ask`, `--agent orquesta:claude-juez`, marketplace + reload, `!` con TTY | /login del usuario |
| E2.1 | codex-forjador | Paths del plugin, `migracion.py`, manifest por fase sin fallback | — |
| E2.2 | codex-forjador | `plugin_render.py` (plugin.json, marketplace, commands, skill, hooks, contexto con Generación) | E2.0, E2.1 |
| E2.3 | codex-forjador | `generate` por fase, lock compartido, sesiones vivas | E2.2 |
| E2.4 | codex-forjador | `hooks.py` session-start y pre-bash | E2.0 |
| E2.5 | codex-forjador | doctor: plugin-generado, plugin-instalado, deploy-viejo, manifiesto-fase, sesiones-desfasadas | E2.3 |
| E2.6 | that-it-guy | Prompt de that-it-guy (protegido: confirma el usuario) | E2.2 |
| E3.0 | that-it-guy | Spike: `--continue` + `--agent` + `-n`, allowed/disallowed, `dontAsk` | /login |
| E3.1 | codex-forjador | `rrhh abrir` (subprocess + fallback), `nota`, `memoria` | E3.0 |
| E3.2 | that-it-guy | Agente `rrhh` protegido (confirma el usuario) | E3.1 |
| E4.1 | codex-forjador | UI sin Conversaciones ni merge/discard; Tareas solo lectura; export queda | E1.3 |
| E5.1 | codex-forjador | `migrar` con diario idempotente, lock, verificación antes de retirar | E2.3 |
| E5.2 | that-it-guy | E2E aislado (ORQUESTA_HOME clonado) en sandbox/empanometro, criterios 2-10 + casos nuevos | todo |
| E5.3 | usuario | Corte real y doctor | E5.2 |
| E5.4 | that-it-guy | Docs: README, PLAN, AGENTS, modelo de amenaza, sandbox (c), snippet de statusLine | E5.3 |

Reviews: el orquestador en todos; `claude-centinela` en E1.3, E2.4, E3.1 y E5.1; `gemini-ux-revisor` en
E4.1. Paralelismo: codex ≤ 2, claude ≤ 3.

## Resultados del spike E2.0/E3.0 (ver `docs/headless.md`)

- Confirmado: namespace `orquesta:<n>`, `additionalContext` llega al modelo, `--agent orquesta:<n>`,
  marketplace de carpeta que se lee en vivo, `--continue` en cwd dedicado, `dontAsk` con allow-list.
- PreToolUse `ask` en `claude -p` equivale a **deny** (también con `bypassPermissions`). Se mantiene: los
  delegados headless nunca pueden correr comandos con `--confirm`. El `permissionDecisionReason` dice que
  la confirmación la corre el usuario en su terminal.
- `agent_type` llega con prefijo (`orquesta:claude-juez`) y no existe sin `--agent` (E2.4).
- Con cwd bajo `/home/luca`, Claude carga `~/.claude/CLAUDE.md` como memoria de un directorio ancestro
  aunque `CLAUDE_CONFIG_DIR` sea otro. El E2E (E5.2) corre con el proyecto de prueba bajo `/tmp`, si no
  ve el bloque global de orquesta duplicado.
- E2.3: el plugin se escribe en una carpeta temporal y se renombra (no exponer escrituras a medias a
  `/reload-plugins`). E2.5: `plugin-instalado` verifica que `claude plugin list` diga "Read from: <carpeta>".
- E3.1: `--continue` + `dontAsk` + allow-list alcanzan; `state/rrhh/` es exclusivo de RR. HH.

## Riesgos

Tope de 600 s del Bash tool (reuniones en background); namespace `orquesta:`; SessionStart que falla en
silencio (ruta absoluta + doctor); más fricción por confirmar en la terminal (si `!` no tiene TTY, segunda
terminal); evasión deliberada por pty fuera del modelo de amenaza; merge por SHA puede bloquear flujos que
hoy pasan (intencional); sin fallback de manifiesto un manifiesto roto frena al runner; el primer generate
con guard nuevo puede pedir `--confirm`; si `dontAsk` no niega, el Bash de RR. HH. depende de no aprobar
lo que pida; generate no atómico frente a `/reload-plugins`.

## Progreso

- Hecho (2026-10-06): E0, E1, E2.0-E2.5, E3.0, E3.1, E4.1, E5.1. E5.2 en sandbox: generate → corte → sesión real con el plugin instalado (15 agentes, 5 comandos, contexto con la generación correcta) → rollback idéntico, doctor sin fallas.
- Pendiente para el usuario: (1) revisar y aplicar la rama `orquesta/orquestador/e2-6-e3-2` (agente rrhh, that-it-guy, regla de no mergear): `git merge orquesta/orquestador/e2-6-e3-2` y en su terminal `orquesta generate --confirm that-it-guy --confirm rrhh --confirm codex-forjador --confirm codex-tester --confirm codex-refactorista -m "…"`; (2) corte real en su terminal: `orquesta migrar corte --confirm` y `orquesta doctor`; (3) borrar la rama abandonada `orquesta/codex-forjador/e5-1-migrar` con `orquesta wt clean orquesta/codex-forjador/e5-1-migrar --force`. Sin verificar en vivo (se agotó la cuota): la denegación del hook pre-bash en una sesión con el plugin instalado (cubierta por tests y por el spike).
- E5.3 (corte real) hecho por el usuario el 2026-10-06: `orquesta migrar corte`, plugin `orquesta@orquesta-local` instalado, deploy viejo y bloque de CLAUDE.md retirados.
- Fixes post-corte: el subcomando `orquesta rrhh` no se prefija con el namespace del plugin; el doctor no pide auditoría del plugin propio.
- E5.4 (docs): README, PLAN, AGENTS, modelo de amenaza, sandbox (c) y snippet de statusLine.
- [x] E0 · [x] E1 · [x] E2 · [x] E3 · [x] E4 · [x] E5
