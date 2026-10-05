"""Recursos Humanos: evidencia local y reglas deterministas, sin red ni LLMs.

Las tasas se expresan en puntos porcentuales (0-100). Las funciones de decisión
son puras; evaluar permite inyectar los datos o cargarlos mediante Paths.
"""
from __future__ import annotations

import json
import re
import unicodedata
from itertools import combinations
from statistics import mean, median

from orquesta import usage
from orquesta.config import load_registry
from orquesta.paths import Paths
from orquesta.schema import Agent, Registry

STOPWORDS = set("a al algo ante antes bajo con contra cual cuando de del desde donde durante e el ella "
                "ellos en entre era es esa ese eso esta este estos hay hasta la las lo los mas mi muy "
                "no o otro para pero por porque que se si sin sobre su sus tiene todo tu un una unos "
                "unas y ya hacer necesita tarea".split())
CATEGORIAS = ("Implementación", "Revisión/Calidad", "Investigación/Lectura", "Diseño/Decisión",
              "Diagnóstico", "Operaciones", "Sin categoría")


def _normalizar(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", texto.lower())
                   if not unicodedata.combining(c))


def _palabras(texto: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _normalizar(texto))) - STOPWORDS


def _resumen(a: Agent) -> dict:
    return {"name": a.name, "display_name": a.label, "backend": a.backend, "enabled": a.enabled}


def _categoria(a: Agent) -> str:
    texto = _normalizar(f"{a.name} {a.role}")
    reglas = (
        ("Operaciones", r"that[-_ ]it[-_ ]guy|manten|operacion|ecosistema|devops"),
        ("Revisión/Calidad", r"abogado|centinela|tester|\bux\b|review|revisi|calidad|\bqa\b"),
        ("Investigación/Lectura", r"bibliotecario|investig|lectura|lector|\blee\b|documentacion"),
        ("Diseño/Decisión", r"arquitect|juez|diseno|decision"),
        ("Diagnóstico", r"detective|perfilador|diagnost|profil|debug"),
    )
    for categoria, patron in reglas:
        if re.search(patron, texto):
            return categoria
    if a.mode == "write" and a.effective_isolation == "worktree":
        return "Implementación"
    return "Sin categoría"


