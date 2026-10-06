"""RR. HH. usa evidencia local y heurísticas reproducibles, sin ejecutar backends."""
import json

import pytest

from orquesta import rrhh, usage
from orquesta.schema import Agent, Registry


def registry(*agents):
    return Registry(orchestrator={"default_mood": "simple", "moods": {"simple": "Simple"}},
                    agents=list(agents))


@pytest.fixture
def plantel():
    return registry(
        Agent(name="codex-forjador", backend="codex", mode="write", role="Implementación y fixes mecánicos",
              route_when=["fix mecánico, typo, cambiar código", "migración de auth y datos"],
              backend_model="gpt-local"),
        Agent(name="claude-centinela", backend="claude", role="Revisión de seguridad y calidad",
              route_when=["revisión de seguridad auth migración"], wrapper_model="sonnet"),
        Agent(name="lector-nuevo", backend="agy", role="Investigador de documentación",
              route_when=["leer documentación y repo"], avoid_when=["typo cambiar código"]),
        Agent(name="apagado", backend="codex", enabled=False, role="fix typo",
              route_when=["fix typo"]))


MODELOS = {"codex": [{"id": "gpt-local", "reasoning_levels": ["medium", "high"]}],
           "claude": [{"id": "haiku"}, {"id": "sonnet"}, {"id": "opus"}],
           "agy": [{"id": "gemini-low"}, {"id": "gemini-high"}]}


def test_evaluar_datos_inyectados_y_reviews_recibidas(plantel):
    rows = [{"agent": "codex-forjador", "status": status, "latency_s": latency,
             "tokens_in": 100, "tokens_out": 20}
            for status, latency in [("OK", 10), ("OK", 20), ("TIMEOUT", 300), ("RATE_LIMIT", 400)]]
    conv = {"messages": [{"author": "claude-centinela", "text": "Veredicto: RECHAZAR",
                          "meta": {"rol": "review", "branch": "b"}}],
            "worktrees": [{"agent": "codex-forjador", "branch": "b",
                           "review": {"by": "claude-centinela", "veredicto": "RECHAZAR"}}]}
    result = rrhh.evaluar(None, registry=plantel, usos=rows, conversaciones=[conv],
                          calibracion=[{"agent": "codex-forjador", "confianza": 95, "acierto": False}])
    a = next(a for a in result if a["name"] == "codex-forjador")
    assert a["llamadas"] == 4 and a["tasa_exito"] == 50
    assert a["timeouts"] == 1 and a["rate_limits"] == 1
    assert a["latencia_mediana_s"] == 160 and a["tokens_promedio"] == 120
    assert a["reviews_aprobadas"] == 0 and a["reviews_rechazadas"] == 1
    assert a["calibracion"] == {"muestras": 1, "confianza_media": 95, "acierto_real": 0}
    assert a["nota"] == 10  # 80% * 50 + 20% * 0 - 20 por sobreconfianza - 10 lento
    assert set(a["senales"]) == {"falla mucho", "sobreconfiado", "lento"}


def test_evaluar_pocas_llamadas_y_nunca_usado(plantel):
    result = rrhh.evaluar(None, registry=plantel, usos=[{"agent": "codex-forjador", "status": "OK"}],
                          conversaciones=[], calibracion=[])
    assert all(a["nota"] is None and a["estado"] == "sin datos suficientes" for a in result)
    assert "nunca usado" in next(a for a in result if a["name"] == "lector-nuevo")["senales"]


@pytest.mark.parametrize("llamadas", [0, 1, 2])
def test_nota_requiere_tres_llamadas(plantel, llamadas):
    usos = [{"agent": "codex-forjador", "status": "OK"}] * llamadas
    a = next(a for a in rrhh.evaluar(None, registry=plantel, usos=usos,
             conversaciones=[], calibracion=[]) if a["name"] == "codex-forjador")
    assert a["nota"] is None


