"""Reglas puras de selección, review y barreras para mergear."""
from __future__ import annotations

import re

ORCHESTRATOR = "orquestador"
JUDGE = "claude-juez"
JUDGE_CANDIDATES = (JUDGE, "codex-abogado-del-diablo", "gemini-bibliotecario")
CONFIDENCE_ESCALATION = 75  # umbral de escalado, no peso de voto (arXiv 2505.19184, 2306.13063)
REVIEW_PREFERENCE = ("codex-abogado-del-diablo", "gemini-bibliotecario", "claude-centinela")


def elegir_revisor(manifest: dict, writer: str) -> str | None:
    agents = manifest.get("agents") or {}
    family = (agents.get(writer) or {}).get("backend")
    ok = [n for n, e in agents.items() if e.get("mode") == "read-only" and e.get("backend") != family]
    for name in REVIEW_PREFERENCE:
        if name in ok:
            return name
    return ok[0] if ok else None


def prompt_review(project_name: str, project_dir: str, diff: str, evidencia: str) -> str:
    return (f"Review automático en el proyecto «{project_name}» ({project_dir}). Un agente dejó estos "
            f"cambios (no importa quién: juzgá solo el código):\n\n```diff\n{diff}\n```\n\n{evidencia}\n\n"
            f"Revisalos: bugs, casos borde, tests que faltan, riesgos. Hallazgos por severidad con "
            f"archivo:línea. Pasar los tests no prueba que esté bien. Solo podés RECHAZAR con un hallazgo "
            f"concreto (archivo:línea y caso que falla); lo demás va como APROBAR CON CAMBIOS. Terminá con "
            f"una línea exacta `Veredicto: APROBAR`, `Veredicto: APROBAR CON CAMBIOS` o `Veredicto: "
            f"RECHAZAR`, y después RESULTADO y CONFIANZA.")


def parsear_veredicto(texto: str | None) -> str | None:
    # "APROBAR" a secas solo si no sigue "CON ...": "APROBAR  CON CAMBIOS" o "APROBAR con reservas" no habilitan el merge.
    m = re.search(r"Veredicto:\s*(APROBAR(?:\s+CON\s+CAMBIOS)?|RECHAZAR)\b(?!\s+CON\b)", texto or "", re.I)
    return " ".join(m.group(1).upper().split()) if m else None


def razones_bloqueo(registro: dict) -> list[str]:
    razones = []
    branch = registro.get("branch", "")
    tests = registro.get("tests")
    if tests and not tests["ok"]:
        razones.append(f"los tests del proyecto FALLARON en {branch} (`{tests.get('cmd', '')}`). "
                       f"Si querés mergear igual, confirmalo explícitamente.")
    conf = registro.get("confianza_autor")
    if conf is not None and conf < CONFIDENCE_ESCALATION:
        razones.append(f"el autor declaró confianza {conf} (< {CONFIDENCE_ESCALATION}): revisá el diff o pedí "
                       f"otra opinión antes de mergear. Si querés mergear igual, confirmalo explícitamente.")
    verdict = (registro.get("review") or {}).get("veredicto")
    if verdict != "APROBAR":
        razones.append(f"la review de {branch} no está aprobada (veredicto: {verdict or 'sin review'}). "
                       f"Si querés mergear igual, confirmalo explícitamente.")
    return razones
