"""Datos y utilidades compartidas por los tests."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

REPO = Path(__file__).resolve().parents[1]
FAKE_BACKEND = REPO / "tests" / "fake_backend.py"


def backends_dict(fake: Path = FAKE_BACKEND) -> dict:
    """backends.yaml de prueba: los CLIs son un script falso (tests/fake_backend.py)."""
    py = sys.executable
    return {
        "version": 1,
        "backends": {
            "codex": {
                "label": "GPT (Codex CLI)",
                "command": [py, str(fake), "codex"],
                "common_flags": ["--color", "never"],
                "cwd_flag": ["-C", "{cwd}"],
                "model_flag": ["-m", "{model}"],
                "prompt": {"via": "stdin", "flag": ["-"]},
                "output": {"format": "text"},
                "modes": {
                    "read-only": {"flags": ["--sandbox", "read-only"], "timeout_s": 30},
                    "write": {"flags": ["--sandbox", "workspace-write"], "timeout_s": 30},
                },
                "rate_limit_patterns": ["usage limit"],
            },
            "agy": {
                "label": "Gemini (Antigravity CLI)",
                "command": [py, str(fake), "agy"],
                "common_flags": ["--output-format", "json"],
                "model_flag": ["--model", "{model}"],
                "prompt": {"via": "arg", "flag": ["-p"], "max_arg_chars": 2000},
                "output": {"format": "json", "text_key": "response", "status_key": "status",
                           "ok_status": "SUCCESS"},
                "modes": {
                    "read-only": {"flags": [], "timeout_s": 30},
                    "write": {"flags": ["--mode", "accept-edits"], "timeout_s": 30},
                },
                "rate_limit_patterns": ["quota reached"],
                "auth_patterns": ["Authentication required"],
            },
            "claude": {
                "label": "Claude (subagente nativo)",
                "native": True,
                "command": ["claude", "-p"],
                "model_flag": ["--model", "{model}"],
                "prompt": {"via": "arg"},
            },
        },
    }


def registry_dict() -> dict:
    return {
        "orchestrator": {
            "enabled": True,
            "default_mood": "transparente",
            "show_mood_tag": True,
            "moods": {
                "silencioso": "Solo resultado final.",
                "transparente": "Plan, delegaciones y verificación.",
                "debate": "Como transparente, más desacuerdos.",
            },
        },
        "limits": {"max_parallel": {"codex": 2, "agy": 1, "claude": 3}},
        "agents": [
            {"name": "gemini-lector", "backend": "agy", "role": "Lee contexto gigante.",
             "route_when": ["hay que leer un repo entero"], "tools": ["Bash"],
             "prompt": "Sos el lector. Citá rutas."},
            {"name": "codex-forja", "backend": "codex", "mode": "write",
             "role": "Implementa fixes acotados.", "route_when": ["fix mecánico"],
             "tools": ["Bash"], "prompt": "Sos la forja."},
            {"name": "codex-critico", "backend": "codex", "role": "Review adversarial.",
             "route_when": ["antes de mergear"], "tools": ["Bash"], "prompt": "Sos el crítico."},
            {"name": "mantenedor", "display_name": "el_mantenedor", "backend": "claude",
             "mode": "write", "isolation": "none", "wrapper_model": "sonnet", "protected": True,
             "role": "Mantiene el ecosistema.", "route_when": ["algo del ecosistema se rompió"],
             "tools": ["Read", "Write", "Edit", "Bash"],
             "prompt": "Sos el mantenedor. Repo: {orquesta_home}. Agentes: {agents_dir}."},
        ],
    }


def dump_yaml(data, path: Path) -> None:
    y = YAML(typ="safe", pure=True)
    y.default_flow_style = False
    with open(path, "w", encoding="utf-8") as fh:
        y.dump(data, fh)


def load_yaml(path: Path):
    return YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def snapshot(root: Path) -> dict[str, bytes]:
    """Contenido de todos los archivos bajo root (para comparar antes/después)."""
    if not root.exists():
        return {}
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file() and ".git" not in p.parts}
