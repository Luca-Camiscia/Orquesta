"""Fase de deploy por claude_dir (legacy | plugin)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from filelock import FileLock

from orquesta.paths import Paths


def _sha12_claude_dir(claude_dir: Path) -> str:
    """Devuelve sha256(str(claude_dir.resolve()))[:12]."""
    return hashlib.sha256(str(claude_dir.resolve()).encode("utf-8")).hexdigest()[:12]


def fase(paths: Paths) -> str:
    """Lee la fase de state/migracion/<sha12>.json; si no existe, devuelve legacy."""
    path = paths.state_dir / "migracion" / f"{_sha12_claude_dir(paths.claude_dir)}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return "legacy"
    return data["fase"]


def set_fase(paths: Paths, nueva_fase: str) -> None:
    """Valida y persiste la fase con lock exclusivo y tmp + rename atómico.

    Raises:
        ValueError: si nueva_fase no es legacy o plugin.
    """
    if nueva_fase not in ("legacy", "plugin"):
        raise ValueError(f"fase inválida: {nueva_fase!r}; usá 'legacy' o 'plugin'")
    folder = paths.state_dir / "migracion"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{_sha12_claude_dir(paths.claude_dir)}.json"
    with FileLock(str(path) + ".lock"):
        data = {"fase": nueva_fase, "claude_dir": str(paths.claude_dir.resolve()),
                "ts": time.time()}
        tmp = path.with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
