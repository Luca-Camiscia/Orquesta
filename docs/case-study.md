# Case study: closing holes in the read-only guarantee

A real, small loop from October 2026, run with orquesta itself. It shows the workflow the tool is built
for: an independent reviewer finds a problem, the orchestrator writes a brief, a GPT agent fixes it in an
isolated worktree, a model of another family reviews the diff, and the change only lands after tests
that fail without the fix pass with it.

## 1. The finding

Before publishing the repository I asked a reviewer with **no prior context** (GPT via Codex CLI, read-only,
on a fresh clone) to audit it as a strict technical screener. Two findings were about the core promise
of the project, "a read-only agent leaves no changes":

1. **The index was invisible.** `tree_state()` hashed `git status` plus the *working tree* diff. If a file
   was already staged (`M  dato.txt`), an agent could modify it and `git add` it again: status and
   working-tree diff stay identical, so the run came back `OK`. The reviewer reproduced it with the fake
   backend.
2. **`/tmp` was writable inside the sandbox.** Gemini runs under `bwrap --ro-bind / /` with a few
   writable binds (`~/.gemini`, `~/.cache`, `/tmp`) that the CLI needs. A project that lives under `/tmp`
   was therefore writable even in read-only mode.

Both were confirmed by reading the code before acting on them (`runner.py`, `_sandbox_prefix` and
`tree_state`): delegated findings are hypotheses until verified.

## 2. The brief

One brief to `codex-forjador` (GPT, write mode, isolated worktree `orquesta/codex-forjador/a-readonly-sandbox`),
quoting the original request and stating the acceptance criteria:

- hash the index diff (`git diff --cached`) and the signature of *every* dirty file, not only untracked ones;
- in read-only mode, re-mount the project read-only *after* the writable binds (later mounts win in `bwrap`);
- regression tests that fail without the fix, including a real `bwrap` write attempt, skipped where the
  kernel does not allow user namespaces.

## 3. The change

```diff
     if spec.os_sandbox == "workspace":
         prefix += ["--bind", str(cwd), str(cwd)]
+    elif spec.os_sandbox == "readonly":
+        prefix += ["--ro-bind", str(cwd), str(cwd)]
```

```diff
-    h = hashlib.sha256(status + git("diff", "--no-ext-diff", "--binary"))
+    h = hashlib.sha256(status + git("diff", "--no-ext-diff", "--binary")
+                       + git("diff", "--cached", "--no-ext-diff", "--binary"))
 ...
-        if e.startswith("?? "):
-            h.update(f"{e}:{firma}".encode())
+        h.update(f"{e}:{firma}".encode())
```

Plus 6 tests (commit `cefb94d`): the staged-then-restaged scenario end to end through the runner, two
`tree_state` cases where status and diff are byte-identical but the content or the index changed, the
mount order with a project under `/tmp`, and the real `bwrap` attempt.

## 4. Verification

The agent's own report said `RESULTADO: PARCIAL`: inside its sandbox it could not run the whole suite nor
create namespaces for `bwrap`. That self-report is not what closes a task. The orchestrator (Claude, a
different family from the author) read the diff, then ran outside the sandbox:

```text
$ uv run pytest -k bwrap_impide -rA
PASSED tests/test_runner.py::test_read_only_bwrap_impide_escribir_en_proyecto_bajo_tmp
$ uv run pytest
1215 passed in 62.14s
```

and only then merged the branch.

## 5. The second round

The same context-free reviewer was run again on the result. It no longer found those two holes, but it
went one level deeper, which is what an adversarial review is for:

- a `git commit` made by a read-only agent leaves a clean tree, so it was not detected;
- re-mounting only `cwd` read-only leaves the rest of the repository (and `.git`) writable when the agent
  runs from a subdirectory under `/tmp`;
- the per-task ledger reported "escalate to the user" after repeated failures, but the runner did not
  enforce it: the next delegation would still run;
- a review over a diff truncated at 20,000 characters still marked the full commit as reviewed.

