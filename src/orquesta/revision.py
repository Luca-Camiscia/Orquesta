"""Reglas puras de selección, review y barreras para mergear."""
from __future__ import annotations

import re

ORCHESTRATOR = "orquestador"
JUDGE = "claude-juez"
JUDGE_CANDIDATES = (JUDGE, "codex-abogado-del-diablo", "gemini-bibliotecario")
CONFIDENCE_ESCALATION = 75  # umbral de escalado, no peso de voto (arXiv 2505.19184, 2306.13063)
REVIEW_PREFERENCE = ("codex-abogado-del-diablo", "gemini-bibliotecario", "claude-centinela")


def validar_revisor(manifest: dict, writer: str, nombre: str) -> str | None:
    """Valida un revisor propuesto para el autor identificado por su nombre, sin I/O."""
    agents = manifest.get("agents") or {}
    if nombre not in agents:
        return f"el revisor {nombre} no existe en el manifiesto"
    candidato = agents[nombre]
    if candidato.get("mode") != "read-only":
        return f"el revisor {nombre} debe ser read-only"
    if candidato.get("backend") == (agents.get(writer) or {}).get("backend"):
        return f"el revisor {nombre} debe ser de otra familia que el autor {writer}"
    return None


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


def tests_debilitados(diff: str) -> list[str]:
    """Busca borrados y desactivaciones en tests, con heurísticas conservadoras de diff."""
    from collections import Counter
    from pathlib import PurePosixPath

    test_def = re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(")
    assertion = re.compile(r"^\s*(?:assert\b|self\.assert\w+\(|(?:with\s+)?pytest\.raises\()")
    skip = re.compile(r"pytest\.skip\s*\(|pytest\.mark\.skip(?:if)?\b|unittest\.skip\b|"
                      r"\.skipTest\s*\(|@pytest\.mark\.xfail\b|\bskipif\s*=")
    trivial = re.compile(r"assert\s*\(?\s*(?:(?:True|1)|(\w+)\s*(?:==|is)\s*\1|.*\bor\s+True)\s*\)?\s*(?:,.*)?")
    # un assert vacío no reemplaza a uno real: True, 1, «x == x», «x is x» y «… or True»
    config_pytest = {"conftest.py", "pytest.ini", "setup.cfg", "pyproject.toml", "tox.ini"}
    config_cambio = re.compile(r"collect_ignore|pytest_collection_modifyitems|pytest_ignore_collect|pytest_runtest_setup|addopts|--deselect|"
                              r"--ignore|\s-k\s|python_files|python_classes|python_functions|testpaths|norecursedirs")
    quotes = re.compile(r"'''|\"\"\"|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|#.*")
    delimiters = {"-": None, "+": None}
    current_test = {"-": None, "+": None}

    def codigo(line: str, side: str, changed: bool) -> str:
        # Solo confiamos en aperturas presentes en líneas modificadas. Una comilla de
        # contexto podría cerrar un docstring empezado fuera del hunk: no debe ocultar código.
        out, pos = "", 0
        while pos < len(line):
            delimiter = delimiters[side]
            if delimiter is not None:
                end = line.find(delimiter, pos)
                if end < 0:
                    break
                delimiters[side] = None
                pos = end + 3
                continue
            match = quotes.search(line, pos)
            if match is None:
                out += line[pos:]
                break
            out += line[pos:match.start()]
            token = match.group()
            if token.startswith("#"):
                break
            if token in ('"""', "'''") and changed:
                delimiters[side] = token
            out += " "
            pos = match.end()
        return out

    def es_test(path: str) -> bool:
        p = PurePosixPath(path)
        return p.suffix == ".py" and (p.name.startswith("test_") or p.name.endswith("_test.py")
                                      or bool({"test", "tests"}.intersection(p.parts[:-1])))

    paths = {"-": "", "+": ""}
    definitions = {"-": [], "+": []}
    asserts = {"-": [], "+": []}
    findings = {}
    block = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            paths = {"-": "", "+": ""}
            in_hunk = False
        elif not in_hunk and line.startswith(("--- ", "+++ ")):
            side = line[0]
            path = line[4:].split("\t", 1)[0]
            paths[side] = path[2:] if path.startswith(("a/", "b/")) else path
            if path.startswith('"'):
                # Git escapó el nombre (no debería, con core.quotePath=false): no se puede saber si es un test.
                findings[(path, "ruta que Git entrecomilló y no se pudo analizar")] = None
        elif line.startswith("Binary files ") and line.endswith(" differ"):
            # Si Git no mostró el contenido de un archivo de tests no se puede comprobar: falla cerrada.
            nombre = line[len("Binary files "):-len(" differ")].split(" and ")[-1]
            nombre = nombre[2:] if nombre.startswith("b/") else nombre
            if es_test(nombre):
                findings[(nombre, "cambio en un test que Git no muestra como texto")] = None
        elif line.startswith("@@ "):
            in_hunk = True
            block += 1
            delimiters = {"-": None, "+": None}
            current_test = {"-": None, "+": None}
        elif in_hunk and line[:1] in ("-", "+", " "):
            sign = line[0]
            if sign == " ":
                block += 1
            for side in ("-", "+") if sign == " " else (sign,):
                path = paths[side]
                if sign == "+" and PurePosixPath(path).name in config_pytest and config_cambio.search(line[1:]):
                    findings[(path, "configuración de pytest cambiada")] = None
                if not es_test(path):
                    continue
                code = codigo(line[1:], side, sign != " ")
                definition = test_def.match(code)
                if definition:
                    current_test[side] = definition.group(1)
                elif re.match(r"^\s*(?:async\s+)?def\s+", code):
                    current_test[side] = None
                if sign == " ":
                    continue
                if definition:
                    definitions[side].append((path, definition.group(1)))
                if assertion.match(code) and not (side == "+" and trivial.fullmatch(code.strip())):
                    asserts[side].append((path, block, current_test[side], code.strip()))
                if side == "+" and skip.search(code):
                    findings[(path, "skip/xfail agregado")] = None

    added_tests = Counter(name for _, name in definitions["+"])
    moved_tests = {name for _, name in definitions["-"] if added_tests[name]}
    for path, name in definitions["-"]:
        if added_tests[name]:
            added_tests[name] -= 1
        else:
            findings[(path, "test eliminado")] = None
    available_asserts = list(asserts["+"])
    for path, block, name, code in asserts["-"]:
        replacement = next((i for i, (p, b, n, c) in enumerate(available_asserts)
                            if (p == path and b == block) or
                            (p != path and name in moved_tests and n == name and c == code)), None)
        if replacement is not None:
            # Cada assert nuevo justifica como máximo un borrado, incluso en traslados.
            available_asserts.pop(replacement)
        else:
            findings[(path, "assert eliminado")] = None
    return [f"{path}: {kind}" for path, kind in findings]


def razones_bloqueo(registro: dict) -> list[str]:
    """Detecta bloqueadores para merge: tests fallidos, confianza baja, veredicto no-APROBAR.
    
    Entrada: dict con keys branch, tests (dict|None con ok/cmd), confianza_autor (0-100), review.veredicto
    """
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
    if registro.get("diff_error"):
        razones.append(f"no pude calcular el diff de {branch} para revisar que no debilite los tests. "
                       f"Si querés mergear igual, confirmalo explícitamente.")
    if "diff" in registro:
        debilitados = tests_debilitados(registro["diff"])
        if debilitados:
            razones.append(f"el diff de {branch} debilita los tests: {'; '.join(debilitados)}. "
                           f"Si querés mergear igual, confirmalo explícitamente.")
    return razones