def _jsonl(path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _conversaciones(paths: Paths) -> list[dict]:
    # Lectura directa: no crear Store ni archivos/directorios para evaluar.
    out = []
    for path in sorted((paths.home / "conversaciones" / "conv").glob("*.json")):
        try:
            conv = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(conv, dict):
            out.append(conv)
    return out


def _reviews(conversaciones: list[dict]) -> list[tuple[str, str]]:
    """Reviews recibidas por el autor del worktree, sin duplicar su mensaje.

    Para reviews sin worktree se usa meta.reviewed_agent, o el autor del mensaje
    si no hay destinatario explícito. APROBAR CON CAMBIOS cuenta como aprobada.
    """
    out = []
    for conv in conversaciones:
        branches = {}
        contabilizadas = set()
        for wt in conv.get("worktrees", []):
            branches[wt.get("branch")] = wt.get("agent")
            review = wt.get("review") or {}
            if review.get("veredicto") and wt.get("agent"):
                out.append((wt["agent"], review["veredicto"].upper()))
                if wt.get("branch"):
                    contabilizadas.add(wt["branch"])
        for msg in conv.get("messages", []):
            meta = msg.get("meta") or {}
            if meta.get("branch") in contabilizadas:
                continue
            verdict = meta.get("veredicto")
            if not verdict and meta.get("rol") == "review":
                match = re.search(r"Veredicto:\s*(APROBAR CON CAMBIOS|APROBAR|RECHAZAR)",
                                  msg.get("text") or "", re.I)
                verdict = match.group(1) if match else None
            who = meta.get("reviewed_agent") or branches.get(meta.get("branch")) or msg.get("author")
            if verdict and who:
                out.append((who, verdict.upper()))
    return out


def evaluar(paths: Paths | None, *, registry: Registry | None = None,
            usos: list[dict] | None = None, conversaciones: list[dict] | None = None,
            calibracion: list[dict] | None = None) -> list[dict]:
    """Evalúa agentes registrados y agentes históricos presentes en los datos.

    Nota (0-100): éxito si no hay reviews; si las hay, 0.8 * éxito +
    0.2 * porcentaje de reviews aprobadas. Restar min(20, max(0, confianza
    media - acierto real - 20)) por sobreconfianza y 10 si la mediana de
    latencia supera 120 s (señal «lento»). Limitar a [0,100], redondear a
    un decimal. Con menos de 3 llamadas: nota null, sin datos suficientes.
    Tokens promedio usa solo llamadas con al menos un conteo conocido;
    latencia y calibración ausentes son null, nunca ceros inventados.
    """
    if registry is None:
        registry = load_registry(paths)
    if usos is None:
        usos = usage.load(paths)
    if conversaciones is None:
        conversaciones = _conversaciones(paths)
    if calibracion is None:
        calibracion = _jsonl(paths.state_dir / "calibracion.jsonl")
    reviews = _reviews(conversaciones)
    agents = {a.name: a for a in registry.agents}
    names = set(agents) | {r["agent"] for r in usos + calibracion if r.get("agent")}
    names |= {name for name, _ in reviews}
    out = []
    for name in sorted(names):
        a = agents.get(name)
        rows = [r for r in usos if r.get("agent") == name]
        n = len(rows)
        exito = 100 * sum(r.get("status") == "OK" for r in rows) / n if n else None
        latencias = [r["latency_s"] for r in rows if isinstance(r.get("latency_s"), (int, float))]
        latencia = median(latencias) if latencias else None
        tokens = [(r.get("tokens_in") or 0) + (r.get("tokens_out") or 0) for r in rows
                  if r.get("tokens_in") is not None or r.get("tokens_out") is not None]
        revs = [v for who, v in reviews if who == name]
        aprobadas = sum(v in ("APROBAR", "APROBAR CON CAMBIOS") for v in revs)
        rechazadas = revs.count("RECHAZAR")
        cal = [r for r in calibracion if r.get("agent") == name
               and isinstance(r.get("confianza"), (int, float)) and isinstance(r.get("acierto"), bool)]
        confianza = mean(r["confianza"] for r in cal) if cal else None
        acierto = 100 * mean(r["acierto"] for r in cal) if cal else None
        gap = confianza - acierto if cal else 0
        lento = latencia is not None and latencia > 120
        senales = []
        if not n:
            senales.append("nunca usado")
        if n and 100 - exito > 30:
            senales.append("falla mucho")
        if gap > 20:
            senales.append("sobreconfiado")
        if lento:
            senales.append("lento")
        nota = None
        if n >= 3:
            calidad = (0.8 * exito + 0.2 * 100 * aprobadas / (aprobadas + rechazadas)
                       if aprobadas + rechazadas else exito)
            nota = round(max(0, min(100, calidad - min(20, max(0, gap - 20)) - 10 * lento)), 1)
        out.append({**(_resumen(a) if a else {"name": name, "display_name": name,
                    "backend": next((r.get("backend") for r in rows), None), "enabled": False}),
                    "llamadas": n, "tasa_exito": round(exito, 1) if n else None,
                    "timeouts": sum(r.get("status") == "TIMEOUT" for r in rows),
                    "rate_limits": sum(r.get("status") == "RATE_LIMIT" for r in rows),
                    "latencia_mediana_s": round(latencia, 1) if latencia is not None else None,
                    "tokens_promedio": round(mean(tokens), 1) if tokens else None,
                    "reviews_aprobadas": aprobadas, "reviews_rechazadas": rechazadas,
                    "calibracion": {"muestras": len(cal),
                        "confianza_media": round(confianza, 1) if cal else None,
                        "acierto_real": round(acierto, 1) if cal else None},
                    "nota": nota, "estado": "sin datos suficientes" if n < 3 else "evaluado",
                    "senales": senales})
    return out


def catalogar(registry: Registry, *, umbral_solapamiento: float = 0.65) -> dict:
    """Categorías por nombre/rol/modo y Jaccard de route_when sin stopwords.

    Los deshabilitados aparecen en el catálogo, pero no cubren huecos ni generan
    solapamientos activos. Dos conjuntos vacíos nunca se consideran similares.
    """
    categorias = {c: [] for c in CATEGORIAS}
    for a in registry.agents:
        categorias[_categoria(a)].append(_resumen(a))
    huecos = [f"{c}: sin agentes habilitados" for c in CATEGORIAS[:-1]
              if not any(a["enabled"] for a in categorias[c])]
    enabled = registry.enabled_agents()
    revisores = [a for a in enabled if _categoria(a) == "Revisión/Calidad" and a.mode == "read-only"]
    for familia in sorted({a.backend for a in enabled}):
        if not any(a.backend != familia for a in revisores):
            huecos.append(f"{familia}: sin revisor habilitado de otra familia")
    solapamientos = []
    for a, b in combinations(enabled, 2):
        x, y = _palabras(" ".join(a.route_when)), _palabras(" ".join(b.route_when))
        similitud = len(x & y) / len(x | y) if x and y else 0
        if x and y and similitud >= umbral_solapamiento:
            solapamientos.append({"agentes": [a.name, b.name], "similitud": round(similitud, 3)})
    return {"categorias": categorias, "huecos": huecos, "solapamientos": solapamientos}


def _matches(palabras: set[str], frases: list[str]) -> list[tuple[str, float]]:
    return [(frase, len(palabras & ws) / len(ws)) for frase in frases
            if (ws := _palabras(frase)) and palabras & ws]


def _modelo(a: Agent, dificultad: int, modelos: dict) -> dict:
    disponibles = modelos.get(a.backend) or []
    configured = a.wrapper_model if a.backend == "claude" else a.backend_model
    elegido = next((m for m in disponibles if m["id"] == configured), None)
    if a.backend == "claude":
        preferidos = ("haiku", "sonnet", "opus") if dificultad <= 2 else ("opus", "sonnet", "haiku")
        elegido = next((m for p in preferidos for m in disponibles if p in m["id"]), elegido)
    elif a.backend == "agy" and dificultad >= 3:
        elegido = (elegido if elegido and elegido["id"].endswith("-high") else
                   next((m for m in disponibles if m["id"].endswith("-high")), elegido))
    elegido = elegido or (disponibles[0] if disponibles else None)
    effort = None
    if a.backend == "codex" and elegido:
        niveles = elegido.get("reasoning_levels") or []
        orden = ["low", "medium", "high", "xhigh", "max", "ultra"]
        target = "medium" if dificultad <= 2 else "high" if dificultad == 3 else "xhigh"
        inferiores = [n for n in orden[:orden.index(target) + 1] if n in niveles]
        effort = inferiores[-1] if inferiores else next((n for n in orden if n in niveles), None)
    return {"modelo": elegido["id"] if elegido else configured, "esfuerzo": effort}


def triage(texto: str, registry: Registry, modelos: dict) -> dict:
    """Dificultad: base 1, +1 por >400 caracteres (+2 por >1500), +1 por
    2-3 archivos/módulos (+2 por 4+), +1 por riesgo (+2 con varios riesgos,
    +3 si combina migración y otro riesgo),
    +1 por diseño, +1 por tests; limitar a 5. Fix mecánico sin riesgo/diseño
    ni múltiples archivos se limita a 2. Ranking: matches de route_when/rol,
    penalización de avoid_when y preferencia por worktree en tareas de escritura.
    """
    palabras = _palabras(texto)
    normal = _normalizar(texto)
    razones = []
    dificultad = 1
    if len(texto) > 400:
        dificultad += 2 if len(texto) > 1500 else 1
        razones.append("Descripción extensa" if len(texto) <= 1500 else "Descripción muy extensa")
    archivos = set(re.findall(r"\b[\w./-]+\.(?:py|js|ts|tsx|jsx|html|css|sql|yaml|yml|json|md)\b", normal))
    modulos = re.findall(r"\b(?:modulos?|archivos?)\s+([\w.-]+)", normal)
    cantidades = [int(n) for n in re.findall(r"\b(\d+)\s+(?:archivos|modulos)\b", normal)]
    alcance = max(len(archivos | set(modulos)), max(cantidades, default=0))
    if alcance >= 2:
        dificultad += 2 if alcance >= 4 else 1
        razones.append(f"Alcance de {alcance} archivos o módulos")
    riesgos = palabras & {"seguridad", "auth", "autenticacion", "autorizacion", "migracion", "migrar",
                          "concurrencia", "datos", "security", "authentication", "migration"}
    if riesgos:
        migracion_riesgosa = bool(riesgos & {"migracion", "migrar", "migration"}) and len(riesgos) >= 2
        dificultad += 3 if migracion_riesgosa else 2 if len(riesgos) >= 2 else 1
        razones.append("Riesgos: " + ", ".join(sorted(riesgos)))
    diseno = bool(palabras & {"disenar", "diseno", "arquitectura", "decidir", "planificar", "design"})
    if diseno:
        dificultad += 1
        razones.append("Requiere diseño o decisiones de arquitectura")
    if palabras & {"tests", "test", "pruebas", "cobertura"}:
        dificultad += 1
        razones.append("Pide tests o validación")
    mecanico = bool(palabras & {"typo", "errata", "mecanico", "mecanicos", "imports", "renombrar"})
    if mecanico and not riesgos and not diseno and alcance <= 1:
        dificultad = min(dificultad, 2)
        razones.append("Fix mecánico y acotado")
    if not razones:
        razones.append("Tarea breve con alcance y riesgo acotados")
    dificultad = min(dificultad, 5)
    escritura = bool(palabras & {"implementar", "implementa", "implementacion", "agregar", "agrega", "crear", "escribir",
                                "corregir", "corregi", "fix", "typo", "errata", "cambiar", "modificar",
                                "migrar", "migracion", "refactor", "refactorizar", "renombrar"})
    produccion = bool(palabras & {"implementar", "implementa", "implementacion", "migrar", "migracion",
                                 "refactor", "refactorizar", "cambiar", "modificar", "codigo"})
    ranked = []
    for a in registry.enabled_agents():
        matches = _matches(palabras, a.route_when)
        avoid = _matches(palabras, a.avoid_when)
        role = _palabras(a.role)
        score = 40 * sum(s for _, s in matches) + 20 * len(palabras & role) / max(1, len(role))
        score -= 60 * sum(s for _, s in avoid)
        motivos = [f"Coincide: {f}" for f, _ in matches]
        if palabras & role:
            motivos.append("Su rol coincide con la tarea")
        if escritura:
            if a.mode == "write" and a.effective_isolation == "worktree":
                if produccion and _categoria(a) == "Revisión/Calidad":
                    score += 10
                    motivos.append("Puede aportar tests/calidad; la implementación requiere otro agente")
                else:
                    score += 45
                    motivos.append("Puede implementar en un worktree aislado")
            elif a.mode == "read-only":
                score -= 15
        if mecanico and "forj" in a.name:
            score += 10
            motivos.append("Especialidad en fixes acotados")
        motivos += [f"Evitar: {f}" for f, _ in avoid]
        ranked.append({**_resumen(a), "puntaje": round(score, 2),
                       "motivos": motivos or ["Agente habilitado; sin coincidencia específica"],
                       **_modelo(a, dificultad, modelos)})
    ranked.sort(key=lambda a: (-a["puntaje"], a["name"]))
    revisor = None
    avisos = []
    if escritura and dificultad >= 3 and ranked:
        familia = ranked[0]["backend"]
        candidatos = [a for a in ranked if a["backend"] != familia
                      and _categoria(registry.agent(a["name"])) == "Revisión/Calidad"
                      and registry.agent(a["name"]).mode == "read-only"]
        if candidatos:
            revisor = {**candidatos[0], "motivos": ["Review de otra familia para escritura no trivial"]}
        else:
            avisos.append("No hay revisor habilitado de otra familia para esta tarea")
    return {"dificultad": dificultad, "razones": razones, "recomendados": ranked[:3],
            "revisor": revisor, "avisos": avisos}


def sugerir_participantes(conv: dict, registry: Registry) -> dict:
    """Match de route_when en los últimos seis mensajes; solo agentes ausentes.

    Considera orquestador como familia Claude; los nombres desconocidos no
    permiten afirmar que todos los participantes sean de la misma familia.
    """
    presentes = set(conv.get("participants", []))
    palabras = _palabras(" ".join(m.get("text") or "" for m in conv.get("messages", [])[-6:]))
    sugerencias = []
    for a in registry.enabled_agents():
        if a.name in presentes:
            continue
        matches = _matches(palabras, a.route_when)
        if matches:
            sugerencias.append({**_resumen(a), "motivo": max(matches, key=lambda m: m[1])[0]})
    familias = {"claude" if p == "orquestador" else
                registry.agent(p).backend if registry.agent(p) else None for p in presentes}
    avisos = ([f"Todos los participantes son de la misma familia ({next(iter(familias))})"]
              if len(familias) == 1 and None not in familias else [])
    return {"sugerencias": sugerencias, "avisos": avisos}
