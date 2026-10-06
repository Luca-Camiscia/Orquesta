# Brief: migrar orquesta a un plugin local de Claude Code (terminal primero)

Brief producido por claude-interrogador tras 2 rondas de preguntas con el usuario (2026-10-05). Repo: /home/luca/orquesta.

## Objetivo
El usuario abre Claude Code en cualquier proyecto → conversa con la sesión principal (orquestador) para definir metas, mejoras y forma de trabajo → aprueba el plan una vez → el orquestador delega en especialistas, verifica con tests, pide review de otra familia y convoca reuniones cuando hace falta. RR. HH. (armado de equipo, triage) es interno: el orquestador lo consulta en silencio. Aparte, una sesión de Claude Code dedicada a RR. HH., con memoria, donde el usuario conversa sobre agentes nuevos o cambios; solo propone diffs de orquesta.yaml.

## Decisiones del usuario
1. Arquitectura híbrida: especialistas = subagentes efímeros (nativos o `orquesta run` para codex/agy); solo pocos roles persistentes (de entrada, RR. HH.).
2. Reuniones (rondas, moderada, crítica, posturas, seccionado) y review automática de otra familia con bloqueo de merge pasan a la terminal, invocadas por la sesión principal. UI web queda como panel opcional sin chat (Agentes, Salud, Historial, Plugins, Tareas solo lectura).
3. Autonomía: OK al plan una vez; después delega, testea y pide review sin preguntar. Consulta solo para: merge a main, escaladas/agentes protegidos, fallas, review rechazada.
4. RR. HH. sesión dedicada, memoria global, usa métricas deterministas de rrhh.py como herramienta, redacta altas/bajas/cambios como diff de orquesta.yaml, NUNCA ejecuta generate. La principal la consulta en silencio.
5. Plugin = capa fina. orquesta.yaml + núcleo Python siguen siendo la fuente de verdad. `orquesta generate` genera el contenido del plugin (agents/*.md, skills, commands, sección de system prompt) en un directorio de plugin local y deja de escribir en ~/.claude/agents/orquesta y en el bloque de ~/.claude/CLAUDE.md. El plugin invoca el CLI de orquesta como proceso del host.
6. Migración por etapas con interruptor: se construye y prueba en un CLAUDE_CONFIG_DIR aislado; tras el E2E un comando retira el deploy viejo; rollback = reinstalar el deploy anterior.
7. Guard ampliado: --confirm también para altas y bajas (además de protected, escaladas, permissionMode, plugins nuevos). Cambios de prompt de no protegidos también los aplica el usuario.
8. Plugin a nivel usuario, activo en todos los repos, desactivable con .orquesta-off. Plan aprobado y estado por proyecto; memoria de RR. HH. global.

## No-objetivos
Backends por API (Groq) — solo dejar el hueco; reescribir runner/guard/generate en JS/TS; rediseñar la UI web; Windows; modo carrera; convivencia indefinida de deploys.

## Alcance por módulos (verificado por el interrogador)
- generate.py, render.py, claude_md.py, manifest.py, paths.py: destino → directorio de plugin local (manifest, agents/, commands/, skills/, sección de system prompt). Manifiesto/hashes cubren el nuevo destino. Se mantienen backup, tests, CHANGELOG y commit.
- guard.py: violaciones `alta` y `baja`, regla para cambios de prompt. Respetar _LATER_FIELDS y editing.FIELD_ORDER/DEFAULTS.
- cli.py (hoy: ui, generate, run, doctor, plugins, wt): subcomandos nuevos con --json para reuniones (chat.py: _mode_critica, _mode_posturas, _mode_seccionado, _moderate, run_turn), review automática (_auto_review) y merge con bloqueo (merge_worktree, discard_worktree), rrhh (triage, evaluar, catalogar, sugerir_participantes, reclutador.consultar opcional), migración (instalar plugin / retirar deploy viejo / rollback).
- chat.py: desacoplar reuniones y review del Store de conversaciones de la UI (hoy guarda en conversaciones/), o adaptarlo a headless.
- rrhh.py, reclutador.py: herramientas, siguen deterministas.
- ui/app.py, ui/index.html: retirar pestaña Conversaciones y /api/chat/* (o solo lectura). Middleware de seguridad intacto.
- doctor.py: plugin instalado y cargado, hash del contenido generado = manifiesto, sin deploy viejo residual tras migrar.
- orquesta.yaml: definición del agente/sesión de RR. HH. (protegido, read-only sobre código, solo propone diffs) y del orquestador (flujo plan → OK → delegar → verificar → review → frenos).
- Contenido nuevo del plugin (generado): slash commands (p. ej. /orquesta:plan, /orquesta:reunion, /orquesta:rrhh, /orquesta:tareas, /orquesta:mood); status line (cuota 5 h, worktrees/tareas, mood); panel lateral opcional detrás de un flag; inyección de la sección del orquestador al system prompt.
- Estado por proyecto: <proyecto>/.orquesta/plan.md; estado de tareas en ~/orquesta/state/<proyecto>/; memoria RR. HH. en ~/orquesta/state/rrhh/.
- Tests para todo comportamiento nuevo, con tmp dirs y CLAUDE_CONFIG_DIR inyectado; nunca tocan ~/.claude.
- Docs: README, PLAN (nueva sección de progreso), AGENTS.md.

## Restricciones
backends.yaml única fuente de comandos; bwrap, worktrees, max_parallel (codex 2, agy 2, claude 3), .orquesta-off, rate limit y detección de escrituras read-only siguen en el runner Python; nadie confirma por su cuenta (NECESITO CONFIRMACIÓN se propaga); moods solo presentación; sin secretos en archivos generados ni tests; diff mínimo, sin dependencias nuevas sin justificar; español rioplatense; la API de hooks de función es early access → todo lo que dependa de ella es opcional y degrada sin romper; review ciega al autor y de otra familia, solo rechaza con hallazgo concreto; juez de familia que no participa; reuniones anónimas.

## Criterios de aceptación
1. `uv run pytest` verde con tests nuevos: guard altas/bajas con --confirm; generate al directorio del plugin sin escribir en ~/.claude; subcomandos CLI nuevos con --json; bloqueo de merge cuando la review rechaza.
2. `orquesta generate --dry-run` muestra el diff del plugin; en un CLAUDE_CONFIG_DIR aislado Claude Code carga el plugin con agentes y comandos.
3. `orquesta doctor --skip-live` reporta plugin instalado y sincronizado, y ausencia de deploy viejo tras migrar.
4. E2E en ~/orquesta/sandbox/empanometro con el plugin: el orquestador conversa y escribe .orquesta/plan.md → usuario aprueba → forjador implementa en worktree → orquestador corre tests y pide review sin preguntar → frena antes de mergear a main.
5. Con review forzada a rechazar, el merge queda bloqueado hasta confirmación explícita.
6. El orquestador convoca una reunión modo crítica desde la terminal y recibe el veredicto del juez.
7. Sesión de RR. HH.: pide agente nuevo → devuelve diff sin generate; generate sin --confirm rechaza el alta y con --confirm la aplica; en otra sesión, la memoria recuerda la conversación previa.
8. Retiro elimina bloque de CLAUDE.md y ~/.claude/agents/orquesta (con backup); rollback los restaura idénticos (hashes coinciden).
9. Con .orquesta-off el plugin no delega.
10. `orquesta ui --no-browser` arranca sin Conversaciones y con los tests de Host/Origin/token pasando.

## Supuestos
Status line con cuota/tareas/mood; panel lateral detrás de flag con fallback sin hooks. Interrogador y arquitecto solo en tareas no triviales; plan en .orquesta/plan.md. RR. HH. persistente = sesión retomable (agente del plugin abierto por comando) con memoria en archivos de ~/orquesta/state/rrhh/; la principal la consulta por SendMessage si está viva, si no cae al triage determinista (VALIDAR este mecanismo). Ubicación del plugin (~/orquesta/plugin/ o state/, marketplace local a nivel usuario): decide el arquitecto. conversaciones/ queda como histórico. Groq fuera.

## Riesgos
API de hooks early access; Claude Code carga agentes/contexto solo al arrancar (probar en sesiones reiniciadas); la salida --json de la CLI se vuelve contrato (versionarla); desacople de chat.py puede romper tests (refactor con tests antes); review automática débil (SWR-Bench <10% precisión, 2503.15223) → no relajar el freno de merge a main; costo de cuota de RR. HH. persistente y reuniones; guard más estricto afecta flujos de that-it-guy (actualizar sus instrucciones); doble deploy durante transición puede duplicar agentes → E2E en CLAUDE_CONFIG_DIR aislado hasta el corte.
