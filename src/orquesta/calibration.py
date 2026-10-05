"""Calibración de la CONFIANZA declarada por cada backend.

La confianza verbalizada de los LLMs está sobreestimada (Xiong et al. 2024, arXiv 2306.13063; Prasad y
Nguyen 2025, arXiv 2505.19184), y solo sirve para ponderar si está calibrada (arXiv 2601.19921). Acá se
registra cada par (confianza declarada, acierto real), donde el acierto sale de los tests del proyecto o
de la review, y se arma una tabla por rangos. Hasta que haya datos, la UI muestra la confianza como
"declarada, sin calibrar" y orquesta no pondera votos con ella.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from filelock import FileLock

from orquesta.paths import Paths

RANGOS = ((0, 49, "0-49"), (50, 79, "50-79"), (80, 100, "80-100"))


def _file(paths: Paths):
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    return paths.state_dir / "calibracion.jsonl"


def record(paths: Paths, *, backend: str, agent: str, confianza: int, acierto: bool, fuente: str) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "backend": backend, "agent": agent,
           "confianza": int(confianza), "acierto": bool(acierto), "fuente": fuente}
    with FileLock(str(paths.state_dir / "calibracion.lock"), timeout=10):
        with open(_file(paths), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def table(paths: Paths) -> dict[str, list[dict]]:
    try:
        rows = [json.loads(x) for x in _file(paths).read_text(encoding="utf-8").splitlines() if x.strip()]
    except FileNotFoundError:
        return {}
    out: dict[str, list[dict]] = {}
    for backend in sorted({r["backend"] for r in rows}):
        bins = []
        for lo, hi, label in RANGOS:
            sel = [r for r in rows if r["backend"] == backend and lo <= r["confianza"] <= hi]
            bins.append({"rango": label, "n": len(sel),
                         "acierto": round(sum(r["acierto"] for r in sel) / len(sel), 3) if sel else None})
        out[backend] = bins
    return out
