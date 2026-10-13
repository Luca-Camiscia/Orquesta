# Evaluation: one agent vs. the orquesta loop

Does a cross-family review loop catch bugs a single agent ships? A deliberately small, honest test with
the real models.

## Setup

Six small bug-fix tasks in [`tareas/`](tareas). Each has buggy code, a short and somewhat vague issue
(`issue.md`, written like a real bug report), a visible test the agents can run, and **hidden tests**
(`test_oculto.py`) that no agent ever sees. The hidden tests check edge cases a careful engineer would
handle but the issue does not spell out (invalid input, boundaries, unsorted input, escaped quotes).
They were validated against reference solutions, and the original buggy code fails all of them.

| Condition | What runs |
|---|---|
| **A** single agent | `codex-forjador` (GPT, write mode, own worktree) gets the issue; its commit is taken as is |
| **B** orquesta loop | same first run, then `orquesta review` by another model family (Gemini) with the visible tests run on the reviewed commit; unless the verdict is a plain `APROBAR`, one more round for the author with the review text |
| **C** self-review | control for B's extra round: same first run, then one more round where the author is asked to review its own attempt critically; no external reviewer |

Same author model and prompt in every condition, real backends through the same runner the plugin uses,
in a throwaway `ORQUESTA_HOME`. The final commit is scored with the hidden tests.

## Results

### Pilot (A and B, one run each)

| Condition | Hidden tests passed | Author calls | Reviewer calls | Median wall time |
|---|---|---|---|---|
| A single agent | 4/6 | 6 | 0 | 68 s |
| B orquesta loop | 6/6 | 12 | 6 | 412 s |

The two tasks A missed (`media`, `paginar`) are the two where the Gemini reviewer answered `RECHAZAR`;
the author's second round fixed both. The other four first-round verdicts were `APROBAR CON CAMBIOS`.

### Full run (A, B and C, three runs each)

In progress.

## Caveats

- Six small tasks; a pilot of one run per condition is anecdote, not evidence. The full run adds
  repetitions and the self-review control so the effect of the *external* reviewer can be separated
  from the effect of simply giving the author a second round.
- The tasks are small functions with hidden edge cases. The result says nothing about large changes,
  ambiguous requirements or architectural work.
- Cost is real: B spends about 3x the calls and 6x the wall time of A in the pilot.

## Reproduce

```bash
uv run python eval/run_eval.py --simulado                 # plumbing only, scripted backends, no quota
uv run python eval/run_eval.py --repeticiones 3 > out.jsonl   # real models: about 54 runs, uses quota
uv run python eval/resumir.py out.jsonl                   # tables above
```

Raw results: [`piloto.jsonl`](piloto.jsonl).
