"""RR. HH. desde terminal: JSON, validación sin escrituras y sesión interactiva."""
import io
import json
import subprocess
from datetime import datetime

import pytest
from filelock import FileLock, Timeout

from helpers import dump_yaml, load_yaml, snapshot
from orquesta import chat, cli, modelos, runner
from orquesta.generate import apply_plan, build_plan


@pytest.fixture
def rrhh_env(env, monkeypatch):
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.setattr(modelos.ModelDiscovery, "local_models", lambda self: {})
    return env


def invocar(monkeypatch, capsys, comando, entrada="", extra=()):
    monkeypatch.setattr("sys.stdin", io.StringIO(entrada))
    code = cli.main(["rrhh", comando, *extra])
    salida = capsys.readouterr()
    assert salida.err == ""
    assert len(salida.out.splitlines()) == 1
    sobre = json.loads(salida.out)
    assert set(sobre) == {"version", "comando", "estado", "generacion", "datos", "errores", "avisos"}
    assert sobre["version"] == 1 and sobre["comando"] == f"rrhh {comando}"
    return code, sobre


def test_triage_json(rrhh_env, monkeypatch, capsys):
    code, sobre = invocar(monkeypatch, capsys, "triage", '{"texto": "  Corregir fix mecánico  "}')
    assert code == 0 and sobre["estado"] == "OK"
    assert sobre["datos"]["dificultad"] <= 2
    assert sobre["datos"]["recomendados"][0]["name"] == "codex-forja"


def test_evaluar_parsea_pedido(rrhh_env, monkeypatch, capsys):
    pedido = {"usos": [{"agent": "codex-forja", "status": "OK"}] * 3,
              "conversaciones": [], "calibracion": []}
    code, sobre = invocar(monkeypatch, capsys, "evaluar", json.dumps(pedido))
    assert code == 0 and sobre["estado"] == "OK"
    agente = next(a for a in sobre["datos"]["agentes"] if a["name"] == "codex-forja")
    assert agente["llamadas"] == 3 and agente["nota"] == 100


def test_evaluar_datos_locales(rrhh_env, monkeypatch, capsys):
    code, sobre = invocar(monkeypatch, capsys, "evaluar", "{}")
    assert code == 0 and len(sobre["datos"]["agentes"]) == 4
    assert all(a["llamadas"] == 0 for a in sobre["datos"]["agentes"])


def test_catalogo_sin_stdin(rrhh_env, monkeypatch, capsys):
    code, sobre = invocar(monkeypatch, capsys, "catalogo")
    assert code == 0 and sobre["estado"] == "OK"
    assert sobre["datos"]["categorias"]["Implementación"][0]["name"] == "codex-forja"


def test_sugerir_participantes(rrhh_env, monkeypatch, capsys):
    code, sobre = invocar(monkeypatch, capsys, "sugerir",
                          extra=("--participantes", " codex-forja, codex-critico "))
    assert code == 0 and sobre["estado"] == "OK"
    assert sobre["datos"]["sugerencias"] == []
    assert sobre["datos"]["avisos"] == ["Todos los participantes son de la misma familia (codex)"]


def test_sugerir_reunion_usa_mensajes(rrhh_env, monkeypatch, capsys):
    store = chat.Store(rrhh_env)
    store.save({"id": "1234567890", "participants": ["codex-critico"],
                "messages": [{"text": "fix mecánico"}]})
    code, sobre = invocar(monkeypatch, capsys, "sugerir", extra=("--reunion", "1234567890"))
    assert code == 0 and sobre["datos"]["sugerencias"][0]["name"] == "codex-forja"


@pytest.mark.parametrize("estado,codigo", [("OK", 0), ("RATE_LIMIT", 1)])
def test_reclutador_backend_falso(rrhh_env, monkeypatch, capsys, estado, codigo):
    data = load_yaml(rrhh_env.registry)
    data["agents"].append({"name": "gemini-reclutador", "backend": "agy",
        "backend_model": "gemini-3.8-flash-high", "wrapper_model": "haiku",
        "mode": "read-only", "isolation": "none", "tools": ["Bash"],
        "role": "Recomienda equipo", "prompt": "Opinión consultiva."})
    dump_yaml(data, rrhh_env.registry)
    apply_plan(build_plan(rrhh_env), "deploy de prueba", run_tests=None)
    calls = []

    def backend(req, paths):
        calls.append(req)
        assert paths == rrhh_env and req.agent == "gemini-reclutador"
        assert '"texto": "fix mecánico"' in req.prompt
        return runner.RunResult(estado, output="Equipo: codex-forja", latency_s=1.2)

    monkeypatch.setattr(runner, "run", backend)
    code, sobre = invocar(monkeypatch, capsys, "reclutador", '{"texto": "fix mecánico"}')
    assert code == codigo and sobre["estado"] == estado and len(calls) == 1
    assert sobre["datos"]["opinion"] == ("Equipo: codex-forja" if codigo == 0 else None)


