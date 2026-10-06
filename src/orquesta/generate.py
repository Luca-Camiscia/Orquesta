"""Generador: orquesta.yaml → ~/.claude/agents/orquesta/*.md y el bloque de ~/.claude/CLAUDE.md.

build_plan valida, aplica los guardrails, renderiza y calcula el diff, sin escribir nada.
apply_plan hace backup → escribe → corre los tests → CHANGELOG + commit, todo en un paso; si algo
falla, restaura el backup. Fuera del repo de orquesta solo escribe en `paths.agents_dir` y en el
bloque marcado de `paths.claude_md`.
"""
from __future__ import annotations

import difflib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from filelock import FileLock

from orquesta import guard, manifest
from orquesta.claude_md import MarkerError, apply_block, find_block
from orquesta.config import load_all, read_manual, registry_at_head
from orquesta.paths import Paths
from orquesta.render import RenderContext, render_agent_md, render_block_inner
from orquesta.schema import ConfigError

KEEP_BACKUPS = 20
CHANGELOG_HEADER = ("# Changelog de orquesta\n\nCada `orquesta generate` aplicado agrega una entrada "
                    "acá, en el mismo commit. Lo más nuevo, arriba.\n")


class GenerateError(Exception):
    pass


class GuardError(GenerateError):
    pass


class TestsFailed(GenerateError):
    __test__ = False  # que pytest no la confunda con una clase de tests

    def __init__(self, output: str):
        self.output = output
        super().__init__(f"los tests fallaron; se restauró el backup y no se aplicó nada:\n{output}")


@dataclass
class Change:
    path: Path
    old: str | None
    new: str | None

    @property
    def kind(self) -> str:
        if self.old is None:
            return "crear"
        return "borrar" if self.new is None else "modificar"


@dataclass
class Plan:
    paths: Paths
    changes: list[Change] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    violations: list[guard.Violation] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not self.changes

    def diff(self) -> str:
        out: list[str] = []
        for c in self.changes:
            label = self.paths.pretty(c.path)
            if c.path == self.paths.manifest:
                out.append(f"~ {label} (manifiesto de deploy actualizado)\n")
                continue
            a = "/dev/null" if c.old is None else f"a/{label}"
            b = "/dev/null" if c.new is None else f"b/{label}"
            chunk = list(difflib.unified_diff((c.old or "").splitlines(keepends=True),
                                              (c.new or "").splitlines(keepends=True), a, b))
            if chunk and not chunk[-1].endswith("\n"):
                chunk[-1] += "\n"
            out.extend(chunk)
        return "".join(out)


@dataclass
class ApplyResult:
    applied: bool
    commit: str | None = None
    backup: Path | None = None
    summary: list[str] = field(default_factory=list)
    tests_output: str = ""


# --- plan ------------------------------------------------------------------------------------

def _read(path: Path) -> str | None:
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def _baseline(paths: Paths, manual_texts: dict[str, str]) -> dict[str, guard.Baseline] | None:
    try:
        deployed = manifest.load(paths)
    except manifest.ManifestError:
        deployed = None  # generate tiene que poder reparar un manifiesto roto: la línea base sale de HEAD
    if deployed is not None:
        return guard.baseline_from_manifest(deployed)
    head = registry_at_head(paths)
    return guard.baseline_from_registry(head, manual_texts) if head else None