def test_senales_limites_y_metricas_desconocidas(plantel):
    rows = [{"agent": "codex-forjador", "status": s, "latency_s": 120}
            for s in ["OK"] * 7 + ["ERROR"] * 3]
    a = next(a for a in rrhh.evaluar(None, registry=plantel, usos=rows, conversaciones=[],
             calibracion=[{"agent": "codex-forjador", "confianza": 20, "acierto": False}])
             if a["name"] == "codex-forjador")
    assert a["nota"] == 70 and a["senales"] == []
    assert a["tokens_promedio"] is None


def test_evaluar_lee_archivos_reales_y_tolera_datos_ausentes(env):
    assert all(a["nota"] is None for a in rrhh.evaluar(env))
    for _ in range(3):
        usage.record(env, backend="codex", agent="codex-forja", status="OK", latency_s=3)
    (env.state_dir / "calibracion.jsonl").write_text('basura\n' + json.dumps(
        {"agent": "codex-forja", "confianza": 80, "acierto": True}) + '\n')
    a = next(a for a in rrhh.evaluar(env) if a["name"] == "codex-forja")
    assert a["nota"] == 100 and a["calibracion"]["acierto_real"] == 100


def test_catalogar_reglas_para_agentes_desconocidos_y_huecos(plantel):
    plantel.agents += [Agent(name="nuevo", backend="agy", role="Sin especialidad"),
                       Agent(name="qa-nuevo", backend="agy", mode="write", role="Tester de calidad")]
    c = rrhh.catalogar(plantel)
    assert any(a["name"] == "lector-nuevo" for a in c["categorias"]["Investigación/Lectura"])
    assert any(a["name"] == "nuevo" for a in c["categorias"]["Sin categoría"])
    assert any(a["name"] == "qa-nuevo" for a in c["categorias"]["Revisión/Calidad"])
    assert any("Diagnóstico" in h for h in c["huecos"])
    assert not any("codex" in h and "revisor" in h for h in c["huecos"])
    only = rrhh.catalogar(registry(plantel.agents[0]))
    assert any("codex" in h and "revisor" in h for h in only["huecos"])


def test_solapamientos_normalizan_acentos_y_stopwords():
    reg = registry(Agent(name="a", backend="codex", role="x", route_when=["Hay que revisar código y tests"]),
                   Agent(name="b", backend="agy", role="x", route_when=["revisar el codigo con tests"]),
                   Agent(name="c", backend="agy", role="x", route_when=["leer audio"]),
                   Agent(name="d", backend="agy", role="x", route_when=[]))
    c = rrhh.catalogar(reg, umbral_solapamiento=0.8)
    assert c["solapamientos"] == [{"agentes": ["a", "b"], "similitud": 1.0}]


def test_triage_typo_recomienda_forjador_y_penaliza_avoid(plantel):
    t = rrhh.triage("Corregir un typo en README.md", plantel, MODELOS)
    assert t["dificultad"] <= 2
    assert t["recomendados"][0]["name"] == "codex-forjador"
    assert t["recomendados"][0]["esfuerzo"] == "medium"
    assert t["recomendados"][0]["modelo"] == "gpt-local"
    assert all(a["name"] != "apagado" for a in t["recomendados"])
    assert t["revisor"] is None


def test_triage_migracion_auth_alta_y_review_cruzada(plantel):
    t = rrhh.triage("Implementar migración de auth y datos, seguridad y concurrencia en auth.py, "
                    "users.py y db.py; diseñar arquitectura y agregar tests", plantel, MODELOS)
    assert t["dificultad"] >= 4 and t["razones"]
    assert t["recomendados"][0]["name"] == "codex-forjador"
    assert t["recomendados"][0]["esfuerzo"] == "high"  # no soporta xhigh
    assert t["revisor"]["backend"] == "claude" and t["revisor"]["modelo"] == "opus"
    assert len(t["recomendados"]) <= 3


