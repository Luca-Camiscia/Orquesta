# AGENTS.md: guía para agentes que trabajan en orquesta

orquesta es un ecosistema multimodelo. Claude Code orquesta y delega en GPT (Codex CLI) y en
Gemini (Antigravity CLI, `agy`) usando las suscripciones del usuario, sin API keys. Tiene un
registro YAML, un generador de subagentes, un runner, un doctor y una UI web local.

## Comandos

```bash
uv run pytest                      # suite completa (~360 tests, ~20 s); tiene que quedar en verde
PYTHONPATH=$PWD/src uv run pytest  # dentro de un worktree: sin esto testeás el paquete del repo principal
uv run orquesta generate --dry-run # qué cambiaría en ~/.claude sin escribir nada
uv run orquesta doctor --skip-live # chequeos de salud sin llamar a los backends
uv run orquesta ui --no-browser    # UI en http://127.0.0.1:8787
```

## Mapa

| Ruta | Qué es |
|---|---|
| `orquesta.yaml` | Registro de agentes, moods y orquestador. Es la fuente de verdad |
| `backends.yaml` | La ÚNICA fuente de comandos de backend (codex, agy). No hardcodees comandos en otro lado |
| `src/orquesta/schema.py` | Modelos pydantic del registro y de backends, más `check_registry` |
| `src/orquesta/generate.py`, `render.py`, `claude_md.py` | Generan `~/.claude/agents/orquesta/*.md` y el bloque de `~/.claude/CLAUDE.md` |
| `src/orquesta/guard.py`, `manifest.py` | Agentes protegidos, escaladas y manifiesto de deploy (hashes) |
| `src/orquesta/runner.py`, `worktrees.py`, `procs.py` | `orquesta run`: arma el comando, aplica sandbox, worktree y límites de paralelismo, y detecta rate limit |
| `src/orquesta/chat.py` | Proyectos, conversaciones y reuniones (rondas, moderada, crítica, posturas, seccionado), review automática |
| `src/orquesta/rrhh.py` | Recursos Humanos: evaluación, catálogo, triage y sugerencias (determinista, sin LLM) |
| `src/orquesta/modelos.py` | Descubre modelos (caché de Codex, `agy models`, alias de Claude) |
| `src/orquesta/usage.py`, `calibration.py` | Uso por ventana de 5 h y calibración de la confianza declarada |
| `src/orquesta/ui/app.py`, `ui/index.html` | FastAPI más una UI vanilla JS en un solo archivo, con temas por `data-estilo` y tokens CSS |
| `docs/` | `headless.md` (invocaciones verificadas), `investigacion.md` (papers y decisiones) |
| `state/`, `backups/`, `worktrees/`, `sandbox/`, `conversaciones/` | Estado local, ignorado por git. No lo edites a mano |

## Reglas

- **No escribas en `~/.claude` a mano.** Los agentes y el bloque de CLAUDE.md los genera `orquesta generate`.
- **Agentes protegidos** (`protected: true`, como that-it-guy) y escaladas (read-only → write, más
  permisos, plugins nuevos) requieren confirmación explícita del usuario (`--confirm`). Nunca la des por tu cuenta.
- **Campos nuevos en `Agent`:** agregalos a `editing.FIELD_ORDER`/`DEFAULTS` y a `guard._LATER_FIELDS`
  (con su default), así no aparecen como cambios en agentes protegidos.
- **Seguridad de la UI:** bind solo a 127.0.0.1, token `X-Orquesta-Token`, chequeo de Host y Origin, CSP.
  Los endpoints nuevos heredan el middleware: no lo saltees. Fuentes externas solo de Google Fonts.
- **Nunca** escribas secretos ni tokens en archivos generados ni en tests.
- **Cambios de código:** diff mínimo, tests nuevos para comportamiento nuevo, sin dependencias nuevas
  sin justificar. Texto de UI y mensajes en español rioplatense.
- **Trabajo de agentes:** en un git worktree, en una rama `orquesta/<agente>/<tarea>`; el orquestador revisa y mergea.
  Si existe `.orquesta-off` en la raíz de un proyecto, no se delega ahí.