def build_plan(paths: Paths, confirmations: Iterable[str] = ()) -> Plan:
    plan = Plan(paths=paths)
    try:
        reg, backends = load_all(paths)
    except ConfigError as exc:
        plan.errors = exc.errors
        return plan
    ctx = RenderContext(paths=paths, backends=backends)

    manual_texts: dict[str, str] = {}
    for a in reg.agents:
        if not a.manual:
            continue
        text = read_manual(paths, a.name)
        if text is not None:
            manual_texts[a.name] = text
        elif a.enabled:
            plan.errors.append(f"agente {a.name!r}: es manual pero no existe manual/{a.name}.md")

    plan.violations = guard.pending(guard.check(_baseline(paths, manual_texts), reg, manual_texts),
                                    confirmations)

    desired: dict[Path, str] = {}
    deployed: dict[str, dict] = {}
    for a in reg.enabled_agents():
        content = manual_texts.get(a.name) if a.manual else render_agent_md(a, ctx)
        if content is None:
            continue
        target = paths.agents_dir / f"{a.name}.md"
        desired[target] = content
        deployed[a.name] = {
            "file": target.name,
            "sha256": manifest.sha256(content),
            "backend": a.backend,
            "mode": a.mode,
            "isolation": a.effective_isolation,
            "backend_model": a.backend_model,
            "backend_effort": a.backend_effort,
            "wrapper_model": a.effective_wrapper_model,
            "backend_prompt": ctx.substitute(a.prompt).strip() if a.is_external else "",
            "manual": a.manual,
            "protected": a.protected,
            "permission_mode": a.extra_frontmatter.get("permissionMode"),
            "entry_sha256": guard.entry_hash(a, manual_texts.get(a.name)),
        }

    block = render_block_inner(reg, ctx)
    old_claude = _read(paths.claude_md)
    new_claude = old_claude
    try:
        new_claude = apply_block(old_claude, block)
    except MarkerError as exc:
        plan.errors.append(str(exc))

    desired[paths.manifest] = manifest.dumps({
        "generated_by": "orquesta",
        "registry_sha256": manifest.sha256(_read(paths.registry) or ""),
        "agents": deployed,
        "guard": {a.name: {"entry_sha256": guard.entry_hash(a, manual_texts.get(a.name)),
                           "mode": a.mode, "protected": a.protected,
                           "permission_mode": a.extra_frontmatter.get("permissionMode"),
                           "plugins": list(a.plugins)}
                  for a in reg.agents},
        "limits": reg.limits.model_dump(),
        "claude_md_block_sha256": manifest.sha256(block),
    })

    for path, content in [*desired.items(), (paths.claude_md, block)]:
        found = guard.scan_secrets(content)
        if found:
            plan.errors.append(f"posible secreto ({', '.join(found)}) en {paths.pretty(path)}: no "
                               f"se escribe nada. Sacalo del registro.")

    if paths.agents_dir.is_dir():
        for existing in sorted(paths.agents_dir.glob("*.md")):
            if existing not in desired:
                plan.changes.append(Change(existing, _read(existing), None))
    for path, content in desired.items():
        old = _read(path)
        if old != content:
            plan.changes.append(Change(path, old, content))
    if new_claude != old_claude:
        plan.changes.append(Change(paths.claude_md, old_claude, new_claude))
    plan.changes.sort(key=lambda c: str(c.path))
    plan.errors = list(dict.fromkeys(plan.errors))
    plan.summary = _summary(plan, deployed, old_claude)
    return plan


def _summary(plan: Plan, deployed: dict[str, dict], old_claude: str | None) -> list[str]:
    lines = []
    for c in plan.changes:
        if c.path == plan.paths.claude_md:
            had_block = old_claude is not None and _has_block(old_claude)
            lines.append("CLAUDE.md: bloque " + ("actualizado" if had_block else "creado"))
        elif c.path != plan.paths.manifest:
            name = c.path.stem
            if c.kind == "crear":
                d = deployed.get(name, {})
                lines.append(f"+ agente `{name}` ({d.get('backend')}, {d.get('mode')})")
            elif c.kind == "borrar":
                lines.append(f"- agente `{name}`")
            else:
                lines.append(f"~ agente `{name}`")
    if not lines and plan.changes:
        lines.append("manifiesto de deploy actualizado")
    return lines


def _has_block(text: str) -> bool:
    try:
        return find_block(text) is not None
    except MarkerError:
        return False


# --- apply -----------------------------------------------------------------------------------

def run_test_suite(paths: Paths) -> tuple[bool, str]:
    env = dict(os.environ, ORQUESTA_IN_GENERATE="1")
    try:
        proc = subprocess.run([sys.executable, "-m", "pytest", "-x", "-p", "no:cacheprovider"],
                              cwd=paths.home, capture_output=True, text=True, timeout=900, env=env)
    except subprocess.TimeoutExpired:
        return False, "los tests tardaron más de 15 minutos"
    out = (proc.stdout + proc.stderr).strip().splitlines()
    return proc.returncode == 0, "\n".join(out[-15:])


def apply_plan(plan: Plan, message: str, *,
               run_tests: Callable[[Paths], tuple[bool, str]] | None = run_test_suite,
               now: datetime | None = None) -> ApplyResult:
    if plan.errors:
        raise GenerateError("hay errores; no se aplicó nada:\n- " + "\n- ".join(plan.errors))
    if plan.violations:
        raise GuardError("hay cambios que requieren confirmación explícita del usuario:\n- "
                         + "\n- ".join(map(str, plan.violations)))
    if plan.empty:
        return ApplyResult(applied=False)
    paths = plan.paths
    now = now or datetime.now()
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(str(paths.state_dir / "generate.lock"), timeout=60):
        _verify_fresh(plan)
        backup = _backup(paths, now)
        old_changelog = _read(paths.changelog)
        tests_output = ""
        try:
            for change in plan.changes:
                _write(paths, change)
            if run_tests is not None:
                ok, tests_output = run_tests(paths)
                if not ok:
                    raise TestsFailed(tests_output)
            _add_changelog(paths, message, plan.summary, now)
            commit = _git_commit(paths, message)
        except BaseException:
            _restore(paths, backup)
            _restore_file(paths.changelog, old_changelog)
            raise
    _prune_backups(paths)
    return ApplyResult(True, commit, backup, plan.summary, tests_output)


