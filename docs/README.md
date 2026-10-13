# Documentation index

Most design documents are in Spanish, the language the project is developed in. This page says what
each one contains so you can decide what is worth translating.

| Document | Language | What it is |
|---|---|---|
| [`design.md`](design.md) | English | Summary of the design decisions and the reason for each: threat model, where rules live, confirmations, review and merge, sandboxing, meetings, accepted limits |
| [`case-study.md`](case-study.md) | English | A real loop from this project: an independent review finds holes in the sandbox guarantees, agents fix them in worktrees, the orchestrator verifies, plus numbers from the usage log |
| [`../eval/README.md`](../eval/README.md) | English | Small evaluation: one agent vs. the orquesta loop, scored by hidden tests |
| [`verification.md`](verification.md) | English | What was checked against the real CLIs, when and with which versions: live `orquesta doctor`, sandbox probes, real end-to-end runs |
| [`headless.md`](headless.md) | Spanish | Evidence log for every CLI invocation in `backends.yaml`: what was tried, what failed (for example `agy --mode plan` still writes files, `agy -p -` does not read stdin) and with which versions |
| [`plugin-plan.md`](plugin-plan.md) | Spanish | Design of the Claude Code plugin: threat model (a model that follows rules but makes mistakes), technical decisions, stages and risks. Written by the architect agent and reviewed adversarially by a GPT agent (12 findings, all addressed) |
| [`plugin-brief.md`](plugin-brief.md) | Spanish | The brief that preceded that plan, produced by the clarifying-questions agent |
| [`cli-json.en.md`](cli-json.en.md) | English | Translation of `cli-json.md`, plus the table of `orquesta run` states and exit codes |
| [`cli-json.md`](cli-json.md) | Spanish | Contract of the JSON envelope the CLI prints for the orchestrator: keys, states and exit codes, review, ledger and confirmation semantics |
| [`investigacion.md`](investigacion.md) | Spanish | Literature review (2024–2026) on multi-agent debate, self-preference, verbalized confidence, test-based verification and clarification, and the rule each paper turned into |
| [`../PLAN.md`](../PLAN.md) | Spanish | The original plan and the decisions of each later stage |

Key terms if you read the Spanish ones: *agente* agent, *orquestador* orchestrator, *revisor* reviewer,
*registro* registry (`orquesta.yaml`), *tarea* task, *rama* branch, *confirmar* confirm (human, at the
TTY), *forjador* the GPT implementation agent, *bibliotecario* the Gemini long-context reader.
