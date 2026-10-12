# Verification against the real CLIs

The test suite and the [demo](../examples/demo) never call a model. This page records what was checked
against the real tools, when, and with which versions, so the claims in the README can be traced to an
actual run. Last update: 2026-10-12.

## Environment

| Tool | Version |
|---|---|
| Ubuntu | 26.04.1 |
| Python | 3.13.12 (CI: 3.11, 3.12, 3.13) |
| uv | 0.12.10 |
| Claude Code | 2.1.296 |
| Codex CLI | 0.160.0 |
| Antigravity CLI (`agy`) | 1.3.3 |
| bubblewrap | 0.11.1 |

## `orquesta doctor` (live checks, no cache)

`orquesta doctor --json --no-cache` runs deterministic checks plus a minimal real ping per backend.

| Check | Result |
|---|---|
| Registry and `backends.yaml` valid | ok: 17 agents, 16 enabled |
| Codex and agy sessions (`codex login status`, `agy models`) | ok |
| `agy` answers on stdout without a TTY | ok: `PONG` in 7.8 s |
| Claude Code loads the plugin agents | ok: 16 agents as `orquesta:<name>` |
| `bwrap` usable (read-only guaranteed for agy) | ok |
| Plugin installed from this folder | ok |
| Tool catalog matches Claude Code's built-ins | ok after removing four tools Claude Code no longer ships |
| Test suite | ok |
| Deployed plugin matches the generator | fails right after code changes until `orquesta generate` is run: this is the drift check doing its job |

## Probes for specific guarantees

| What | How | Result |
|---|---|---|
| Codex `workspace-write` can reach other projects under `/tmp` | `codex exec --sandbox workspace-write -C /tmp/…/cwd` asked to `touch /tmp/…/outside/x` | it could; with `-c sandbox_workspace_write.exclude_slash_tmp=true` and a private `TMPDIR` it could not, while `$TMPDIR` stayed writable. That flag is now in `backends.yaml` |
| Gemini read-only works inside the new sandbox (private `/tmp`, repository re-mounted read-only) | `orquesta run gemini-ux-revisor` on a scratch repository | `OK` in 30.9 s, answered `PONG` |
| `agy --mode plan` is read-only | see [`headless.md`](headless.md) | it is not: it still writes files. That is why Gemini runs inside `bwrap` |
| End-to-end with real models | [`eval/`](../eval/README.md): GPT writes the fix in a worktree, Gemini reviews through `orquesta review`, hidden tests score the result | see the results there |

## Reproduce

```bash
orquesta doctor --no-cache        # live checks; spends one minimal ping per backend
uv run python eval/run_eval.py --tareas paginar --condiciones A,B   # a small real run (uses quota)
uv run python eval/run_eval.py --simulado                           # same plumbing, scripted backends
```
