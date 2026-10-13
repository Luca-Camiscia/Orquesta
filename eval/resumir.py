#!/usr/bin/env python3
"""Tabla markdown a partir de los .jsonl de run_eval.py: aciertos en tests ocultos, llamadas y tiempo."""
import json
import statistics
import sys
from collections import defaultdict


def main(path: str) -> None:
    filas = [json.loads(ln) for ln in open(path, encoding="utf-8") if ln.strip()]
    por = defaultdict(list)
    for f in filas:
        por[f["condicion"]].append(f)
    print("| Condition | Hidden tests passed | Author calls | Reviewer calls | Median wall time | Errors |")
    print("|---|---|---|---|---|---|")
    for cond in sorted(por):
        fs = por[cond]
        ok = sum(1 for f in fs if f.get("ocultos_ok"))
        mediana = statistics.median(f.get("segundos", 0) for f in fs) if fs else 0
        errores = sum(1 for f in fs if "error" in f or "error_ronda_2" in f)
        print(f"| {cond} | {ok}/{len(fs)} | {sum(f.get('llamadas_autor', 0) for f in fs)} | "
              f"{sum(f.get('llamadas_revisor', 0) for f in fs)} | {mediana:.0f} s | {errores} |")
    print()
    tareas = sorted({f["tarea"] for f in filas})
    conds = sorted(por)
    print("| Task | " + " | ".join(conds) + " |")
    print("|---|" + "---|" * len(conds))
    for t in tareas:
        celdas = []
        for c in conds:
            fs = [f for f in filas if f["tarea"] == t and f["condicion"] == c]
            celdas.append(f"{sum(1 for f in fs if f.get('ocultos_ok'))}/{len(fs)}")
        print(f"| {t} | " + " | ".join(celdas) + " |")
    veredictos = defaultdict(int)
    for f in por.get("B", []):
        veredictos[f.get("veredicto_1") or "sin veredicto"] += 1
    if veredictos:
        print("\nFirst-round verdicts in B: " + ", ".join(f"{k} {v}" for k, v in sorted(veredictos.items())))


if __name__ == "__main__":
    main(sys.argv[1])
