# Design decisions (English summary)

The full design is in Spanish in [`plugin-plan.md`](plugin-plan.md) and [`../PLAN.md`](../PLAN.md). This
page summarizes the decisions that shape the code, with the reason for each.

## Threat model

The adversary is **a model that follows rules but makes mistakes**, not a malicious one. A model with a
shell under your user id can defeat any local control (for example by opening a pty to fake a
terminal). So the controls aim at two things: a mistake never ends in a merge or a registry change you
did not see, and a deliberate bypass leaves a trace (git history, the changelog, the deploy manifest,
the runner's warnings).

## Where rules live

| Decision | Why |
|---|---|
| The rules that matter are **code in the runner and the CLI**, not instructions in prompts | Prompts are advice a model can misread; a sandbox flag, a SHA comparison or a TTY read are not |
| **Orchestrator rules are injected by a `SessionStart` hook**, generated from the registry and stamped with a generation hash | Every Claude Code session gets the same routing table; if the CLI answers with another generation, the orchestrator asks for a restart instead of acting on stale rules |
| **Agents backed by GPT or Gemini are thin Claude wrappers** (a cheap model) whose only job is `orquesta run <agent>` | Claude Code can only spawn Claude subagents. The wrapper is a pipe; the runner owns flags, sandbox, worktree, limits and output classification |
| **`backends.yaml` is data**, each invocation verified by hand and logged with evidence in [`headless.md`](headless.md) | CLIs change fast and their flags lie (`agy --mode plan` still writes). Evidence first, then config |
| **The runner trusts a deploy manifest, not the editable YAML** | Editing `orquesta.yaml` by hand (or by a model) does not change what runs until `orquesta generate` validates it, and the guard compares against what is deployed |

## Confirmations

- Merging, discarding, applying protected registry changes and installing or rolling back the plugin
  read an exact text (the branch name, the agent name, a keyword) from **`/dev/tty`**.
- The model's Bash tool has no TTY, and neither does Claude Code's `!` prefix: without one, the CLI
  answers `NECESITO_CONFIRMACION` (exit 3) with the exact command for the user to run in a second
  terminal. There is deliberately no flag or environment variable that skips it.
- A `PreToolUse` hook on Bash warns when a command contains `--confirm`, `--forzar` or known bypass
  patterns (`python -c … orquesta`, `script`, `unbuffer`, `pty`). It is a warning layer; the TTY read is
  the actual brake. In headless `claude -p` the hook's `ask` is a denial, which was verified.

## Review and merge

- **Cross-family review**: work written by one family (GPT, Gemini, Claude) is reviewed by a read-only
  agent of another, chosen among installed backends; the prompt carries no author name or path
  (self-preference bias, [arXiv 2404.13076](https://arxiv.org/abs/2404.13076)).
- **Evidence over claims**: the project's tests run on the exact commit under review, and the reviewer is
  told that passing tests do not prove correctness. The reviewer may only reject with a concrete
  `file:line` finding.
- **Merge only what was reviewed**: branch tip = reviewed SHA = tested SHA, the destination must not
  have moved since the base, and the diff is scanned for weakened tests. Any new commit invalidates the
  review and the test evidence.
- **Verdict parsing fails closed**: only the last `Veredicto:` line counts, and any other verdict in the
  reply makes it ambiguous.
- **Declared confidence** from delegates is inflated, so it is used only as an escalation threshold
  (below 75 asks for confirmation) and calibrated per backend in the panel, never as a vote weight.

## Sandboxing

- **Gemini (`agy`)** runs inside `bwrap`: `/` read-only, a private empty `/tmp`, two writable state
  directories the CLI needs, and the repository re-mounted read-only on top. In write mode only the
  worktree, its git dir and the object store are re-opened.
- **Codex** uses its own sandbox (`--sandbox read-only` or `workspace-write` with the shared `/tmp`
  excluded); write runs get a private `TMPDIR`.
- **Detection everywhere**: a digest of git status, worktree and index diffs, file signatures, `HEAD` and
  refs before and after each run. A change during a read-only run fails it; a change to your checkout
  during a write run attaches a warning, because you may be working there at the same time.
- Outside Linux there is no OS sandbox; the runner falls back to detection and says so in its output.

## Multi-model meetings

Meetings (`orquesta reunion`) follow the literature in [`investigacion.md`](investigacion.md): a blind,
independent first round before any debate (most of the gain from debate comes from voting), debate
only when the participants disagree, anonymized peers in random order (models conform to peers they
can identify), one critique round at most, and a judge from a family that did not take part.

## Alternatives considered

| Decision | Alternatives | Why this one |
|---|---|---|
| Own runner for Gemini, inside `bwrap` | The community Claude Code plugin for Antigravity | Audited line by line before installing ([audit](../audits)): it ran `agy` without any sandbox and shipped an agent described as "use proactively". Verdict: do not install. The official Codex plugin passed a lighter audit and is installed pinned to the audited commit |
| Read-only for Gemini by OS sandbox | `agy --mode plan`, the CLI's own "plan" mode | Tested first: plan mode still writes files ([`headless.md`](headless.md)). A flag that lies cannot be the control |
| A Claude Code plugin generated from `orquesta.yaml` | The first version wrote agents into `~/.claude/agents/` and a marked block into `~/.claude/CLAUDE.md` | The plugin can be installed, disabled and rolled back as one unit, and carries hooks and commands. The migration was built in an isolated `CLAUDE_CONFIG_DIR`, is idempotent and has a rollback command |
| Human confirmation by reading `/dev/tty` | A web approval tray in the panel; an environment variable or flag for automation | Anything the model can reach (a flag, a variable, an HTTP call from Bash) it can also trigger by mistake. Claude Code's Bash tool and its `!` prefix have no TTY, so a second terminal is a hard boundary. Cost: one extra step for every merge |
| Claude Code's own Bash sandbox left off | Turn it on for every session | It would live in the user's `~/.claude/settings.json`, outside what orquesta manages, and its interaction with the TTY brake, worktrees and `state/` was not evaluated. The modelled risk (mistakes) is covered by the hook, the TTY read and the SHA-pinned merge. Documented as future work |
| Checkout changes during a write run: warning | Fail the run and drop the agent's commit | The stricter version was implemented and its tests passed, but it blocked legitimate work: the user keeps working in the checkout and parallel tasks move their branches. The sandbox prevents; the comparison leaves a trace ([case study](case-study.md)) |
| Verdict parser that fails closed on any second verdict | First match in the reply | An external review showed that quoting the literal `Veredicto: APROBAR` approved a reply ending in `RECHAZAR`. Ambiguity now means "not approved" |

## Limits that are accepted

- Delegated runs time out at 540 s, under the 10-minute limit of Claude Code's Bash tool, so a stuck
  backend can never hang the orchestrator. Gemini hits that limit often on large reads.
- Files ignored by git are outside the change detection, by design (caches, virtual environments).
- Claude Code's own Bash sandbox is not configured by orquesta; it would live in the user's
  `~/.claude/settings.json`, which orquesta does not touch.