def test_reclutador_sin_config_no_consume_cuota(rrhh_env, monkeypatch, capsys):
    monkeypatch.setattr(runner, "run", lambda *a: pytest.fail("no debe gastar cuota"))
    code, sobre = invocar(monkeypatch, capsys, "reclutador", '{"texto": "fix mecánico"}')
    assert code == 1 and sobre["estado"] == "ERROR" and sobre["errores"]
    assert sobre["datos"]["estado"] == "CONFIGURACION_INVALIDA"


def test_validar_alta_informativa_sin_escribir(rrhh_env, monkeypatch, capsys, tmp_path):
    apply_plan(build_plan(rrhh_env), "deploy", run_tests=None)
    data = load_yaml(rrhh_env.registry)
    data["agents"].append({"name": "nuevo", "backend": "codex", "tools": ["Bash"],
                           "role": "Lee código", "prompt": "Leé código."})
    candidato = tmp_path / "candidato.yaml"
    dump_yaml(data, candidato)
    before = snapshot(tmp_path)
    code, sobre = invocar(monkeypatch, capsys, "validar", candidato.read_text())
    assert code == 0 and sobre["estado"] == "OK" and sobre["datos"]["errores"] == []
    assert sobre["datos"]["violaciones"] == [
        {"agente": "nuevo", "tipo": "alta", "detalle": "se agrega un agente"}]
    assert sobre["datos"]["comando_sugerido"] == "orquesta generate --confirm nuevo"
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("entrada", ["agents: [", "[]", "agents: [{name: inválido}]"])
def test_validar_invalido_errores_en_datos(rrhh_env, monkeypatch, capsys, entrada):
    code, sobre = invocar(monkeypatch, capsys, "validar", entrada)
    assert code == 1 and sobre["estado"] == "ERROR"
    assert sobre["datos"]["errores"] and sobre["errores"] == sobre["datos"]["errores"]


def test_validar_compara_backends(rrhh_env, monkeypatch, capsys, tmp_path):
    data = load_yaml(rrhh_env.backends)
    del data["backends"]["codex"]
    dump_yaml(data, rrhh_env.backends)
    before = snapshot(tmp_path)
    code, sobre = invocar(monkeypatch, capsys, "validar", rrhh_env.registry.read_text())
    assert code == 1 and any("backends.yaml" in e for e in sobre["datos"]["errores"])
    assert snapshot(tmp_path) == before


def test_memoria_vacia_no_crea_estado(rrhh_env, monkeypatch, capsys):
    code, sobre = invocar(monkeypatch, capsys, "memoria")
    assert code == 0 and sobre["datos"] == {"texto": ""}
    assert not rrhh_env.state_dir.exists()


def test_nota_memoria_ida_y_vuelta(rrhh_env, monkeypatch, capsys):
    for texto in ("Primera nota\nCon detalle", "Segunda nota"):
        code, sobre = invocar(monkeypatch, capsys, "nota", texto)
        assert code == 0 and sobre["estado"] == "OK"
    _, sobre = invocar(monkeypatch, capsys, "memoria")
    texto = sobre["datos"]["texto"]
    assert "Primera nota\nCon detalle" in texto and "Segunda nota" in texto
    assert texto.index("Primera") < texto.index("Segunda")
    for fecha in (line.removeprefix("## ") for line in texto.splitlines() if line.startswith("## ")):
        datetime.fromisoformat(fecha)
    assert sum(line.startswith("## ") for line in texto.splitlines()) == 2


@pytest.mark.parametrize("longitud,codigo", [(20000, 0), (20001, 1)])
def test_tope_por_nota_no_trunca(rrhh_env, monkeypatch, capsys, longitud, codigo):
    code, sobre = invocar(monkeypatch, capsys, "nota", "ñ" * longitud)
    assert code == codigo
    if codigo == 0:
        _, memoria = invocar(monkeypatch, capsys, "memoria")
        assert "ñ" * longitud in memoria["datos"]["texto"]
    else:
        assert sobre["errores"] and not (rrhh_env.state_dir / "rrhh" / "memoria.md").exists()


