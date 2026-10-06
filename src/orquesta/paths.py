"""Rutas de orquesta y de Claude Code.

Todo lo que orquesta lee o escribe sale de acá, así los tests pueden apuntar a directorios
temporales. Fuera del repo, orquesta solo escribe en `agents_dir` y en el bloque marcado de
`claude_md`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    home: Path        # raíz del repo de orquesta (~/orquesta)
    claude_dir: Path  # config de Claude Code (~/.claude o $CLAUDE_CONFIG_DIR)

    @property
    def registry(self) -> Path:
        return self.home / "orquesta.yaml"

    @property
    def backends(self) -> Path:
        return self.home / "backends.yaml"

    @property
    def manual_dir(self) -> Path:
        return self.home / "manual"

    @property
    def audits_dir(self) -> Path:
        return self.home / "audits"

    @property
    def changelog(self) -> Path:
        return self.home / "CHANGELOG.md"

    @property
    def state_dir(self) -> Path:
        return self.home / "state"

    @property
    def backups_dir(self) -> Path:
        return self.home / "backups"

    @property
    def worktrees_dir(self) -> Path:
        return self.home / "worktrees"

    @property
    def agents_dir(self) -> Path:
        return self.claude_dir / "agents" / "orquesta"

    @property
    def manifest(self) -> Path:
        return self.agents_dir / ".orquesta-manifest.json"

    @property
    def legacy_manifest(self) -> Path:
        return self.manifest

    @property
    def plugin_root(self) -> Path:
        return self.home / "plugin"

    @property
    def plugin_dir(self) -> Path:
        return self.plugin_root / "orquesta"

    @property
    def plugin_manifest(self) -> Path:
        return self.plugin_root / ".orquesta-manifest.json"

    @property
    def claude_md(self) -> Path:
        return self.claude_dir / "CLAUDE.md"

    @property
    def plugins_dir(self) -> Path:
        return self.claude_dir / "plugins"

    def pretty(self, path: Path) -> str:
        """Ruta legible: abrevia el home del usuario con ~."""
        try:
            return "~/" + str(Path(path).relative_to(Path.home()))
        except ValueError:
            return str(path)


def default_paths() -> Paths:
    home = Path(os.environ.get("ORQUESTA_HOME") or REPO_ROOT).expanduser()
    claude = os.environ.get("CLAUDE_CONFIG_DIR")
    claude_dir = Path(claude).expanduser() if claude else Path.home() / ".claude"
    return Paths(home=home, claude_dir=claude_dir)
