"""Catálogos locales de modelos; solo Antigravity consulta su CLI (con cache en memoria)."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from threading import Lock

from orquesta import catalog


def read_codex_models(path: Path | None = None) -> list[dict]:
    path = path if path is not None else Path.home() / ".codex" / "models_cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("models") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    models = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("slug"), str) or not item["slug"]:
            continue
        levels = item.get("supported_reasoning_levels") or []
        models.append({
            "id": item["slug"],
            "display_name": item.get("display_name") or item["slug"],
            "reasoning_levels": [level["effort"] for level in levels
                                 if isinstance(level, dict) and isinstance(level.get("effort"), str)],
            "default_reasoning_level": item.get("default_reasoning_level"),
        })
    return models


def parse_agy_models(output: str) -> list[dict]:
    models = []
    for line in output.splitlines():
        if "\t" not in line:
            continue
        slug, name = (part.strip() for part in line.split("\t", 1))
        if slug:
            models.append({"id": slug, "display_name": name or slug,
                           "reasoning_levels": [], "default_reasoning_level": None})
    return models


class ModelDiscovery:
    """Una instancia por UI; paths, comando, ejecución y reloj inyectables para tests."""

    def __init__(self, *, codex_cache: Path | None = None,
                 agy_command: list[str] | None = None, run=None, clock=None):
        self.codex_cache = codex_cache
        self.agy_command = agy_command if agy_command is not None else ["agy", "models"]
        self.run = run if run is not None else subprocess.run
        self.clock = clock if clock is not None else time.monotonic
        self._agy: list[dict] = []
        self._expires = 0.0
        self._lock = Lock()

    def get_models(self) -> dict[str, list[dict]]:
        with self._lock:
            if self.clock() >= self._expires:
                try:
                    result = self.run(self.agy_command, capture_output=True, text=True,
                                      timeout=20, check=True)
                    self._agy = parse_agy_models(result.stdout)
                except (OSError, subprocess.SubprocessError):
                    self._agy = []
                self._expires = self.clock() + 600
        aliases = getattr(catalog, "MODEL_ALIASES", {"haiku", "sonnet", "opus", "inherit"})
        return {"codex": read_codex_models(self.codex_cache), "agy": self._agy,
                "claude": [{"id": name, "display_name": name, "reasoning_levels": [],
                            "default_reasoning_level": None} for name in sorted(aliases)]}
