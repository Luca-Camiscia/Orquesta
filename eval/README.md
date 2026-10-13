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
| **D** loop + checklist | B, plus the same self-review checklist as C in the author's second round: isolates what the external reviewer adds |

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

### Full run (A, B and C, three runs each, 54 runs)

| Condition | Hidden tests passed | Author calls | Reviewer calls | Median wall time | Total wall time |
|---|---|---|---|---|---|
| A single agent | 12/18 | 18 | 0 | 66 s | 20 min |
| B orquesta loop | 15/18 | 36 | 18 | 351 s | 106 min |
| C self-review | **18/18** | 36 | 0 | 170 s | 50 min |

| Task | A | B | C |
|---|---|---|---|
| csvlinea | 3/3 | 3/3 | 3/3 |
| duracion | 3/3 | 3/3 | 3/3 |
| intervalos | 3/3 | 3/3 | 3/3 |
| media | 0/3 | 2/3 | 3/3 |
| paginar | 0/3 | 1/3 | 3/3 |
| slug | 3/3 | 3/3 | 3/3 |

All 18 first-round verdicts in B were `APROBAR CON CAMBIOS`, so B always had its second round.
Contamination audit ([`auditar.py`](auditar.py)): none of the 182 Codex sessions of the evaluation (pilot, full run and D)
mentions the hidden tests or the task folder.

### Control D: the reviewer plus the same checklist (three runs each, 18 runs)

C's self-review prompt names the kind of defect the hidden tests check (see below), so B against C was
not a fair fight. D gives B's author exactly C's checklist on top of the review.

| Condition | Hidden tests passed | Median wall time |
|---|---|---|
| A single agent | 12/18 | 66 s |
| B orquesta loop | 15/18 | 351 s |
| C self-review + checklist | **18/18** | 170 s |
| D orquesta loop + checklist | 16/18 | 404 s |

D failed `paginar` twice. The [traces](trazas) show why: in all three `paginar` runs the Gemini reviewer
*did* flag the missing validation of `page < 1`, but as "defensive validation, severity low/medium"
next to an open question about whether the issue required it, and closed with `APROBAR CON CAMBIOS`. The
author, told to ignore findings that do not apply, implemented it once out of three. With the same
checklist and no review notes (C), it implemented it every time.

### What it says

- **A second, critical pass is what helped.** Everything A missed is in two tasks whose hidden tests
  check invalid input (`page < 1`, `k <= 0`) that the issue never mentions.
- **On these tasks the cross-family reviewer did not add to that pass and sometimes diluted it.** Its
  findings were correct but hedged: a low-severity flag plus "approve with changes" read as permission
  to skip. That is a finding about the review contract, not about Gemini: the reviewer is asked to
  reject only with a concrete failing case, so an edge case the issue leaves open becomes a soft note.
- **The pilot did not replicate.** In the pilot the reviewer rejected exactly the two flawed fixes and B
  went 6/6; over 36 later reviews it rejected once. One run per condition was not enough to see that.
- **What this does and does not support.** The guarantees (isolation, read-only enforcement, the merge
  gate) are enforced in code regardless of model quality; this evaluation is about output quality. On
  small, well-scoped fixes it supports a cheap critical second pass by the author over an external
  review. Two concrete follow-ups: let the reviewer mark edge cases the spec leaves open as questions
  the author must answer explicitly, and add tasks whose defects are not input validation.

## Caveats

- Six small functions with hidden edge cases, three runs per condition: enough to see the pilot was
  noise, not enough for fine-grained claims. Nothing here speaks to large changes, ambiguous
  requirements or architectural work.
- The hidden tests are readable on disk by any process: the agents never had them in their project and
  the audit found no access in the Codex logs, but the Gemini CLI's logs are binary and were not
  audited. The reviewer is read-only and can only pass text to the author, which the Codex logs cover.
- Same author model (Codex, as configured in `orquesta.yaml`) in every condition; the reviewer in B is
  the Gemini agent `orquesta review` picks by default.

## Reproduce

```bash
uv run python eval/run_eval.py --simulado                 # plumbing only, scripted backends, no quota
uv run python eval/run_eval.py --repeticiones 3 > out.jsonl   # real models: about 54 runs, uses quota
uv run python eval/resumir.py out.jsonl                   # tables above
```

Raw results: [`piloto.jsonl`](piloto.jsonl), [`resultados.jsonl`](resultados.jsonl) (A, B, C),
[`resultados-d.jsonl`](resultados-d.jsonl) (D, with versions and final commit per row) and the D runs'
final patches and review texts in [`trazas/`](trazas). A first attempt at D was discarded: the Gemini
session expired mid-run and every review failed (see [`../docs/verification.md`](../docs/verification.md)).
