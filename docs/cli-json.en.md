# CLI output contract (English)

Translation of [`cli-json.md`](cli-json.md), which stays the reference. Field names and state values are
in Spanish because they are part of the contract the orchestrator parses.

## The JSON envelope

Commands that use the envelope print it as JSON on a single line, with Unicode characters unescaped. It
always has these keys:

| Key | Content |
|---|---|
| `version` | Contract version: `1`. |
| `comando` | Command that produced the answer. |
| `estado` | One of the states below. |
| `generacion` | Generation id of the deployed plugin, or `null` when it does not apply. If it differs from the one in the session context, the orchestrator asks for a restart. |
| `datos` | Command result; `{}` by default. |
| `errores` | Errors; `[]` by default. |
| `avisos` | Warnings; `[]` by default. |

| State | Meaning | Exit code |
|---|---|---|
| `OK` | The command finished correctly. | `0` |
| `NECESITO_CONFIRMACION` | A human confirmation is needed. | `3` |
| `BLOQUEADO` | A condition prevents continuing. | `3` |
| `RATE_LIMIT` | The backend hit its usage limit. | `1` |
| `ORQUESTA_OFF` | Delegation is disabled for the project (`.orquesta-off`). | `1` |
| `OCUPADO` | The required resource is busy. | `1` |
| `ERROR` | The command failed. | `1` |

The constructor rejects unknown states with `ValueError`. `exit_code()` returns `1` for any state other
than `OK`, `NECESITO_CONFIRMACION` and `BLOQUEADO`.

## `orquesta run` states

`orquesta run` is read by the wrapper agents as text: the first line is `ESTADO: <state> — <detail>` when
the run did not finish normally, and the output ends with an `── orquesta run ──` block (agent, state,
latency, worktree, branch, commit, diffstat, warnings, path and sha256 of the full raw output).

| State | Exit | Meaning |
|---|---|---|
| `OK` | 0 | The backend answered |
| `ERROR` | 2 | Configuration or runner error (for example the backend does not define the agent's mode, so it will not run without its sandbox) |
| `ORQUESTA_OFF` | 3 | The project has `.orquesta-off` |
| `BLOQUEADO` | 3 | The task is closed in the ledger or reached its cap |
| `NO_GIT` | 4 | A write agent needs a git repository |
| `OCUPADO` | 5 | All slots of that backend are taken (`max_parallel`) |
| `FALLO_BACKEND` | 6 | The CLI exited with an error, empty output or an unparsable answer |
| `VIOLACION_READ_ONLY` | 7 | The git state changed during a read-only run, or could not be read afterwards |
| `RATE_LIMIT` | 75 | Usage or quota limit |
| `SIN_SESION` | 77 | The CLI asks to log in; the user has to do it |
| `TIMEOUT` | 124 | The backend did not finish in time (540 s by default, under Claude Code's 10-minute Bash limit) |
| `CANCELADO` | 130 | Cancelled by the user |

## Review

`orquesta review BRANCH [--repo DIR] [--revisor NAME]` returns the envelope. `--revisor` picks a
reviewer from the manifest that must be `read-only` and from a different backend family than the
author. Without it, the usual automatic choice applies (among installed backends). If the named reviewer
does not meet those conditions it returns `ERROR` (exit 1) with the reason in `errores`, without running
tests or the review and without touching earlier evidence. A valid review records the name in
`datos.revisor`.

## `orquesta ledger`

`orquesta ledger anotar TASK (--progreso | --sin-progreso) [--nota TEXT] [--repo DIR]` records an
attempt and returns its state in `datos`: `tarea`, `intentos`, `corridas`, `sin_progreso` (trailing
streak), `cerrada` and `accion`. Notes are cut at 500 characters. With 2 consecutive attempts without
progress it returns `OK` (exit 0), action `replanificar` and a warning to write a fresh brief without
history. With 4 without progress or 6 attempts in total it returns `BLOQUEADO` (exit 3), action
`escalar` and the reason `tope alcanzado: escalá al usuario antes de otro intento`; the attempt is
still recorded. Escalation takes precedence, and progress resets only the streak.

`orquesta ledger cerrar TASK --motivo TEXT [--repo DIR]` stores the reason and closes the task; it
accepts no attempts afterwards. `orquesta ledger ver [--repo DIR]` returns the states in
`datos.tareas`. Both return `OK` (exit 0). The project is identified by the git repository of the
current directory or `--repo`, resolving registered worktrees to their original repository. An invalid
slug, annotating a closed task or a corrupt ledger returns `ERROR` (exit 1).

`orquesta run AGENT --task TASK` reserves and counts every run in the same ledger, under a lock, once a
slot is available and right before calling the backend, so a run rejected as `OCUPADO` does not use up
the budget. At 8 runs the action becomes `escalar`: the eighth runs and the ninth is blocked, even if the
orchestrator never annotated progress. That margin over the 6 annotated attempts leaves room for
retries within a round. Old entries without `corridas` read as 0; without `--task` the ledger is not
touched. The key keeps `TASK` if it is a valid slug, otherwise it is normalized with `slugify`.

## Human confirmation

The model never confirms. Confirmation opens `/dev/tty` to read and write and asks
`Para confirmar, escribí «expected text»: `. Only the exact text is accepted, after trimming leading and
trailing spaces. No environment variable or flag skips it.

If the terminal cannot be opened, `pedir_confirmacion()` raises `SinTTY` with the attribute
`comando_sugerido`. The commands that confirm (`merge`, `descartar`, `generate --confirm`,
`migrar corte|rollback`) turn it into the `NECESITO_CONFIRMACION` envelope (exit 3) with the exact
command for the user to run in a second terminal (Claude Code's `!` prefix has no TTY either).

## `orquesta usage`

`orquesta usage [--dias N]` aggregates usage per agent over the last N days (default 30) from
`state/usage.jsonl`. It prints a JSON array on one line, sorted by `agent`, without the envelope, and
exits `0`; an empty window returns `[]`.

```json
[{"agent":"codex-forjador","llamadas":4,"tokens":1200,"latencia_media_s":3.5,"errores":1,"tasa_error":0.25,"backend":"codex"}]
```

| Field | Content |
|---|---|
| `agent` | Agent id. |
| `llamadas` | Calls in the window. |
| `tokens` | Sum of `tokens_in` and `tokens_out`; `null` counts as `0`. |
| `latencia_media_s` | Mean of available latencies in seconds; `0.0` without samples. |
| `errores` | Calls whose state is not `ok`/`OK`. |
| `tasa_error` | `errores / llamadas`. |
| `backend` | The backend if unique in the window; `null` if several. |

The window is `now - N * 86400 <= timestamp < now`. Unix or ISO `timestamp` and the ISO `ts` field are
accepted; missing files and invalid lines are skipped (a bad line is dropped whole). `--dias` must be
between 1 and 36500. An ISO timestamp without a zone is read as UTC.