def test_triage_modelos_agy_y_catalogo_vacio(plantel):
    reg = registry(plantel.agents[2])
    t = rrhh.triage("Investigar migración de seguridad auth y datos", reg, MODELOS)
    assert t["recomendados"][0]["modelo"] == "gemini-high"
    t = rrhh.triage("fix typo", plantel, {})
    assert t["recomendados"][0]["modelo"] == "gpt-local"
    assert t["recomendados"][0]["esfuerzo"] is None
    assert rrhh.triage("", registry(), {})["recomendados"] == []


def test_triage_avoid_puede_cambiar_el_recomendado():
    reg = registry(Agent(name="a", backend="codex", role="Investigación", route_when=["leer logs"],
                         avoid_when=["seguridad"]),
                   Agent(name="b", backend="agy", role="Investigación", route_when=["leer logs"]))
    assert rrhh.triage("leer logs de seguridad", reg, MODELOS)["recomendados"][0]["name"] == "b"


def test_modelo_claude_baja_y_codex_xhigh_si_soportado(plantel):
    reg = registry(plantel.agents[1])
    assert rrhh.triage("revisión breve", reg, MODELOS)["recomendados"][0]["modelo"] == "haiku"
    modelos = {**MODELOS, "codex": [{"id": "gpt-local", "reasoning_levels": ["medium", "xhigh"]}]}
    t = rrhh.triage("Implementar migración de auth, datos y seguridad", plantel, modelos)
    assert t["recomendados"][0]["esfuerzo"] == "xhigh"


def test_triage_sin_revisor_cruzado_informa_el_hueco(plantel):
    t = rrhh.triage("Implementar migración de auth y datos", registry(plantel.agents[0]), MODELOS)
    assert t["revisor"] is None and t["avisos"]


@pytest.mark.parametrize("texto", [
    "Verificar si el login ya funciona correctamente",
    "Puede que ya esté arreglado, chequealo por favor",
    "Validar y confirmar que el problema aún existe antes de implementar",
    "checar si el issue todavía pasa",
    "Verificar si ya funciona",
    "Reproducí si el error todavía pasa",
    "Verificalo, quizá ya está resuelto",
    "Comprobá: quizá funciona",
    "No estamos seguros: verificar el login",
    "Check: puede que funcione",
    "Comprobá: quizás funciona",
    "Confirmá: todavía pasa",
    "Validación: acaso funciona",
    "Reproduzco: podría funcionar",
    "No estoy segura: verificar el login",
])
def test_triage_abstencion_verificar_primero(plantel, texto):
    t = rrhh.triage(texto, plantel, MODELOS)
    assert t["abstencion"] == "Verificar primero y no escribir código si el problema no se reproduce"
    assert "Sugerencia: verificar primero si el problema aún existe antes de implementar cambios" in t["avisos"]


@pytest.mark.parametrize("texto", [
    "Implementar login con autenticación OAuth",
    "Corregir typo en README.md",
    "Agregar tests para la nueva función de búsqueda",
    "Arreglar el bug de autenticación",
    "Crear una new feature para dashboards",
    "Verificar login",
    "Verificar verificar verificar login",
    "Implementar login seguro que ya puede funcionar",
    "Corregir el bug del reporte: el error aparece en el login y el problema rompe la sesión",
    "Arreglar el error 500 que ya reportaron, el problema está en el parser",
    "Implementar el fix del issue 42, ya tenemos el bug reproducido",
    "Validar el formato del input del formulario",
    "Agregar tests que verifiquen el parser",
    "VERIFICÓ, verificás y comprobá el error",
    "No estoy segura: quizás ya funciona",
    "¿Estás seguro? Podría ya funcionar",
    "Validar y confirmar el problema antes de implementar",
    "Crear función para validar si un cuit es válido",
    "Implementar autenticación y verificar si el token es válido",
    "Validar datos del formulario ya que fallan las pruebas",
    "Si estás seguro de la solución, validá y confirmá los cambios",
    "Verificar si funciona",
])
def test_triage_sin_abstencion_en_pedidos_normales(plantel, texto):
    t = rrhh.triage(texto, plantel, MODELOS)
    assert "abstencion" not in t
    assert set(t) == {"dificultad", "razones", "recomendados", "revisor", "avisos"}
    assert not any("verificar primero" in a for a in t["avisos"])


