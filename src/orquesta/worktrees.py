"""Worktrees de agentes write.

Cada tarea corre en un `git worktree` propio bajo ~/orquesta/worktrees/<proyecto>-<hash>/, en la rama
`orquesta/<agente>/<tarea>`. El repo del usuario no se toca: el orquestador revisa el diff y mergea.
Un registro en state/worktrees.json le permite a doctor detectar huérfanos.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from filelock import FileLock

from orquesta.paths import Paths


class WorktreeError(Exception):
    pass


@dataclass
class Worktree:
    repo: Path
    path: Path
    branch: str
    base_sha: str
    agent: str
    task: str
    created_at: str

    def to_json(self) -> dict:
        d = asdict(self)
        d["repo"], d["path"] = str(self.repo), str(self.path)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Worktree":
        return cls(**{**d, "repo": Path(d["repo"]), "path": Path(d["path"])})


@dataclass
class Orphan:
    branch: str
    path: Path
    repo: Path
    reason: str


def slugify(text: str, max_len: int = 40) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:max_len].strip("-") or "tarea"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise WorktreeError(f"git {' '.join(args)} falló: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def diff_completo(repo: Path, base: str, head: str) -> str:
    """Diff completo para la guardia de tests: sin límite, sin detectar renombres (un `git mv` de un archivo
    de tests saldría sin hunks), forzado a texto (un `.gitattributes` con `-diff` o `binary` lo esconde),
    sin colores ni escapes de nombres (`core.quotePath`) ni prefijos de la configuración del usuario, y tolerante a bytes no UTF-8 (un Latin-1 no puede apagar la guardia)."""
    proc = subprocess.run(["git", "-c", "core.quotePath=false", "diff", "--no-renames", "--no-ext-diff", "--no-textconv",
                           "--text", "--no-color", "--src-prefix=a/", "--dst-prefix=b/", f"{base}..{head}", "--"], cwd=repo,
                          capture_output=True)
    if proc.returncode != 0:
        raise WorktreeError(f"git diff falló: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def git_toplevel(path: Path) -> Path | None:
    proc = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True,
                          text=True)
    return Path(proc.stdout.strip()) if proc.returncode == 0 else None


def _branch_tip(repo: Path, branch: str) -> str:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)


def _is_merged(repo: Path, branch: str) -> bool:
    return subprocess.run(["git", "merge-base", "--is-ancestor", branch, "HEAD"], cwd=repo,
                          capture_output=True).returncode == 0


def project_id(repo: Path) -> str:
    digest = hashlib.sha1(str(repo.resolve()).encode()).hexdigest()[:8]
    return f"{slugify(repo.name, 30)}-{digest}"


def _registry(paths: Paths) -> Path:
    return paths.state_dir / "worktrees.json"


def _lock(paths: Paths) -> FileLock:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    return FileLock(str(paths.state_dir / "worktrees.lock"), timeout=30)


def _load(paths: Paths) -> list[Worktree]:
    try:
        return [Worktree.from_json(d) for d in json.loads(_registry(paths).read_text())]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(paths: Paths, items: list[Worktree]) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    _registry(paths).write_text(json.dumps([w.to_json() for w in items], indent=2,
                                           ensure_ascii=False) + "\n")


def create(paths: Paths, *, repo: Path, agent: str, task: str, base: str = "HEAD") -> Worktree:
    root = git_toplevel(repo)
    if root is None:
        raise WorktreeError(f"{repo} no es un repo git")
    slug = slugify(task)
    with _lock(paths):
        n = 1
        while True:
            suffix = "" if n == 1 else f"-{n}"
            branch = f"orquesta/{agent}/{slug}{suffix}"
            path = paths.worktrees_dir / project_id(root) / f"{agent}--{slug}{suffix}"
            if not _branch_tip(root, branch) and not path.exists():
                break
            n += 1
        base_sha = _git(root, "rev-parse", "--verify", base)
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(root, "worktree", "add", "-q", "-b", branch, str(path), base_sha)
        wt = Worktree(repo=root, path=path, branch=branch, base_sha=base_sha, agent=agent,
                      task=task, created_at=datetime.now(timezone.utc).isoformat())
        _save(paths, [*_load(paths), wt])
    return wt


def project_config(repo: Path) -> dict | None:
    """`.orquesta/proyecto.toml` del repo: setup y archivos a copiar en cada worktree."""
    import tomllib
    f = repo / ".orquesta" / "proyecto.toml"
    if not f.is_file():
        return None
    try:
        return tomllib.loads(f.read_text(encoding="utf-8")).get("worktree") or {}
    except tomllib.TOMLDecodeError as exc:
        raise WorktreeError(f"{f} no es TOML válido: {exc}") from None


def _env_for(wt: Worktree) -> dict:
    import os
    port = 20000 + int(hashlib.sha1(wt.branch.encode()).hexdigest(), 16) % 10000
    return {**os.environ, "ORQUESTA_WORKTREE": str(wt.path), "ORQUESTA_PORT": str(port),
            "ORQUESTA_BRANCH": wt.branch}


def prepare(paths: Paths, wt: Worktree) -> dict | None:
    """Copia archivos sin versionar (p. ej. .env) y corre el `setup` del proyecto en el worktree."""
    cfg = project_config(wt.repo)
    if cfg is None:
        return None
    for rel in cfg.get("copiar") or []:
        src = wt.repo / rel
        if src.is_file():
            dst = wt.path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    setup = cfg.get("setup")
    if setup:
        try:
            proc = subprocess.run(["bash", "-c", setup], cwd=wt.path, capture_output=True, text=True,
                                  timeout=int(cfg.get("timeout_s", 600)), env=_env_for(wt))
        except subprocess.TimeoutExpired:
            raise WorktreeError(f"el setup del worktree no terminó en {cfg.get('timeout_s', 600)} s") from None
        if proc.returncode != 0:
            raise WorktreeError(f"el setup del worktree falló (código {proc.returncode}): "
                                f"{(proc.stderr or proc.stdout).strip()[-300:]}")
    return cfg


def run_tests(wt: Worktree, tests_cmd: str | None = None) -> dict | None:
    """Corre el comando `tests` del proyecto en el worktree: evidencia ejecutable antes de mergear."""
    try:
        cfg = project_config(wt.repo) or {}
    except WorktreeError:
        return None
    cmd = tests_cmd or cfg.get("tests")
    if not cmd or not wt.path.exists():
        return None
    try:
        p = subprocess.run(["bash", "-c", cmd], cwd=wt.path, capture_output=True, text=True,
                           timeout=int(cfg.get("timeout_s", 600)), env=_env_for(wt))
        ok, salida = p.returncode == 0, (p.stdout + p.stderr).strip()[-1500:]
    except subprocess.TimeoutExpired:
        ok, salida = False, "los tests no terminaron a tiempo"
    return {"ok": ok, "cmd": cmd, "salida": salida}


def _archive(wt: Worktree) -> None:
    try:
        cfg = project_config(wt.repo) or {}
    except WorktreeError:
        return
    if cfg.get("archive") and wt.path.exists():
        subprocess.run(["bash", "-c", cfg["archive"]], cwd=wt.path, capture_output=True, timeout=120,
                       env=_env_for(wt))


def commit_all(wt: Worktree, message: str) -> str | None:
    """Commitea todo lo que haya en el worktree. None si no había cambios."""
    _git(wt.path, "add", "-A")
    if not _git(wt.path, "status", "--porcelain"):
        return None
    _git(wt.path, "commit", "-q", "-m", message)
    return _git(wt.path, "rev-parse", "--short", "HEAD")


def diffstat(wt: Worktree) -> str:
    return _git(wt.repo, "diff", "--stat", f"{wt.base_sha}..{wt.branch}")


def list_worktrees(paths: Paths) -> list[Worktree]:
    return _load(paths)


def orphans(paths: Paths, now: datetime | None = None) -> list[Orphan]:
    now = now or datetime.now(timezone.utc)
    out: list[Orphan] = []
    for wt in _load(paths):
        def add(reason: str) -> None:
            out.append(Orphan(wt.branch, wt.path, wt.repo, reason))
        if not wt.repo.exists():
            add("el repo original no existe")
            continue
        if not wt.path.exists():
            add("el directorio del worktree no existe (corré `orquesta wt prune`)")
            continue
        tip = _branch_tip(wt.repo, wt.branch)
        if not tip:
            add("la rama ya no existe")
            continue
        age = now - datetime.fromisoformat(wt.created_at)
        if tip != wt.base_sha and _is_merged(wt.repo, wt.branch):
            add(f"ya mergeada: limpiala con `orquesta wt clean {wt.branch}`")
        elif tip == wt.base_sha and age.total_seconds() > 86400:
            add("sin commits hace más de un día")
        elif age.days > 7:
            add("abierta hace más de 7 días")
    return out


def clean(paths: Paths, branch: str, force: bool = False, repo: str | Path | None = None) -> None:
    with _lock(paths):
        items = _load(paths)
        wt = _find(paths, branch, repo)
        if wt.repo.exists():
            tip = _branch_tip(wt.repo, branch)
            if tip and tip != wt.base_sha and not _is_merged(wt.repo, branch) and not force:
                raise WorktreeError(f"la rama {branch} tiene commits sin mergear; si querés "
                                    f"descartarla, usá --force")
            _archive(wt)
            if wt.path.exists():
                _git(wt.repo, "worktree", "remove", *(["--force"] if force else []), str(wt.path))
            else:
                _git(wt.repo, "worktree", "prune")
            if tip:
                _git(wt.repo, "branch", "-D" if force else "-d", branch)
        else:
            shutil.rmtree(wt.path, ignore_errors=True)
        _save(paths, [w for w in items if w != wt])


def _find(paths: Paths, branch: str, repo: str | Path | None = None) -> Worktree:
    resolved = Path(repo).resolve() if repo is not None else None
    matches = [w for w in _load(paths) if w.branch == branch
               and (resolved is None or w.repo.resolve() == resolved)]
    repos = sorted({str(w.repo.resolve()) for w in matches})
    if resolved is None and len(repos) > 1:
        lista = "\n".join(repos)
        raise WorktreeError(f"la rama '{branch}' existe en varios repos registrados:\n{lista}\n"
                            "Pasá `--repo <ruta>` para desambiguar")
    wt = matches[0] if matches else None
    if wt is None:
        raise WorktreeError(f"no hay ningún worktree registrado con la rama {branch}")
    return wt


def changed_files(wt: Worktree) -> set[str]:
    out = _git(wt.repo, "diff", "--name-only", f"{wt.base_sha}..{wt.branch}", check=False)
    return {f for f in out.splitlines() if f}


def conflicts(paths: Paths, branch: str, repo: str | Path | None = None) -> dict:
    """¿El merge con la rama actual del repo da conflicto? ¿Qué otras ramas abiertas tocan los mismos archivos?"""
    wt = _find(paths, branch, repo)
    proc = subprocess.run(["git", "merge-tree", "--write-tree", "HEAD", branch], cwd=wt.repo,
                          capture_output=True, text=True)
    mine = changed_files(wt)
    overlaps = sorted(o.branch for o in _load(paths)
                      if o.branch != branch and o.repo == wt.repo and o.path.exists()
                      and mine & changed_files(o))
    return {"merge_conflict": proc.returncode == 1, "overlaps": overlaps}


def merge(paths: Paths, branch: str, repo: str | Path | None = None, sha: str | None = None) -> None:
    """Mergea la rama en el repo original (aborta si hay conflicto) y limpia el worktree.

    Con `sha` solo mergea ese commit y falla si la rama avanzó: es el que se revisó.
    """
    wt = _find(paths, branch, repo)
    if sha is not None and _branch_tip(wt.repo, branch) != sha:
        raise WorktreeError(f"la rama {branch} cambió después de revisarla: volvé a revisarla antes de mergear")
    proc = subprocess.run(["git", "merge", "--no-edit", sha or branch], cwd=wt.repo, capture_output=True, text=True)
    if proc.returncode != 0:
        subprocess.run(["git", "merge", "--abort"], cwd=wt.repo, capture_output=True)
        raise WorktreeError(f"el merge falló y se abortó: {(proc.stderr or proc.stdout).strip()[:300]}")
    clean(paths, branch, repo=wt.repo)


def merge_sha(paths: Paths, branch: str, sha: str, destino: str,
              repo: str | Path | None = None) -> dict:
    """Mergea el tip revisado; la base debe seguir siendo ancestro del destino."""
    wt = _find(paths, branch, repo)
    actual = _git(wt.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if actual != destino:
        raise WorktreeError(f"el repo debe estar en la rama destino '{destino}' (actual: {actual or 'HEAD separado'})")
    if (_git(wt.repo, "status", "--porcelain", "--untracked-files=no")
            or _git(wt.path, "status", "--porcelain", "--untracked-files=no")):
        raise WorktreeError("el repo y el worktree deben tener el árbol limpio antes de mergear")
    if _branch_tip(wt.repo, branch) != sha:
        raise WorktreeError(f"review vieja: corré `orquesta review {branch}`")
    if not _is_merged(wt.repo, wt.base_sha):
        raise WorktreeError("el SHA base ya no es ancestro del destino")
    try:
        _git(wt.repo, "merge", "--no-edit", sha)
    except WorktreeError:
        _git(wt.repo, "merge", "--abort", check=False)
        raise
    return {"status": "ok", "sha_merged": sha, "destino": destino}


def discard(paths: Paths, branch: str, repo: str | Path | None = None) -> None:
    clean(paths, branch, force=True, repo=repo)


def prune(paths: Paths) -> list[str]:
    """Saca del registro los worktrees cuyo directorio ya no existe. Devuelve las ramas sacadas."""
    with _lock(paths):
        items = _load(paths)
        gone = [w for w in items if not w.path.exists()]
        for repo in {w.repo for w in gone if w.repo.exists()}:
            _git(repo, "worktree", "prune", check=False)
        _save(paths, [w for w in items if w.path.exists()])
    return [w.branch for w in gone]