These became two parallel briefs (disjoint files, so two GPT agents could work at once): the runner now
hashes `HEAD` and every ref and re-mounts the whole repository read-only (including an external `.git`
for worktrees); it refuses to run a task the ledger marked for escalation; and a review over a truncated
diff lists every changed file to the reviewer and blocks the merge until a human confirms.

Verifying those merges surfaced one more problem that neither the agents nor the reviewer had reported:
the new review prompt included the worktree path, `…/codex-forjador--<task>`, which names the author and
defeats the blind cross-family review. Checking the older CLI path showed it had the same leak since the
beginning. The fix removes paths from the review prompt, and a test now asserts that neither the author
name nor the path reaches the reviewer. A last check found that `--task "Fix X"` (branch `fix-x`) slipped
past the ledger lookup, which compared the raw text; it now matches the slug too.

```text
$ uv run pytest
1250 passed in 64.57s
```

## The third round: a correct fix with the wrong behavior

The next review moved to write agents: Gemini's sandbox kept the shared `/tmp` writable, so a checkout
under `/tmp` was reachable from a worktree. A probe showed the same for Codex: `codex exec --sandbox
workspace-write` could `touch` a file in another directory under `/tmp`, and could not once
`sandbox_workspace_write.exclude_slash_tmp=true` was set. Tests need a temp dir, so each write run now
gets a private `TMPDIR` that is created before the run and deleted after it.

The delegated fix also added a second layer: compare the original checkout before and after a write
run and, if it changed, fail the run and drop the agent's commit. Its tests passed. It was still wrong.
While an agent works in its worktree, the user keeps working in the checkout (that is the point of
worktrees), and parallel tasks move their own branches. This very session would have tripped it: the
orchestrator committed docs while two agents were running. The merged version keeps the comparison,
ignores every `orquesta/*` task branch and reports a warning instead of blocking. The sandbox is the
control; the comparison leaves a trace.

The trace showed up on the very next delegation. While a GPT agent fixed the merge rule in its
worktree, the orchestrator edited `backends.yaml`, `runner.py` and `docs/README.md` in the checkout.
The run finished `OK`, committed the agent's work, and carried:

```text
aviso: el checkout original cambió durante la corrida (¿el agente o vos?): backends.yaml, src/orquesta/runner.py, docs/README.md
```

The agent's diffstat did not include those files, which settled the question in seconds.

## What the usage log says

Every delegated run is appended to `state/usage.jsonl` (`orquesta usage` aggregates it). Over the first
week of real use, October 5–12, 2026, **85 runs went through the runner** (plus 26 native Claude
subagent calls, which do not):

| Backend | Runs | OK | Timeout | Read-only violation | Mean latency |
|---|---|---|---|---|---|
| Codex (GPT) | 48 | 42 | 6 | 0 | 206–423 s by agent; 409 s for the write agent |
| Antigravity (Gemini) | 37 | 23 | 12 | **2** | 230–481 s by agent |

- The before/after check flagged **two read-only runs as violations**, both from Gemini agents
  (October 6 and 10): the run is reported as failed instead of its output being trusted.
- Gemini timeouts (12 of 37) are the main operational cost. The 540 s cap is deliberate: it is under
  the 10-minute limit of Claude Code's Bash tool, so a stuck backend can never hang the orchestrator.
- Codex in text mode does not report tokens; Gemini read about 6.2 M tokens across its runs. All of it
  ran on flat-rate subscriptions, so the cost that matters is quota, which the panel tracks per
  5-hour window.

## What this shows

- Guarantees are stated narrowly and backed by tests that exercise the failure, not the happy path.
- Model output (the reviewer's finding, the author's `PARCIAL`, the reviewer's verdict) is input to a
  decision, never the decision. What closes a task is an external signal: tests on the reviewed SHA.
- The threat model is "a model that follows rules but makes mistakes". Each fix raises the cost of a
  mistake slipping through; none claims to stop a malicious process running as your user.