def test_tope_total_no_modifica_memoria(rrhh_env, monkeypatch, capsys):
    path = rrhh_env.state_dir / "rrhh" / "memoria.md"
    path.parent.mkdir(parents=True)
    path.write_text("x" * 199999, encoding="utf-8")
    code, sobre = invocar(monkeypatch, capsys, "nota", "y")
    assert code == 1 and sobre["errores"]
    assert path.read_text() == "x" * 199999


def test_tope_total_inclusivo(rrhh_env, monkeypatch, capsys):
    path = rrhh_env.state_dir / "rrhh" / "memoria.md"
    path.parent.mkdir(parents=True)
    path.write_text("x" * 199900, encoding="utf-8")
    code, _ = invocar(monkeypatch, capsys, "nota", "y" * 74)
    assert code == 0 and len(path.read_text()) == 200000
    code, sobre = invocar(monkeypatch, capsys, "nota", "otra nota")
    assert code == 1 and sobre["errores"] and len(path.read_text()) == 200000


def test_nota_usa_filelock(rrhh_env, monkeypatch, capsys):
    real_acquire = FileLock.acquire
    locks = []

    def acquire(lock, *args, **kwargs):
        acquired = real_acquire(lock, *args, **kwargs)
        with pytest.raises(Timeout):
            real_acquire(FileLock(lock.lock_file), timeout=0)
        locks.append(lock.lock_file)
        return acquired

    monkeypatch.setattr(FileLock, "acquire", acquire)
    code, _ = invocar(monkeypatch, capsys, "nota", "Nota bloqueada durante el append")
    assert code == 0 and len(locks) == 1


@pytest.mark.parametrize("comando,entrada", [("triage", "   "),
    ("triage", '{"texto": " "}'), ("triage", '{"texto": 1}'),
    ("evaluar", "[]"), ("evaluar", '{"usos": "inválido"}'),
    ("evaluar", '{"desconocido": []}'), ("reclutador", '{"texto": ""}')])
def test_pedidos_invalidos_emiten_json(rrhh_env, monkeypatch, capsys, comando, entrada):
    code, sobre = invocar(monkeypatch, capsys, comando, entrada)
    assert code == 1 and sobre["estado"] == "ERROR" and sobre["errores"]


@pytest.mark.parametrize("nueva", [False, True])
def test_argv_rrhh_exacto(nueva):
    esperado = ["claude", "--agent", "orquesta:rrhh", "-n", "orquesta-rrhh",
        "--allowedTools", "Bash(orquesta rrhh *)", "--disallowedTools", "Edit", "Write",
        "NotebookEdit", "--permission-mode", "dontAsk"]
    assert cli.argv_rrhh(nueva) == esperado + ([] if nueva else ["--continue"])


@pytest.mark.parametrize("nueva,resultados", [(False, [0]), (False, [1, 7]), (True, [7])])
def test_abrir_cwd_reintento_y_exit_code(rrhh_env, monkeypatch, capsys, nueva, resultados):
    calls = []
    codes = iter(resultados)

    def run(argv, *, cwd):
        calls.append(argv)
        assert cwd == rrhh_env.state_dir / "rrhh" and cwd.is_dir()
        return subprocess.CompletedProcess(argv, next(codes))

    monkeypatch.setattr(subprocess, "run", run)
    code = cli.main(["rrhh", "abrir", *(["--nueva"] if nueva else [])])
    assert code == resultados[-1] and len(calls) == len(resultados)
    assert ("--continue" in calls[0]) is (not nueva)
    if len(calls) == 2:
        assert "--continue" not in calls[1]
    assert capsys.readouterr().out == ""


def test_abrir_sin_binario_error_claro(rrhh_env, monkeypatch, capsys):
    def ausente(*a, **kw):
        raise FileNotFoundError("claude")

    monkeypatch.setattr(subprocess, "run", ausente)
    assert cli.main(["rrhh", "abrir"]) == 1
    assert "claude" in capsys.readouterr().err


def test_triage_acepta_texto_plano(rrhh_env, monkeypatch, capsys):
    code, sobre = invocar(monkeypatch, capsys, "triage", "revisar el login de la API")
    assert code == 0 and sobre["estado"] == "OK" and "dificultad" in sobre["datos"]
