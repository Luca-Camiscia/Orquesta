"""Runner (`orquesta run`): arma el comando desde backends.yaml y aplica las reglas en código."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from helpers import REPO, backends_dict, dump_yaml, git, load_yaml
from orquesta import ledger, migracion, runner, worktrees
from orquesta.generate import apply_plan, build_plan
from orquesta.schema import parse_backends


def editar(path: Path, mod) -> None:
    data = load_yaml(path)
    mod(data)
    dump_yaml(data, path)


def deploy(env, mod=None) -> None:
    if mod:
        editar(env.registry, mod)
    plan = build_plan(env)
    assert not plan.errors and not plan.violations, (plan.errors, plan.violations)
    apply_plan(plan, "deploy", run_tests=None)


@pytest.fixture
def proyecto(tmp_path) -> Path:
    p = tmp_path / "proyecto"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    (p / "README.md").write_text("hola\n")
    git(p, "add", "-A")
    git(p, "commit", "-qm", "init")
    return p


def correr(env, agente, prompt, cwd, **kw) -> runner.RunResult:
    return runner.run(runner.RunRequest(agent=agente, cwd=cwd, prompt=prompt, **kw), env)


@pytest.mark.parametrize("fase", ["plugin", "legacy"])
def test_runner_manifest_error_no_ejecuta_backend(env, proyecto, monkeypatch, fase):
    deploy(env)
    if fase == "legacy":
        env.manifest.write_text("invalid", encoding="utf-8")
    if fase == "plugin":
        env.plugin_manifest.unlink(missing_ok=True)  # generate en legacy también escribe el plugin
    migracion.set_fase(env, fase)

    def backend_prohibido(*args, **kwargs):
        pytest.fail("se ejecutó el backend con un manifiesto ausente o corrupto")

    monkeypatch.setattr(runner, "_execute", backend_prohibido)
    result = correr(env, "codex-critico", "hola", proyecto)
    assert result.status == "ERROR"
    assert result.exit_code == runner.EXIT_CODES["ERROR"]
    salida = runner.format_result(result)  # los wrappers leen la línea ESTADO: mismo formato que el resto
    assert salida.startswith("ESTADO: ERROR — ")
    assert f"manifiesto de la fase {fase}" in salida and "orquesta doctor" in salida


BACKENDS = parse_backends(backends_dict()).backends
REAL = parse_backends(load_yaml(REPO / "backends.yaml")).backends


# --- armado del comando ----------------------------------------------------------------------

@pytest.mark.parametrize("effort", [None, "high"])
def test_codex_flags_de_modelo_y_esfuerzo(env, effort):
    cmd = runner.build_command(REAL["codex"], mode="read-only", model="gpt-6.1-sol",
                               effort=effort, cwd=Path("/proyecto"), prompt="hola",
                               state_dir=env.state_dir)
    expected = ["codex", "exec", "--skip-git-repo-check", "--color", "never",
                "--sandbox", "read-only", "-m", "gpt-6.1-sol"]
    if effort:
        expected += ["-c", "model_reasoning_effort=high"]
    assert cmd.argv == expected + ["-C", "/proyecto", "-"]
    assert cmd.stdin == "hola"


def test_backend_sin_effort_flag_no_agrega_esfuerzo(env):
    cmd = runner.build_command(BACKENDS["agy"], mode="read-only", model=None,
                               effort="high", cwd=Path("/proyecto"), prompt="hola",
                               state_dir=env.state_dir)
    assert cmd.argv == BACKENDS["agy"].command + ["--output-format", "json", "-p", "hola"]


def test_run_usa_modelo_y_esfuerzo_desplegados(env, proyecto):
    editar(env.backends, lambda d: d["backends"]["codex"].update(
        effort_flag=["-c", "model_reasoning_effort={effort}"]))
    deploy(env, lambda d: d["agents"][2].update(
        backend_model="gpt-6.1-sol", backend_effort="high"))
    # Editar el YAML sin desplegar no debe cambiar la configuración aplicada.
    editar(env.registry, lambda d: d["agents"][2].update(backend_effort="low"))
    r = correr(env, "codex-critico", "ECHOARGS", proyecto)
    assert r.status == "OK", r.detail
    args = json.loads(r.output)["args"]
    assert args[args.index("-m"):args.index("-m") + 4] == [
        "-m", "gpt-6.1-sol", "-c", "model_reasoning_effort=high"]

def test_codex_recibe_el_prompt_por_stdin_con_los_flags_del_modo(env):
    cmd = runner.build_command(BACKENDS["codex"], mode="read-only", model="gpt-x",
                               cwd=Path("/proyecto"), prompt="hola", state_dir=env.state_dir)
    assert cmd.stdin == "hola" and cmd.argv[-1] == "-"
    i = cmd.argv.index("--sandbox")
    assert cmd.argv[i:i + 2] == ["--sandbox", "read-only"]
    assert cmd.argv[cmd.argv.index("-m") + 1] == "gpt-x"
    assert cmd.argv[cmd.argv.index("-C") + 1] == "/proyecto"


def test_agy_recibe_el_prompt_como_ultimo_argumento(env):
    cmd = runner.build_command(BACKENDS["agy"], mode="read-only", model=None,
                               cwd=Path("/proyecto"), prompt="hola", state_dir=env.state_dir)
    assert cmd.argv[-2:] == ["-p", "hola"] and cmd.stdin is None
    assert "--model" not in cmd.argv


def test_agy_con_prompt_largo_lo_pasa_por_archivo(env):
    largo = "x" * 5000
    cmd = runner.build_command(BACKENDS["agy"], mode="read-only", model=None,
                               cwd=Path("/proyecto"), prompt=largo, state_dir=env.state_dir)
    assert len(cmd.argv[-1]) < 2000 and cmd.temp_files
    assert cmd.temp_files[0].read_text() == largo
    assert str(cmd.temp_files[0]) in cmd.argv[-1]


def test_read_only_de_agy_corre_dentro_de_bwrap_con_proyecto_solo_lectura(env):
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=Path("/proyecto"),
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    assert cmd.argv[:5] == ["bwrap", "--ro-bind", "/", "/", "--dev"]
    assert "--die-with-parent" in cmd.argv
    assert cmd.argv[cmd.argv.index("--") + 1] == "agy"
    i = cmd.argv.index("/proyecto")
    assert cmd.argv[i - 1:i + 2] == ["--ro-bind", "/proyecto", "/proyecto"]


def test_read_only_remonta_el_proyecto_despues_del_tmp_escribible(env):
    cwd = Path("/tmp/proyecto")
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=cwd,
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    pre = cmd.argv[:cmd.argv.index("--")]
    i = pre.index("/tmp")
    j = pre.index(str(cwd))
    assert pre[i - 1:i + 2] == ["--bind", "/tmp", "/tmp"]
    assert pre[j - 1:j + 2] == ["--ro-bind", str(cwd), str(cwd)]
    assert j > i


def test_read_only_desde_subdirectorio_remonta_raiz_git(env, proyecto):
    cwd = proyecto / "src"
    cwd.mkdir()
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=cwd,
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    pre = cmd.argv[:cmd.argv.index("--")]
    i = pre.index(str(proyecto))
    assert pre[i - 1:i + 2] == ["--ro-bind", str(proyecto), str(proyecto)]
    assert i > pre.index("/tmp")


def test_read_only_worktree_remonta_git_comun(env, proyecto):
    wt = worktrees.create(env, repo=proyecto, agent="codex-forja", task="fix")
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=wt.path,
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    pre = cmd.argv[:cmd.argv.index("--")]
    comun = str(proyecto / ".git")
    i = pre.index(comun)
    assert pre[i - 1:i + 2] == ["--ro-bind", comun, comun]
    assert i > pre.index("/tmp")


def _bwrap_disponible():
    return shutil.which("bwrap") is not None and subprocess.run(
        ["bwrap", "--ro-bind", "/", "/", "true"], capture_output=True).returncode == 0


@pytest.mark.skipif(not _bwrap_disponible(), reason="bwrap no está o no puede crear namespaces")
def test_read_only_bwrap_impide_escribir_en_proyecto_bajo_tmp(tmp_path):
    prefix, warnings = runner._sandbox_prefix(
        REAL["agy"], REAL["agy"].modes["read-only"], "read-only", tmp_path,
        "linux", Path.home())
    archivo = tmp_path / "colado.txt"
    result = subprocess.run(prefix + ["touch", str(archivo)], cwd=tmp_path, capture_output=True)
    assert not warnings
    assert result.returncode != 0, result.stderr
    assert not archivo.exists()


@pytest.mark.skipif(not _bwrap_disponible(), reason="bwrap no está o no puede crear namespaces")
@pytest.mark.parametrize("destino", ["colado.txt", ".git/colado.txt"])
def test_read_only_bwrap_desde_subdirectorio_impide_escribir_en_raiz(proyecto, destino):
    cwd = proyecto / "src"
    cwd.mkdir()
    prefix, warnings = runner._sandbox_prefix(
        REAL["agy"], REAL["agy"].modes["read-only"], "read-only", cwd,
        "linux", Path.home())
    archivo = proyecto / destino
    result = subprocess.run(prefix + ["touch", str(archivo)], cwd=cwd, capture_output=True)
    assert not warnings
    assert result.returncode != 0, result.stderr
    assert not archivo.exists()


def test_write_de_agy_en_bwrap_solo_bindea_el_workspace(env):
    cmd = runner.build_command(REAL["agy"], mode="write", model=None, cwd=Path("/proyecto"),
                               prompt="hola", state_dir=env.state_dir, platform="linux")
    pre = cmd.argv[:cmd.argv.index("--")]
    i = pre.index("/proyecto")
    assert pre[i - 1:i + 2] == ["--bind", "/proyecto", "/proyecto"]


def test_fuera_de_linux_no_hay_sandbox_y_avisa(env):
    cmd = runner.build_command(REAL["agy"], mode="read-only", model=None, cwd=Path("/p"),
                               prompt="hola", state_dir=env.state_dir, platform="win32")
    assert cmd.argv[0] == "agy"
    assert cmd.warnings and "sandbox" in cmd.warnings[0]


# --- ejecución -------------------------------------------------------------------------------

def test_run_ok_con_codex(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert (r.status, r.exit_code) == ("OK", 0)
    assert r.output.startswith("PONG:")


@pytest.mark.parametrize("motivo,intentos,progreso", [
    ("tope", 4, False), ("tope", 6, True), ("cerrada", 1, True),
])
@pytest.mark.parametrize("ubicacion", ["raiz", "subdirectorio", "worktree"])
def test_run_ledger_bloquea_antes_de_ejecutar_backend(
        env, proyecto, monkeypatch, motivo, intentos, progreso, ubicacion):
    deploy(env)
    tarea = "fix_promedio.v2"
    pid = worktrees.project_id(proyecto)
    for _ in range(intentos):
        ledger.anotar(pid, tarea, progreso=progreso, paths=env)
    if motivo == "cerrada":
        ledger.cerrar(pid, tarea, motivo="resuelto", paths=env)
    cwd = proyecto
    if ubicacion == "subdirectorio":
        cwd = proyecto / "src"
        cwd.mkdir()
    elif ubicacion == "worktree":
        cwd = worktrees.create(env, repo=proyecto, agent="codex-forja", task=tarea).path

    def backend_prohibido(*args, **kwargs):
        pytest.fail("se ejecutó el backend con la tarea bloqueada en el ledger")

    monkeypatch.setattr(runner, "_execute", backend_prohibido)
    r = correr(env, "codex-critico", "hola", cwd, task=tarea)
    assert (r.status, r.exit_code) == ("BLOQUEADO", 3)
    assert r.detail == ("tarea cerrada en el ledger" if motivo == "cerrada"
                        else "tope del ledger alcanzado: escalá al usuario")


def test_run_ledger_bloquea_tambien_con_la_tarea_sin_slugificar(env, proyecto, monkeypatch):
    """`--task "Fix Promedio"` trabaja en la rama fix-promedio: el ledger anotado con ese slug la frena."""
    deploy(env)
    pid = worktrees.project_id(proyecto)
    for _ in range(4):
        ledger.anotar(pid, "fix-promedio", progreso=False, paths=env)
    monkeypatch.setattr(runner, "_execute", lambda *a, **k: pytest.fail("se ejecutó el backend"))
    r = correr(env, "codex-forja", "hola", proyecto, task="Fix Promedio")
    assert (r.status, r.exit_code) == ("BLOQUEADO", 3)


def test_run_tarea_sin_anotaciones_corre_normal(env, proyecto):
    deploy(env)
    pid = worktrees.project_id(proyecto)
    for _ in range(4):
        ledger.anotar(pid, "otra", progreso=False, paths=env)
    r = correr(env, "codex-critico", "hola", proyecto, task="nueva")
    assert (r.status, r.exit_code) == ("OK", 0)
    assert r.output.startswith("PONG:")


@pytest.mark.parametrize("task", [None, "revision"])
def test_run_guarda_salida_completa_y_sha256(env, proyecto, task):
    deploy(env)
    r = correr(env, "codex-critico", "revisá esto", proyecto, task=task)
    assert (r.status, r.exit_code) == ("OK", 0)
    salida = Path(r.meta["salida"])
    assert salida.is_absolute()
    assert salida.parent == env.state_dir / "salidas" / "codex-critico"
    assert salida.read_text(encoding="utf-8") == r.output
    sha = hashlib.sha256(r.output.encode("utf-8")).hexdigest()
    assert r.meta["salida_sha256"] == sha
    assert re.fullmatch(rf"\d{{8}}T\d{{6}}Z-{task or 'sin-tarea'}-{sha[:8]}\.md", salida.name)


def test_format_result_muestra_referencia_a_salida_completa(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    r.meta.update(branch="orquesta/codex-critico/revision", commit="abc123")
    texto = runner.format_result(r)
    linea = f"salida completa: {r.meta['salida']} (sha256 {r.meta['salida_sha256'][:12]})"
    assert linea in texto.splitlines()
    assert texto.index("── orquesta run ──") < texto.index("rama:") < texto.index("commit:") < texto.index(linea)


@pytest.mark.parametrize("operacion", ["mkdir", "open"])
def test_run_avisa_si_no_puede_guardar_salida(env, proyecto, monkeypatch, operacion):
    deploy(env)
    duenio = os if operacion == "open" else Path
    original = getattr(duenio, operacion)

    def fallar_salida(path, *args, **kwargs):
        if Path(path).is_relative_to(env.state_dir / "salidas"):
            raise OSError("sin permiso")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(duenio, operacion, fallar_salida)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert (r.status, r.exit_code) == ("OK", 0)
    assert r.output.startswith("PONG:")
    assert "salida" not in r.meta and "salida_sha256" not in r.meta
    assert "no se pudo guardar la salida completa: sin permiso" in r.meta["warnings"]


@pytest.mark.parametrize("task", ["../../../escape", "a/b", "/tmp/x"])
def test_run_la_tarea_no_arma_rutas_fuera_de_la_carpeta(env, proyecto, task):
    deploy(env)
    carpeta = env.state_dir / "salidas" / "codex-critico"
    carpeta.mkdir(parents=True)
    r = correr(env, "codex-critico", "revisá esto", proyecto, task=task)
    salida = Path(r.meta["salida"])
    assert salida.parent == carpeta
    assert salida.read_text(encoding="utf-8") == r.output


def test_run_salida_solo_legible_por_el_usuario(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert Path(r.meta["salida"]).stat().st_mode & 0o777 == 0o600


def test_run_no_sigue_enlaces_simbolicos_en_las_salidas(env, proyecto, tmp_path):
    deploy(env)
    afuera = tmp_path / "afuera"
    afuera.mkdir()
    (env.state_dir / "salidas").mkdir(parents=True)
    (env.state_dir / "salidas" / "codex-critico").symlink_to(afuera)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert r.status == "OK" and "salida" not in r.meta
    assert any("enlace simbólico" in w for w in r.meta["warnings"])
    assert list(afuera.iterdir()) == []


def test_run_retiene_las_200_salidas_mas_recientes(env, proyecto):
    deploy(env)
    carpeta = env.state_dir / "salidas" / "codex-critico"
    carpeta.mkdir(parents=True)
    viejas = [carpeta / f"20000101T000000Z-revision-{i:08x}.md" for i in range(202)]
    for path in reversed(viejas):
        path.write_text("salida anterior", encoding="utf-8")
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert r.status == "OK"
    assert set(carpeta.iterdir()) == {*viejas[3:], Path(r.meta["salida"])}


def test_run_ignora_errores_al_borrar_salidas_viejas(env, proyecto, monkeypatch):
    deploy(env)
    carpeta = env.state_dir / "salidas" / "codex-critico"
    carpeta.mkdir(parents=True)
    for i in range(200):
        (carpeta / f"20000101T000000Z-revision-{i:08x}.md").touch()
    original = Path.unlink

    def fallar_borrado(path, *args, **kwargs):
        if path.parent == carpeta:
            raise OSError("sin permiso")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fallar_borrado)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert (r.status, r.exit_code) == ("OK", 0)
    assert Path(r.meta["salida"]).read_text(encoding="utf-8") == r.output


def test_run_salida_vacia_no_crea_archivo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "EMPTY", proyecto)
    assert r.status == "FALLO_BACKEND" and r.output == ""
    assert "salida" not in r.meta and "salida_sha256" not in r.meta
    assert not (env.state_dir / "salidas").exists()


def test_run_guarda_salida_despues_del_chequeo_read_only(env, proyecto, monkeypatch):
    deploy(env)
    original = runner.tree_state

    def chequear_arbol(cwd):
        assert not (env.state_dir / "salidas").exists()
        return original(cwd)

    monkeypatch.setattr(runner, "tree_state", chequear_arbol)
    r = correr(env, "codex-critico", "revisá esto", proyecto)
    assert r.status == "OK"
    assert Path(r.meta["salida"]).is_file()


def test_run_antepone_la_persona_y_el_modo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "ECHOARGS", proyecto)
    prompt = json.loads(r.output)["prompt"]
    assert prompt.startswith("Sos el crítico.")
    assert "SOLO LECTURA" in prompt and "ECHOARGS\n\n---\n\n" in prompt
    assert prompt.rstrip().endswith(runner.CONTRACT.splitlines()[-1])


def test_run_ok_con_agy_devuelve_el_texto_de_la_respuesta_json(env, proyecto):
    deploy(env)
    r = correr(env, "gemini-lector", "hola", proyecto)
    assert r.status == "OK" and r.output.startswith("PONG:") and "{" not in r.output


def test_status_json_distinto_de_success_es_fallo(env, proyecto):
    deploy(env)
    assert correr(env, "gemini-lector", "STATUSFAIL", proyecto).status == "FALLO_BACKEND"


def test_salida_vacia_es_fallo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "EMPTY", proyecto)
    assert r.status == "FALLO_BACKEND" and "vacía" in r.detail


def test_rate_limit(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "RATELIMIT", proyecto)
    assert (r.status, r.exit_code) == ("RATE_LIMIT", 75)


def test_mencionar_el_limite_en_una_respuesta_exitosa_no_es_rate_limit(env, proyecto):
    deploy(env)
    assert correr(env, "codex-critico", "MENTION_LIMIT", proyecto).status == "OK"


def test_timeout(env, proyecto):
    editar(env.backends, lambda d: d["backends"]["codex"]["modes"]["read-only"].update(timeout_s=1))
    deploy(env)
    r = correr(env, "codex-critico", "SLEEP:5", proyecto)
    assert (r.status, r.exit_code) == ("TIMEOUT", 124)


def test_orquesta_off_desde_un_subdirectorio(env, proyecto):
    deploy(env)
    (proyecto / ".orquesta-off").touch()
    (proyecto / "sub").mkdir()
    r = correr(env, "codex-critico", "hola", proyecto / "sub")
    assert (r.status, r.exit_code) == ("ORQUESTA_OFF", 3)


def test_write_fuera_de_un_repo_git_es_no_git(env, tmp_path):
    deploy(env)
    suelto = tmp_path / "suelto"
    suelto.mkdir()
    r = correr(env, "codex-forja", "WRITE:x.txt", suelto, task="fix")
    assert (r.status, r.exit_code) == ("NO_GIT", 4)


def test_write_con_worktree_sin_task_es_error(env, proyecto):
    deploy(env)
    assert correr(env, "codex-forja", "WRITE:x.txt", proyecto).status == "ERROR"


def test_read_only_detecta_escrituras(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "WRITE:colado.txt", proyecto)
    assert (r.status, r.exit_code) == ("VIOLACION_READ_ONLY", 7)
    assert "colado.txt" in r.detail


def test_read_only_detecta_commit_con_arbol_limpio(env, proyecto):
    deploy(env)
    antes = git(proyecto, "rev-parse", "HEAD")
    r = correr(env, "codex-critico", "WRITE:README.md COMMIT", proyecto)
    assert git(proyecto, "status", "--porcelain") == ""
    assert git(proyecto, "rev-parse", "HEAD") != antes
    assert (r.status, r.exit_code) == ("VIOLACION_READ_ONLY", 7)
    assert "cambió HEAD o una rama" in r.detail


@pytest.mark.parametrize("cambio", ["rama", "head-simbolico", "detached"])
def test_tree_state_detecta_refs_sin_cambios_de_archivos(proyecto, cambio):
    git(proyecto, "branch", "otra")
    antes = runner.tree_state(proyecto)
    if cambio == "rama":
        git(proyecto, "branch", "nueva")
    elif cambio == "head-simbolico":
        git(proyecto, "symbolic-ref", "HEAD", "refs/heads/otra")
    else:
        git(proyecto, "checkout", "--detach", "-q")
    despues = runner.tree_state(proyecto)
    assert antes.entries == despues.entries == frozenset()
    assert antes.digest != despues.digest


@pytest.mark.parametrize("rastreado", [False, True])
def test_read_only_nombra_el_archivo_ya_modificado_que_cambio(env, proyecto, rastreado):
    deploy(env)
    (proyecto / "plan.md").write_text("antes\n", encoding="utf-8")
    if rastreado:
        git(proyecto, "add", "plan.md")
        git(proyecto, "commit", "-qm", "plan")
        (proyecto / "plan.md").write_text("sucio antes de la corrida\n", encoding="utf-8")
    r = correr(env, "codex-critico", "WRITE:plan.md", proyecto)
    assert (r.status, r.exit_code) == ("VIOLACION_READ_ONLY", 7)
    assert "plan.md" in r.detail and "ya modificados" not in r.detail


def test_read_only_detecta_archivo_staged_que_el_backend_vuelve_a_stagear(env, proyecto):
    deploy(env)
    archivo = proyecto / "dato.txt"
    archivo.write_text("original\n", encoding="utf-8")
    git(proyecto, "add", "dato.txt")
    git(proyecto, "commit", "-qm", "dato")
    archivo.write_text("staged antes de la corrida\n", encoding="utf-8")
    git(proyecto, "add", "dato.txt")
    status = git(proyecto, "status", "--porcelain")
    assert status == "M  dato.txt"

    r = correr(env, "codex-critico", "WRITE:dato.txt STAGE:dato.txt", proyecto)

    assert git(proyecto, "status", "--porcelain") == status
    assert git(proyecto, "diff", "--binary") == ""
    assert (r.status, r.exit_code) == ("VIOLACION_READ_ONLY", 7)
    assert "dato.txt" in r.detail


@pytest.mark.parametrize("cambio", ["indice", "firma"])
def test_tree_state_detecta_cambios_con_status_y_diff_identicos(proyecto, cambio):
    archivo = proyecto / "README.md"
    archivo.write_text("antes\n", encoding="utf-8")
    git(proyecto, "add", "README.md")
    st = archivo.stat()
    antes = runner.tree_state(proyecto)
    if cambio == "indice":
        archivo.write_text("luego\n", encoding="utf-8")
        git(proyecto, "add", "README.md")
        os.utime(archivo, ns=(st.st_atime_ns, st.st_mtime_ns))
    else:
        os.utime(archivo, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    despues = runner.tree_state(proyecto)
    assert antes.entries == despues.entries
    assert git(proyecto, "diff", "--binary") == ""
    assert antes.digest != despues.digest


def test_write_en_worktree_commitea_en_su_rama_sin_tocar_el_repo(env, proyecto):
    deploy(env)
    r = correr(env, "codex-forja", "WRITE:arreglo.txt", proyecto, task="fix promedio")
    assert r.status == "OK", r.detail
    assert r.meta["branch"] == "orquesta/codex-forja/fix-promedio"
    assert r.meta["commit"] and "arreglo.txt" in r.meta["diffstat"]
    assert not (proyecto / "arreglo.txt").exists()
    assert "arreglo.txt" in git(proyecto, "show", "--stat", "--format=", r.meta["branch"])


def test_ocupado_si_no_hay_slots_libres(env, proyecto):
    deploy(env)  # agy: max_parallel 1
    with runner.hold_slots(env, "agy", 1):
        r = correr(env, "gemini-lector", "hola", proyecto, wait_s=0.2)
    assert (r.status, r.exit_code) == ("OCUPADO", 5)


def test_agente_no_desplegado(env, proyecto):
    r = correr(env, "codex-critico", "hola", proyecto)
    assert r.status == "ERROR" and "orquesta generate" in r.detail


def test_agente_nativo_no_pasa_por_el_runner(env, proyecto):
    deploy(env)
    r = correr(env, "mantenedor", "hola", proyecto)
    assert r.status == "ERROR" and "nativo" in r.detail


def test_usa_la_config_aplicada_y_no_la_editada_sin_aplicar(env, proyecto):
    deploy(env)
    editar(env.registry, lambda d: d["agents"][2].update(prompt="PERSONA NUEVA SIN APLICAR"))
    r = correr(env, "codex-critico", "ECHOARGS", proyecto)
    assert "PERSONA NUEVA" not in json.loads(r.output)["prompt"]


def test_ping(env):
    deploy(env)
    r = runner.ping(env, "codex-critico")
    assert r.status == "OK" and "PONG" in r.output and r.latency_s >= 0


def test_formato_de_salida_con_estado_primero_si_falla(env, proyecto):
    deploy(env)
    r = correr(env, "codex-critico", "RATELIMIT", proyecto)
    assert runner.format_result(r).startswith("ESTADO: RATE_LIMIT")