def _verify_fresh(plan: Plan) -> None:
    for c in plan.changes:
        if _read(c.path) != c.old:
            raise GenerateError(f"{plan.paths.pretty(c.path)} cambió desde que se armó el plan; "
                                f"volvé a correr `orquesta generate --dry-run`.")


def _write(paths: Paths, change: Change) -> None:
    if not (change.path.parent == paths.agents_dir or change.path == paths.claude_md):
        raise GenerateError(f"bug: intento de escribir fuera de la zona permitida: {change.path}")
    if change.new is None:
        change.path.unlink(missing_ok=True)
        return
    change.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = change.path.with_name(f".{change.path.name}.tmp-{os.getpid()}")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        fh.write(change.new)
    os.replace(tmp, change.path)


def _backup(paths: Paths, now: datetime) -> Path:
    base = paths.backups_dir / now.strftime("%Y%m%d-%H%M%S-%f")
    dest, n = base, 1
    while dest.exists():
        n += 1
        dest = base.with_name(f"{base.name}-{n}")
    dest.mkdir(parents=True)
    meta = {"agents_dir_existed": paths.agents_dir.is_dir(),
            "claude_md_existed": paths.claude_md.is_file()}
    if meta["agents_dir_existed"]:
        shutil.copytree(paths.agents_dir, dest / "agents")
    if meta["claude_md_existed"]:
        shutil.copy2(paths.claude_md, dest / "CLAUDE.md")
    (dest / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return dest


def _restore(paths: Paths, backup: Path) -> None:
    meta = json.loads((backup / "meta.json").read_text(encoding="utf-8"))
    if paths.agents_dir.is_dir():
        for p in paths.agents_dir.iterdir():
            if p.is_file():
                p.unlink()
    if meta["agents_dir_existed"]:
        paths.agents_dir.mkdir(parents=True, exist_ok=True)
        for p in (backup / "agents").iterdir():
            if p.is_file():
                shutil.copy2(p, paths.agents_dir / p.name)
    elif paths.agents_dir.is_dir() and not any(paths.agents_dir.iterdir()):
        paths.agents_dir.rmdir()
    if meta["claude_md_existed"]:
        shutil.copy2(backup / "CLAUDE.md", paths.claude_md)
    else:
        paths.claude_md.unlink(missing_ok=True)


def _restore_file(path: Path, content: str | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)


def _prune_backups(paths: Paths) -> None:
    dirs = sorted(p for p in paths.backups_dir.iterdir() if p.is_dir())
    for old in dirs[:-KEEP_BACKUPS]:
        shutil.rmtree(old, ignore_errors=True)


def _add_changelog(paths: Paths, message: str, summary: list[str], now: datetime) -> None:
    entry = (f"\n## {now:%Y-%m-%d %H:%M} — {message}\n\n"
             + "".join(f"- {s}\n" for s in (summary or ["sin cambios de contenido"])))
    text = _read(paths.changelog) or CHANGELOG_HEADER
    i = text.find("\n## ")
    text = text.rstrip("\n") + "\n" + entry if i == -1 else text[:i] + entry + text[i:]
    _restore_file(paths.changelog, text)


def _git(paths: Paths, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=paths.home, capture_output=True, text=True)


def _git_commit(paths: Paths, message: str) -> str:
    specs = []
    for rel in ("orquesta.yaml", "backends.yaml", "manual", "CHANGELOG.md"):
        p = paths.home / rel
        has_files = p.is_file() or (p.is_dir() and any(x.is_file() for x in p.rglob("*")))
        if has_files or _git(paths, "ls-files", "--", rel).stdout.strip():
            specs.append(rel)
    for args in (("add", "--", *specs), ("commit", "-q", "-m", message, "--", *specs)):
        proc = _git(paths, *args)
        if proc.returncode != 0:
            raise GenerateError(f"git {args[0]} falló: {(proc.stderr or proc.stdout).strip()}")
    return _git(paths, "rev-parse", "--short", "HEAD").stdout.strip()
