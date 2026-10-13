#!/usr/bin/env python3
"""¿Algún agente de la eval tocó los tests ocultos? Revisa los logs de sesión de Codex (~/.codex/sessions)
de las corridas cuyo directorio de trabajo es un sandbox de la eval y busca cualquier mención de ellos."""
import json
import sys
from pathlib import Path

RAIZ = Path.home() / ".codex" / "sessions"
MARCAS = ("test_oculto", "eval/tareas")


def main() -> int:
    sesiones, sospechosas = 0, []
    for f in sorted(RAIZ.rglob("*.jsonl")):
        with f.open(encoding="utf-8", errors="replace") as fh:
            primera = fh.readline()
            try:
                cwd = json.loads(primera).get("payload", {}).get("cwd", "")
            except ValueError:
                continue
            if "orquesta-eval-" not in cwd:
                continue
            sesiones += 1
            for n, linea in enumerate(fh, 2):
                if any(m in linea for m in MARCAS):
                    sospechosas.append(f"{f.name}:{n}")
                    break
    print(json.dumps({"sesiones_de_la_eval": sesiones, "con_menciones": sospechosas}, indent=2))
    return 1 if sospechosas else 0


if __name__ == "__main__":
    sys.exit(main())
