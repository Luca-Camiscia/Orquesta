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

Each of these became its own brief, fix and regression test (see the git history after `cefb94d`).

## What this shows

- Guarantees are stated narrowly and backed by tests that exercise the failure, not the happy path.
- Model output (the reviewer's finding, the author's `PARCIAL`, the reviewer's verdict) is input to a
  decision, never the decision. What closes a task is an external signal: tests on the reviewed SHA.
- The threat model is "a model that follows rules but makes mistakes". Each fix raises the cost of a
  mistake slipping through; none claims to stop a malicious process running as your user.
