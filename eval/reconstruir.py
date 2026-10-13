#!/usr/bin/env python3
"""Reproduce the published results from the traces, without calling any model.

For every eval/trazas/<task>-<condition>-<rep>/final.patch: rebuild the initial project exactly as
run_eval.py did, apply the agent's final patch, run the hidden tests and compare with the row in
resultados-d.jsonl. Exit code 1 if any result differs.

    uv run python eval/reconstruir.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Tal como quedó en las trazas: run_eval.py usa sys.executable y la traza reemplaza el home por "~".
TESTS = '~/orquesta/.venv/bin/python3 -m pytest -q -p no:cacheprovider test_visible.py'


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True)


def reconstruir(traza: Path) -> bool:
    tarea = traza.name.split("-")[0]
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d) / "proyecto"
        shutil.copytree(HERE / "tareas" / tarea, proj, ignore=shutil.ignore_patterns("test_oculto.py", "__pycache__"))
        (proj / ".orquesta").mkdir()
        (proj / ".orquesta" / "proyecto.toml").write_text(f'[worktree]\ntests = "{TESTS}"\n')
        sh(proj, "git", "init", "-q")
        sh(proj, "git", "add", "-A")
        sh(proj, "git", "-c", "user.name=eval", "-c", "user.email=eval@example.com", "commit", "-qm", "inicial")
        sh(proj, "git", "apply", str(traza / "final.patch"))
        shutil.copy(HERE / "tareas" / tarea / "test_oculto.py", proj)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_oculto.py"],
                           cwd=proj, capture_output=True, text=True, timeout=120)
        return r.returncode == 0


def main() -> int:
    publicados = {f"{f['tarea']}-{f['condicion']}-{f['rep']}": f["ocultos_ok"]
                  for f in map(json.loads, (HERE / "resultados-d.jsonl").read_text().splitlines())}
    distintos = []
    for traza in sorted(p for p in (HERE / "trazas").iterdir() if (p / "final.patch").is_file()):
        ok = reconstruir(traza)
        if ok != publicados.get(traza.name):
            distintos.append(traza.name)
        print(f"{traza.name:16} hidden tests {'pass' if ok else 'FAIL'}  published {publicados.get(traza.name)}")
    print(f"\n{len(publicados) - len(distintos)}/{len(publicados)} match the published results")
    return 1 if distintos else 0


if __name__ == "__main__":
    sys.exit(main())
