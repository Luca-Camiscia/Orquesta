#!/usr/bin/env python3
"""Offline end-to-end demo of orquesta: no accounts, no API keys, no quota.

Everything runs in a throwaway directory. The real CLIs (codex, agy) are replaced by
`demo_backend.py`, a scripted stand-in; everything else is the real orquesta code path:
registry → deploy → `orquesta run` (worktree, read-only check, rate-limit detection) →
`orquesta review` (tests on the reviewed SHA + cross-family verdict) → `orquesta merge`
(human confirmation, weakened-test guard).

    uv run python examples/demo/run_demo.py [--keep]
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from ruamel.yaml import YAML  # noqa: E402

FAKE = [sys.executable, str(HERE / "demo_backend.py")]


def backends() -> dict:
    return {"version": 1, "backends": {
        "codex": {"label": "GPT (Codex CLI) [scripted]", "command": [*FAKE, "codex"],
                  "common_flags": ["--color", "never"], "cwd_flag": ["-C", "{cwd}"],
                  "prompt": {"via": "stdin", "flag": ["-"]}, "output": {"format": "text"},
                  "modes": {"read-only": {"flags": ["--sandbox", "read-only"], "timeout_s": 60},
                            "write": {"flags": ["--sandbox", "workspace-write"], "timeout_s": 60}},
                  "rate_limit_patterns": ["usage limit"]},
        "agy": {"label": "Gemini (Antigravity CLI) [scripted]", "command": [*FAKE, "agy"],
                "common_flags": ["--output-format", "json"],
                "prompt": {"via": "arg", "flag": ["-p"], "max_arg_chars": 24000},
                "output": {"format": "json", "text_key": "response", "status_key": "status",
                           "ok_status": "SUCCESS"},
                "modes": {"read-only": {"flags": [], "timeout_s": 60}},
                "rate_limit_patterns": ["quota reached"]},
        "claude": {"label": "Claude", "native": True, "command": ["claude", "-p"],
                   "prompt": {"via": "arg"}},
    }}


def registry() -> dict:
    return {
        "orchestrator": {"enabled": True, "default_mood": "ejecutivo", "show_mood_tag": True,
                         "moods": {"ejecutivo": "Result, key decisions and risks."}},
        "limits": {"max_parallel": {"codex": 2, "agy": 2, "claude": 3}},
        "agents": [
            {"name": "codex-forjador", "backend": "codex", "mode": "write", "isolation": "worktree",
             "role": "Scoped implementation.", "route_when": ["a well-specified code change"],
             "tools": ["Bash"], "prompt": "You implement scoped changes."},
            {"name": "codex-critico", "backend": "codex", "role": "Adversarial review.",
             "route_when": ["before merging"], "tools": ["Bash"], "prompt": "You review."},
            {"name": "gemini-revisor", "backend": "agy", "role": "Cross-family reviewer.",
             "route_when": ["a diff needs an independent review"], "tools": ["Bash"],
             "prompt": "You review diffs."},
        ],
    }


PRICES = '''def final_price(price: float, discount_pct: float) -> float:
    """Price after a percentage discount."""
    return price - discount_pct  # bug: subtracts the percentage as if it were money
'''
TESTS = '''from shop.prices import final_price


def test_no_discount():
    assert final_price(100, 0) == 100
'''


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def dump(data, path: Path) -> None:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    with open(path, "w", encoding="utf-8") as fh:
        y.dump(data, fh)


def step(n: int, title: str, why: str) -> None:
    print(f"\n\033[1m── {n}. {title}\033[0m\n   {why}")


def orquesta(*argv: str, expect: int, stdin: str | None = None, cli_fn=None, **kw) -> tuple[int, str]:
    """Runs the real CLI in-process, prints its (Spanish) output trimmed and checks the exit code."""
    from orquesta import cli
    buf = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = io.StringIO(stdin or "")
    try:
        with redirect_stdout(buf):
            if cli_fn is None:
                code = cli.main(list(argv))
            else:
                code = cli_fn(cli.build_parser().parse_args(list(argv)), **kw)
    finally:
        sys.stdin = old_stdin
    out = buf.getvalue().strip()
    print(f"   $ orquesta {' '.join(argv)}   → exit {code}")
    for line in _summary(out).splitlines():
        print(f"     {line}")
    if code != expect:
        raise SystemExit(f"demo: expected exit {expect}, got {code}\n{out}")
    return code, out


def _summary(out: str) -> str:
    try:
        data = json.loads(out)
    except ValueError:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        keep = [ln for ln in lines if ln.startswith(("ESTADO", "rama", "commit", "salida completa"))]
        return "\n".join((keep or lines)[:8])
    datos = data.get("datos") or {}
    keys = ("motivo", "comando", "revisor", "veredicto", "tests_ok", "sha_revisado", "razones")
    shown = {"estado": data.get("estado"), **{k: datos[k] for k in keys if datos.get(k) not in (None, [], "")}}
    if data.get("errores") and "razones" not in shown:
        shown["errores"] = data["errores"]
    if isinstance(shown.get("sha_revisado"), str):
        shown["sha_revisado"] = shown["sha_revisado"][:7]
    return json.dumps(shown, ensure_ascii=False, indent=2)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="orquesta-demo-"))
    home, claude, proj = tmp / "orquesta-home", tmp / "claude-config", tmp / "shop"
    os.environ.update({
        "ORQUESTA_HOME": str(home), "CLAUDE_CONFIG_DIR": str(claude),
        "GIT_CONFIG_GLOBAL": str(tmp / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
        "GIT_COMMITTER_NAME": "demo", "GIT_COMMITTER_EMAIL": "demo@example.com",
    })
    print(f"Sandbox: {tmp}  (your real Claude Code config and orquesta state are never touched)")

    step(1, "Registry → deploy", "orquesta.yaml declares 3 agents; the generator validates it and writes the "
         "plugin plus a manifest the runner trusts (not the editable YAML).")
    home.mkdir(parents=True)
    claude.mkdir()
    dump(registry(), home / "orquesta.yaml")
    dump(backends(), home / "backends.yaml")
    (home / "manual").mkdir()
    (home / ".gitignore").write_text("state/\nbackups/\nworktrees/\nplugin/\n")
    git(home, "init", "-q", "-b", "main")
    git(home, "add", "-A")
    git(home, "commit", "-qm", "registry")
    from orquesta.generate import apply_plan, build_plan
    from orquesta.paths import default_paths
    plan = build_plan(default_paths())
    apply_plan(plan, "deploy", run_tests=None)
    print(f"   deployed: {', '.join(a['name'] for a in registry()['agents'])}")

    step(2, "A project with a bug", "shop/prices.py subtracts the discount percentage as money. "
         "`.orquesta/proyecto.toml` tells orquesta how to run its tests.")
    (proj / "shop").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / "shop" / "__init__.py").write_text("")
    (proj / "shop" / "prices.py").write_text(PRICES)
    (proj / "tests" / "test_prices.py").write_text(TESTS)
    (proj / ".orquesta").mkdir()
    (proj / ".orquesta" / "proyecto.toml").write_text(
        f'[worktree]\ntests = "{sys.executable} -m pytest -q -p no:cacheprovider"\n')
    git(proj, "init", "-q", "-b", "main")
    git(proj, "add", "-A")
    git(proj, "commit", "-qm", "shop with a pricing bug")

    branch = "orquesta/codex-forjador/fix-discount"
    step(3, "Delegate the fix to a write agent", "The runner creates a git worktree on its own branch; "
         "the agent never touches the user's checkout. The result is committed on that branch.")
    orquesta("run", "codex-forjador", "--cwd", str(proj), "--task", "fix-discount",
             expect=0, stdin="[demo:fix] final_price treats the discount as money; make it a percentage.")
    print(f"   main is untouched: {git(proj, 'log', '--oneline', 'main')}")
    print(f"   {branch}: {git(proj, 'log', '--oneline', '-1', branch)}")

    step(4, "A read-only agent that writes anyway", "The backend ignores its read-only flag. The runner "
         "compares the git state before/after and fails the run, naming the file.")
    orquesta("run", "codex-critico", "--cwd", str(proj), expect=7, stdin="[demo:snoop] Review shop/prices.py.")
    (proj / "notes.txt").unlink()

    step(5, "Rate limit", "Quota errors are classified (ESTADO: RATE_LIMIT) so the orchestrator can fall back "
         "instead of treating it as a code failure.")
    orquesta("run", "codex-critico", "--cwd", str(proj), expect=75, stdin="[demo:quota] Review the diff.")

    step(6, "Cross-family review", "The author was GPT, so the reviewer is Gemini. Tests run on the exact "
         "commit under review; both the SHA and the verdict are recorded.")
    orquesta("review", branch, "--repo", str(proj), expect=0)

    step(7, "Merge needs a human", "Without a terminal the CLI refuses (exit 3) and prints the exact "
         "command for the user to run in a second terminal. There is no flag or env var to skip it.")
    orquesta("merge", branch, "--repo", str(proj), expect=3)

    step(8, "The human confirms", "Here the demo plays the user typing the branch name at the TTY prompt.")
    from orquesta import cli
    orquesta("merge", branch, "--repo", str(proj), "--confirm", expect=0, cli_fn=cli.cmd_merge,
             leer_tty=lambda *_a, **_k: branch)
    print(f"   main now: {git(proj, 'log', '--oneline', '-1', 'main')}")

    bad = "orquesta/codex-forjador/make-tests-green"
    step(9, "An agent that 'fixes' tests by deleting an assertion", "The reviewer approves and the tests "
         "pass, but the merge guard reads the full diff and blocks it.")
    orquesta("run", "codex-forjador", "--cwd", str(proj), "--task", "make-tests-green",
             expect=0, stdin="[demo:weaken] Make the test suite green.")
    orquesta("review", bad, "--repo", str(proj), expect=0)
    orquesta("merge", bad, "--repo", str(proj), "--confirm", expect=3, cli_fn=cli.cmd_merge,
             leer_tty=lambda *_a, **_k: bad)
    print("\nDone: every step above ran the real orquesta code against scripted backends.")
    if "--keep" in sys.argv:
        print(f"Sandbox kept at {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
