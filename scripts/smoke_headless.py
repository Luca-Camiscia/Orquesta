#!/usr/bin/env python3
"""Smoke test headless de los backends (paso 1 del plan).

Corre cada variante en un subprocess SIN TTY (stdin/stdout/stderr como pipes), en las mismas
condiciones en que corren el runner de orquesta y el Bash tool de Claude Code, y reporta exit code,
stdout y latencia. Los resultados quedan en state/smoke-<fecha>.json.

Uso:
    python scripts/smoke_headless.py                 # variantes base de codex y agy
    python scripts/smoke_headless.py --only agy      # un solo backend
    python scripts/smoke_headless.py --fallbacks     # variantes alternativas de agy (bug no-TTY)
    python scripts/smoke_headless.py --write         # pruebas de modo write vs read-only
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = ROOT / "sandbox" / "smoke"
STATE = ROOT / "state"


@dataclass
class Variant:
    id: str
    backend: str
    argv: list[str]
    stdin: str | None = None
    cwd: Path | None = None
    timeout: int = 180
    stdout_to_file: bool = False
    expect: str | None = "PONG"
    # (archivo, debe_existir): para las pruebas de write / read-only
    check_file: tuple[str, bool] | None = None
    note: str = ""


@dataclass
class Result:
    id: str
    backend: str
    ok: bool
    exit_code: int | None
    latency_s: float
    stdout_head: str
    stderr_tail: str
    detail: str
    argv: list[str] = field(default_factory=list)


def _repo() -> Path:
    """Repo git descartable para las pruebas de escritura."""
    repo = SANDBOX / "repo"
    if not (repo / ".git").exists():
        repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
        (repo / "README.md").write_text("repo descartable del smoke test\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    return repo


def bwrap_readonly() -> list[str]:
    """Prefijo bubblewrap: todo el FS read-only salvo /tmp y el estado propio de agy (Linux)."""
    home = Path.home()
    return ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc",
            "--bind", "/tmp", "/tmp",
            "--bind", str(home / ".gemini"), str(home / ".gemini"),
            "--bind", str(home / ".cache"), str(home / ".cache"),
            "--die-with-parent", "--"]


def variants(args: argparse.Namespace) -> list[Variant]:
    out: list[Variant] = []
    cheap = ["--model", args.agy_cheap_model] if args.agy_cheap_model else []

    if args.only in (None, "codex"):
        common = ["codex", "exec", "--skip-git-repo-check", "--color", "never", "--ephemeral"]
        out += [
            Variant("codex-arg", "codex", common + ["--sandbox", "read-only", "Reply PONG"],
                    note="prompt como argumento"),
            Variant("codex-stdin", "codex", common + ["--sandbox", "read-only", "-"],
                    stdin="Reply PONG", note="prompt por stdin (`-`)"),
        ]
    if args.only in (None, "agy"):
        out += [
            Variant("agy-text", "agy", ["agy", "-p", "Reply PONG"],
                    note="invocación base, salida text"),
            Variant("agy-stdin-dash", "agy", ["agy", *cheap, "-p", "-"], stdin="Reply PONG",
                    note="prompt por stdin con `-p -`"),
            Variant("agy-stdin-bare", "agy", ["agy", *cheap, "-p"], stdin="Reply PONG",
                    note="prompt por stdin con `-p` sin valor"),
        ]
        if args.fallbacks:
            base = ["agy", *cheap]
            out += [
                Variant("agy-json", "agy", base + ["--output-format", "json", "-p", "Reply PONG"],
                        note="--output-format json"),
                Variant("agy-stream-json", "agy",
                        base + ["--output-format", "stream-json", "-p", "Reply PONG"],
                        note="--output-format stream-json"),
                Variant("agy-to-file", "agy", base + ["-p", "Reply PONG"], stdout_to_file=True,
                        note="stdout redirigido a archivo"),
                Variant("agy-script", "agy",
                        ["script", "-q", "-e", "-c", shlex.join(base + ["-p", "Reply PONG"]),
                         "/dev/null"],
                        note="pseudo-TTY con `script -q` (solo Linux)"),
            ]

    if args.write:
        repo = _repo()
        create = "Create a file named {f} in the current directory containing exactly the word {w}. Do nothing else."
        if args.only in (None, "codex"):
            common = ["codex", "exec", "--skip-git-repo-check", "--color", "never", "--ephemeral",
                      "-C", str(repo)]
            out += [
                Variant("codex-write", "codex", common + ["--sandbox", "workspace-write", "-"],
                        stdin=create.format(f="codex_write.txt", w="PONG"), cwd=repo, expect=None,
                        check_file=("codex_write.txt", True), timeout=300,
                        note="--sandbox workspace-write tiene que crear el archivo"),
                Variant("codex-readonly", "codex", common + ["--sandbox", "read-only", "-"],
                        stdin=create.format(f="codex_nope.txt", w="NOPE"), cwd=repo, expect=None,
                        check_file=("codex_nope.txt", False), timeout=300,
                        note="--sandbox read-only NO tiene que crear el archivo"),
            ]
        if args.only in (None, "agy"):
            out += [
                Variant("agy-write", "agy",
                        ["agy", *cheap, "--mode", "accept-edits", "-p",
                         create.format(f="agy_write.txt", w="PONG")],
                        cwd=repo, expect=None, check_file=("agy_write.txt", True), timeout=300,
                        note="--mode accept-edits tiene que crear el archivo"),
                Variant("agy-plan", "agy",
                        ["agy", *cheap, "--mode", "plan", "-p",
                         create.format(f="agy_nope.txt", w="NOPE")],
                        cwd=repo, expect=None, check_file=("agy_nope.txt", False), timeout=300,
                        note="--mode plan NO tiene que crear el archivo"),
                # Hallazgo del paso 1: --mode plan NO es read-only (ver docs/headless.md).
                # La garantía real es un sandbox de SO: todo read-only salvo el estado de agy.
                Variant("agy-ro-bwrap", "agy",
                        [*bwrap_readonly(), "agy", *cheap, "--output-format", "json", "-p",
                         "First, read README.md and tell me its first line. Then "
                         + create.format(f="agy_bwrap.txt", w="NOPE")],
                        cwd=repo, expect="repo descartable", check_file=("agy_bwrap.txt", False),
                        timeout=300, note="bwrap read-only: tiene que leer pero NO escribir"),
            ]
    if args.ids:
        wanted = set(args.ids.split(","))
        out = [v for v in out if v.id in wanted]
    return out


def run(v: Variant) -> Result:
    cwd = v.cwd or SANDBOX
    cwd.mkdir(parents=True, exist_ok=True)
    if v.check_file:
        (cwd / v.check_file[0]).unlink(missing_ok=True)
    out_file = SANDBOX / f"{v.id}.stdout"
    t0 = time.monotonic()
    try:
        if v.stdout_to_file:
            with open(out_file, "w", encoding="utf-8") as fh:
                proc = subprocess.run(v.argv, cwd=cwd, input=v.stdin, stdout=fh,
                                      stderr=subprocess.PIPE, text=True, timeout=v.timeout,
                                      stdin=None if v.stdin is not None else subprocess.DEVNULL)
            stdout = out_file.read_text(encoding="utf-8", errors="replace")
        else:
            proc = subprocess.run(v.argv, cwd=cwd, input=v.stdin, capture_output=True, text=True,
                                  timeout=v.timeout,
                                  stdin=None if v.stdin is not None else subprocess.DEVNULL)
            stdout = proc.stdout
        code, stderr = proc.returncode, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or b"").decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        code = None
    except FileNotFoundError as exc:
        return Result(v.id, v.backend, False, None, 0.0, "", str(exc), "binario no encontrado", v.argv)
    latency = round(time.monotonic() - t0, 1)

    ok, detail = True, "ok"
    if code is None:
        ok, detail = False, f"timeout ({v.timeout}s)"
    elif code != 0:
        ok, detail = False, f"exit {code}"
    if ok and v.expect is not None:
        if not stdout.strip():
            ok, detail = False, "stdout vacío"
        elif v.expect.lower() not in stdout.lower():
            ok, detail = False, f"stdout sin '{v.expect}'"
    if v.check_file:
        name, must_exist = v.check_file
        exists = (cwd / name).exists()
        if exists != must_exist:
            ok = False
            detail = f"{name} {'no se creó' if must_exist else 'SE CREÓ (no debía)'}"
        elif ok:
            detail = f"{name} {'creado' if must_exist else 'no creado'} (correcto)"
    return Result(v.id, v.backend, ok, code, latency, stdout.strip()[:400],
                  stderr.strip()[-600:], detail, v.argv)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["codex", "agy"])
    ap.add_argument("--fallbacks", action="store_true", help="variantes alternativas de agy")
    ap.add_argument("--write", action="store_true", help="pruebas de write vs read-only")
    ap.add_argument("--ids", help="correr solo estas variantes (separadas por coma)")
    ap.add_argument("--agy-cheap-model", default="gemini-3.8-flash-low",
                    help="modelo barato para variantes secundarias de agy ('' = default de agy)")
    args = ap.parse_args()

    results = []
    for v in variants(args):
        print(f"→ {v.id:<16} {v.note}", flush=True)
        r = run(v)
        results.append(r)
        mark = "OK  " if r.ok else "FALLA"
        print(f"  {mark} exit={r.exit_code} {r.latency_s:>6}s  {r.detail}", flush=True)
        if r.stdout_head:
            print("  stdout: " + r.stdout_head.replace("\n", "\n          ")[:300], flush=True)
        if not r.ok and r.stderr_tail:
            print("  stderr: " + r.stderr_tail.replace("\n", "\n          ")[-400:], flush=True)

    STATE.mkdir(exist_ok=True)
    dest = STATE / f"smoke-{datetime.now():%Y%m%d-%H%M%S}.json"
    dest.write_text(json.dumps([asdict(r) for r in results], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResultados: {dest}")
    print("\n| variante | backend | resultado | exit | latencia |")
    print("|---|---|---|---|---|")
    for r in results:
        print(f"| {r.id} | {r.backend} | {'✅ ' if r.ok else '❌ '}{r.detail} | {r.exit_code} | {r.latency_s}s |")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