def test_triage_abstencion_quita_solo_bonus_worktree_y_considera_lectura():
    reg = registry(
        Agent(name="escritor", backend="codex", mode="write", role="Implementación",
              route_when=["implementar login"]),
        Agent(name="lector", backend="agy", role="Login", route_when=["implementar login"]))
    normal = rrhh.triage("Implementar login", reg, MODELOS)
    abstencion = rrhh.triage("Verificar si ya funciona antes de implementar login", reg, MODELOS)
    assert [a["name"] for a in normal["recomendados"]] == ["escritor", "lector"]
    assert [a["puntaje"] for a in normal["recomendados"]] == [85, 45]
    assert [a["name"] for a in abstencion["recomendados"]] == ["lector", "escritor"]
    assert [a["puntaje"] for a in abstencion["recomendados"]] == [45, 40]
    assert all("Puede implementar en un worktree aislado" not in a["motivos"]
               for a in abstencion["recomendados"])


def test_triage_abstencion_conserva_avisos_existentes(plantel):
    t = rrhh.triage("Validar y confirmar si el problema aún existe antes de implementar migración de auth y datos",
                    registry(plantel.agents[0]), MODELOS)
    assert len(t["avisos"]) == 2
    assert t["avisos"][0] == "No hay revisor habilitado de otra familia para esta tarea"
    assert "verificar primero" in t["avisos"][1]


def test_migracion_auth_es_alta_aunque_descripcion_breve(plantel):
    assert rrhh.triage("Migrar auth", plantel, MODELOS)["dificultad"] >= 4


def test_implementacion_con_tests_no_se_deriva_al_tester(plantel):
    plantel.agents.append(Agent(name="codex-tester", backend="codex", mode="write",
        role="Escribe tests; nunca toca código de producción", route_when=["agregar tests"],
        avoid_when=["arreglar código de producción"]))
    t = rrhh.triage("Implementar migración de auth y datos y agregar tests", plantel, MODELOS)
    assert t["recomendados"][0]["name"] == "codex-forjador"


def test_sugerencias_ultimos_mensajes_y_familia(plantel):
    conv = {"participants": ["codex-forjador"], "messages": [
        {"author": "usuario", "text": "Necesito revisión de seguridad auth migración"}]}
    s = rrhh.sugerir_participantes(conv, plantel)
    assert [a["name"] for a in s["sugerencias"]] == ["claude-centinela"]
    assert s["sugerencias"][0]["motivo"] and s["avisos"]
    conv["participants"].append("claude-centinela")
    assert rrhh.sugerir_participantes(conv, plantel) == {"sugerencias": [], "avisos": []}
    conv["messages"] += [{"author": "usuario", "text": "Sin novedades"}] * 6
    assert rrhh.sugerir_participantes(conv, plantel)["sugerencias"] == []


def test_evaluar_reviews_de_store(env):
    from orquesta.chat import Store
    store = Store(env)
    project = store.create_project("p", str(env.home))
    # Guardar una conversación real sin ejecutar ningún agente.
    store.save({"id": "0123456789", "project_id": project["id"], "messages": [],
                "worktrees": [{"agent": "codex-forja", "branch": "b",
                               "review": {"by": "codex-critico", "veredicto": "APROBAR CON CAMBIOS"}}]})
    a = next(a for a in rrhh.evaluar(env) if a["name"] == "codex-forja")
    assert a["reviews_aprobadas"] == 1


def test_evaluar_incluye_reviews_de_tareas(env):
    from orquesta import tareas
    for n, veredicto in enumerate(("APROBAR", "RECHAZAR", "APROBAR CON CAMBIOS", None)):
        tareas.registrar("proyecto", {"repo": str(env.home), "rama": f"b{n}",
                         "agente": "codex-forja", "veredicto": veredicto}, paths=env)
    a = next(a for a in rrhh.evaluar(env) if a["name"] == "codex-forja")
    assert a["reviews_aprobadas"] == 2 and a["reviews_rechazadas"] == 1
