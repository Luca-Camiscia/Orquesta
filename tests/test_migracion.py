"""Fase de deploy persistida por configuración de Claude Code."""
import hashlib
import json

import pytest

from orquesta import migracion
from orquesta.paths import Paths, default_paths

from helpers import git, snapshot


@pytest.fixture
def deploy(env):
    """Deploy legacy real, sin ejecutar backends ni hacer commits con generate."""
    from orquesta import generate
    env.claude_md.write_bytes(b"Antes\r\n\r\n")
    plan = generate.build_plan(env)
    assert not plan.errors and not plan.violations
    for change in plan.changes:
        if not change.path.is_relative_to(env.plugin_root):
            generate._write(env, change)
    generate._write_plugin(plan)
    env.claude_md.write_bytes(env.claude_md.read_bytes() + b"\r\nDespues\r\n")
    return env


class ClaudeFalso:
    """Simula solo la CLI de plugins y exige una config temporal inyectada."""

    def __init__(self, paths, *, origen=None, falla=None):
        self.paths = paths
        self.origen = origen or paths.plugin_dir
        self.falla = falla
        self.calls = []
        self.instalado = False
        self.habilitado = True
        self.marketplace = False

    def __call__(self, cmd, *, env):
        assert env["CLAUDE_CONFIG_DIR"] == str(self.paths.claude_dir)
        assert cmd[:2] == ["claude", "plugin"]
        self.calls.append(cmd)
        if self.falla and cmd[2:2 + len(self.falla)] == self.falla:
            return 1, "", "falló claude"
        if cmd[2:] == ["marketplace", "add", str(self.paths.plugin_root)]:
            self.marketplace = True
            self.paths.plugins_dir.mkdir(exist_ok=True)
            (self.paths.plugins_dir / "known_marketplaces.json").write_text(json.dumps({
                "orquesta-local": {"source": {"source": "directory", "path": str(self.paths.plugin_root)}}}))
        elif cmd[2:] == ["install", "orquesta@orquesta-local", "-s", "user"]:
            assert self.marketplace
            self.instalado = True
        elif cmd[2:] == ["list", "--json"]:
            # Formato real de Claude Code 2.1.290: installPath es la copia en cache; readFromFolder, la carpeta.
            entries = [{"id": "orquesta@orquesta-local", "scope": "user", "version": "1.0.0",
                        "enabled": self.habilitado,
                        "installPath": str(self.paths.plugins_dir / "cache" / "orquesta-local" / "orquesta"),
                        "readFromFolder": str(self.origen)}] if self.instalado else []
            return 0, json.dumps(entries), ""
        elif cmd[2:] == ["enable", "orquesta@orquesta-local", "-s", "user"]:
            self.habilitado = True
        elif cmd[2:] == ["uninstall", "orquesta@orquesta-local", "-s", "user"]:
            self.instalado = False
        elif cmd[2:] == ["marketplace", "remove", "orquesta-local"]:
            self.marketplace = False
            (self.paths.plugins_dir / "known_marketplaces.json").unlink(missing_ok=True)
        else:
            pytest.fail(f"comando inesperado: {cmd}")
        return 0, "", ""


def corte(paths, run, **kwargs):
    from orquesta.migracion_cmd import migrar_corte
    return migrar_corte(paths, run, True, leer_tty=lambda prompt: "corte\n", **kwargs)


def rollback(paths, run):
    from orquesta.migracion_cmd import migrar_rollback
    return migrar_rollback(paths, run, True, leer_tty=lambda prompt: "rollback\n")


def test_estado_inicial_es_legacy(env):
    from orquesta.migracion_cmd import migrar_estado
    assert migrar_estado(env, ClaudeFalso(env)) == {
        "fase": "legacy", "plugin_generado": False, "plugin_instalado": False,
        "deploy_viejo": False, "diario": {},
    }
    assert not env.state_dir.exists()


def test_corte_sin_confirm_devuelve_NECESITO_CONFIRMACION(deploy):
    from orquesta.migracion_cmd import migrar_corte
    run = ClaudeFalso(deploy)
    antes = snapshot(deploy.claude_dir), snapshot(deploy.home)
    estado, datos = migrar_corte(deploy, run, False)
    assert estado == "NECESITO_CONFIRMACION"
    assert datos["comando"] == "orquesta migrar corte --confirm --json"
    assert antes == (snapshot(deploy.claude_dir), snapshot(deploy.home))
    assert not run.calls


@pytest.mark.parametrize("accion", ["corte", "rollback"])
def test_con_confirm_sin_tty_devuelve_NECESITO_CONFIRMACION(deploy, accion):
    from orquesta import confirmar, migracion_cmd
    def sin_tty(prompt):
        raise confirmar.SinTTY(f"orquesta migrar {accion} --confirm --json")
    antes = snapshot(deploy.claude_dir), snapshot(deploy.home)
    estado, datos = getattr(migracion_cmd, f"migrar_{accion}")(
        deploy, ClaudeFalso(deploy), True, leer_tty=sin_tty)
    assert estado == "NECESITO_CONFIRMACION"
    assert datos["comando"] == f"orquesta migrar {accion} --confirm --json"
    assert antes == (snapshot(deploy.claude_dir), snapshot(deploy.home))


def test_corte_con_confirm_y_tty_completa_todos_los_pasos(deploy):
    from orquesta.migracion_cmd import migrar_estado
    run = ClaudeFalso(deploy)
    original = deploy.claude_md.read_bytes()
    estado, datos = corte(deploy, run)
    assert estado == "OK", datos
    assert migracion.fase(deploy) == "plugin"
    assert not deploy.agents_dir.exists()
    assert deploy.claude_md.read_bytes() == b"Antes\r\n\r\n\r\n\r\nDespues\r\n"
    diario = migrar_estado(deploy, run)["diario"]
    assert set(diario) == {"backup_ok", "plugin_generado", "plugin_instalado",
                           "plugin_verificado", "legacy_retirado"}
    backups = list((deploy.state_dir / "migracion").glob("backup-*"))
    assert len(backups) == 1
    assert (backups[0] / "CLAUDE.md").read_bytes() == original
    calls = list(run.calls)
    assert corte(deploy, run)[0] == "OK"
    assert run.calls == calls


@pytest.mark.parametrize("paso", ["backup_ok", "plugin_generado", "plugin_instalado",
                                 "plugin_verificado", "legacy_retirado"])
def test_corte_idempotente_sobre_interrupciones(deploy, monkeypatch, paso):
    from orquesta import migracion_cmd
    run = ClaudeFalso(deploy)
    marcar = migracion_cmd._marcar
    def interrumpir(paths, diario, nombre):
        marcar(paths, diario, nombre)
        if nombre == paso:
            raise RuntimeError("interrupción")
    with monkeypatch.context() as m:
        m.setattr(migracion_cmd, "_marcar", interrumpir)
        with pytest.raises(RuntimeError, match="interrupción"):
            corte(deploy, run)
    assert corte(deploy, run)[0] == "OK"
    assert migracion.fase(deploy) == "plugin"
    assert len(list((deploy.state_dir / "migracion").glob("backup-*"))) == 1
    assert sum(c[2] == "install" for c in run.calls) == 1
    assert 1 <= sum(c[2] == "list" for c in run.calls) <= 3  # verificación + reconciliación del estado real


@pytest.mark.parametrize("falla", [["marketplace", "add"], ["install"], ["list"], None])
def test_corte_falla_si_plugin_falla_y_preserva_legacy(deploy, tmp_path, falla):
    run = ClaudeFalso(deploy, origen=tmp_path / "cache", falla=falla)
    antes = snapshot(deploy.agents_dir), deploy.claude_md.read_bytes()
    estado, datos = corte(deploy, run)
    assert estado == "ERROR", datos
    assert migracion.fase(deploy) == "legacy"
    assert antes == (snapshot(deploy.agents_dir), deploy.claude_md.read_bytes())


def test_rollback_tras_corte_deja_estado_identico(deploy):
    run = ClaudeFalso(deploy)
    antes = snapshot(deploy.claude_dir)
    repo_antes = git(deploy.home, "status", "--porcelain")
    head = git(deploy.home, "rev-parse", "HEAD")
    assert corte(deploy, run)[0] == "OK"
    assert rollback(deploy, run)[0] == "OK"
    assert snapshot(deploy.claude_dir) == antes
    assert migracion.fase(deploy) == "legacy"
    assert not run.instalado and not run.marketplace
    assert git(deploy.home, "status", "--porcelain") == repo_antes
    assert git(deploy.home, "rev-parse", "HEAD") == head
    calls = list(run.calls)
    assert rollback(deploy, run)[0] == "OK"
    assert run.calls == calls
    assert corte(deploy, run)[0] == "OK"
    assert run.instalado and not deploy.agents_dir.exists()


def test_rollback_preserva_ediciones_fuera_del_bloque(deploy):
    from orquesta.claude_md import END, START
    run = ClaudeFalso(deploy)
    original = deploy.claude_md.read_bytes()
    assert corte(deploy, run)[0] == "OK"
    deploy.claude_md.write_bytes(deploy.claude_md.read_bytes().replace(b"Antes", b"Antes editado")
                               .replace(b"Despues", b"Despues editado"))
    assert rollback(deploy, run)[0] == "OK"
    assert deploy.claude_md.read_bytes() == original.replace(b"Antes", b"Antes editado").replace(
        b"Despues", b"Despues editado")
    assert START in deploy.claude_md.read_text() and END in deploy.claude_md.read_text()


def test_rollback_rechaza_si_hashes_no_coinciden(deploy):
    run = ClaudeFalso(deploy)
    assert corte(deploy, run)[0] == "OK"
    backup = next((deploy.state_dir / "migracion").glob("backup-*"))
    next((backup / "agents").glob("*.md")).write_text("alterado")
    estado, datos = rollback(deploy, run)
    assert estado == "ERROR", datos
    assert migracion.fase(deploy) == "plugin"
    assert run.instalado and run.marketplace
    assert not any(c[2] == "uninstall" for c in run.calls)


@pytest.mark.parametrize("original, esperado", [
    ("antes\r\n<!-- orquesta:start -->\r\nbloque\r\n<!-- orquesta:end -->\r\ndespués\n",
     "antes\r\n\r\ndespués\n"),
    ("a<!-- orquesta:start -->x<!-- orquesta:end -->b", "ab"),
    ("sin bloque\r\n\n", "sin bloque\r\n\n"),
])
def test_remove_block_preserva_texto_fuera_del_bloque(original, esperado):
    from orquesta.claude_md import remove_block
    assert remove_block(original) == esperado


@pytest.mark.parametrize("accion", ["estado", "corte", "rollback"])
def test_cli_migrar_json_y_orquesta_off(env, monkeypatch, capsys, accion):
    from orquesta import cli
    monkeypatch.setattr(cli, "default_paths", lambda: env)
    monkeypatch.chdir(env.home)
    args = cli.build_parser().parse_args(["migrar", accion, "--json"])
    run = ClaudeFalso(env)
    code = cli.cmd_migrar(args, run=run)
    result = json.loads(capsys.readouterr().out)
    assert result["comando"] == f"migrar {accion}"
    assert result["estado"] == ("OK" if accion == "estado" else "NECESITO_CONFIRMACION")
    assert code == (0 if accion == "estado" else 3)
    (env.home / ".orquesta-off").touch()
    run.calls.clear()
    assert cli.cmd_migrar(args, run=run) == 1
    assert json.loads(capsys.readouterr().out)["estado"] == "ERROR"
    assert not run.calls


def test_corte_genera_plugin_sin_tocar_legacy_ni_commitear(deploy):
    import shutil
    shutil.rmtree(deploy.plugin_root)
    run = ClaudeFalso(deploy, falla=["install"])
    antes = snapshot(deploy.claude_dir)
    head = git(deploy.home, "rev-parse", "HEAD")
    status = git(deploy.home, "status", "--porcelain")
    assert corte(deploy, run)[0] == "ERROR"
    assert (deploy.plugin_dir / ".claude-plugin" / "plugin.json").is_file()
    assert snapshot(deploy.claude_dir) == antes
    assert git(deploy.home, "status", "--porcelain") == status
    assert git(deploy.home, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("local", [True, False])
def test_estado_verifica_read_from_folder_antes_que_install_path(env, tmp_path, local):
    """Read from usa readFromFolder en la CLI actual, aunque exista un cache instalado."""
    from orquesta.migracion_cmd import migrar_estado
    def run(cmd, *, env):
        return 0, json.dumps([{"id": "orquesta@orquesta-local", "scope": "user", "enabled": True,
                               "installPath": str(paths.plugin_dir if not local else tmp_path / "cache"),
                               "readFromFolder": str(paths.plugin_dir if local else tmp_path / "otro")}]), ""
    paths = env
    assert migrar_estado(env, run)["plugin_instalado"] is local


@pytest.mark.parametrize("paso", ["rollback_legacy_restaurado", "rollback_legacy_verificado",
                                 "rollback_plugin_desinstalado", "rollback_marketplace_retirado",
                                 "rollback_fase_restaurada"])
def test_rollback_idempotente_sobre_interrupciones(deploy, monkeypatch, paso):
    from orquesta import migracion_cmd
    run = ClaudeFalso(deploy)
    antes = snapshot(deploy.claude_dir)
    assert corte(deploy, run)[0] == "OK"
    marcar = migracion_cmd._marcar
    def interrumpir(paths, diario, nombre):
        marcar(paths, diario, nombre)
        if nombre == paso:
            raise RuntimeError("interrupción")
    with monkeypatch.context() as m:
        m.setattr(migracion_cmd, "_marcar", interrumpir)
        with pytest.raises(RuntimeError, match="interrupción"):
            rollback(deploy, run)
    assert rollback(deploy, run)[0] == "OK"
    assert snapshot(deploy.claude_dir) == antes
    assert migracion.fase(deploy) == "legacy"
    assert sum(c[2] == "uninstall" for c in run.calls) == 1
    assert sum(c[2:] == ["marketplace", "remove", "orquesta-local"] for c in run.calls) == 1


def test_corte_y_rollback_usan_lock_compartido_con_generate(deploy, monkeypatch):
    from orquesta import generate
    from filelock import FileLock, Timeout
    run = ClaudeFalso(deploy)
    def validar_lock(cmd, *, env):
        with pytest.raises(Timeout):
            with FileLock(str(deploy.state_dir / "generate.lock"), timeout=0):
                pytest.fail("migración sin lock compartido")
        return run(cmd, env=env)
    def generar_sin_commit(*args, **kwargs):
        pytest.fail("la migración no debe ejecutar el flujo de commits")
    monkeypatch.setattr(generate, "_git_commit", generar_sin_commit)
    assert corte(deploy, validar_lock)[0] == "OK"
    assert rollback(deploy, validar_lock)[0] == "OK"


@pytest.mark.parametrize("operacion", [["marketplace", "add"], ["install"], ["uninstall"],
                                      ["marketplace", "remove"]])
def test_reanuda_si_claude_completa_antes_de_interrumpirse(deploy, operacion):
    """Una respuesta perdida después de una mutación no debe repetirla ni trabar el diario."""
    run = ClaudeFalso(deploy)
    antes = snapshot(deploy.claude_dir)
    inversa = operacion in (["uninstall"], ["marketplace", "remove"])
    if inversa:
        assert corte(deploy, run)[0] == "OK"
    interrumpido = False
    def crash(cmd, *, env):
        nonlocal interrumpido
        if cmd[2:2 + len(operacion)] == operacion:
            if interrumpido:
                return 1, "", "la operación ya fue ejecutada"
            result = run(cmd, env=env)
            interrumpido = True
            raise RuntimeError("respuesta perdida")
        return run(cmd, env=env)
    accion = rollback if inversa else corte
    with pytest.raises(RuntimeError, match="respuesta perdida"):
        accion(deploy, crash)
    estado, datos = accion(deploy, crash)
    assert estado == "OK", datos
    assert sum(c[2:2 + len(operacion)] == operacion for c in run.calls) == 1
    if inversa:
        assert snapshot(deploy.claude_dir) == antes
        assert not run.instalado and not run.marketplace
    else:
        assert run.instalado and not deploy.agents_dir.exists()


def test_rollback_desinstala_install_interrumpido_sin_marca(deploy):
    run = ClaudeFalso(deploy)
    def crash(cmd, *, env):
        result = run(cmd, env=env)
        if cmd[2] == "install":
            raise RuntimeError("respuesta perdida")
        return result
    with pytest.raises(RuntimeError):
        corte(deploy, crash)
    assert run.instalado
    assert rollback(deploy, run)[0] == "OK"
    assert not run.instalado and not run.marketplace
    assert migracion.fase(deploy) == "legacy"


def test_fase_default_es_legacy(env):
    assert not (env.state_dir / "migracion").exists()
    assert migracion.fase(env) == "legacy"


@pytest.mark.parametrize("valor", ["unknown", "", None])
def test_set_fase_valida(env, valor):
    with pytest.raises(ValueError):
        migracion.set_fase(env, valor)
    assert not (env.state_dir / "migracion").exists()


def test_set_y_get_fase(env):
    migracion.set_fase(env, "plugin")
    assert migracion.fase(env) == "plugin"
    migracion.set_fase(env, "legacy")
    assert migracion.fase(env) == "legacy"


def test_fases_independientes_por_claude_dir(env, tmp_path):
    env2 = Paths(home=env.home, claude_dir=tmp_path / "otra")
    env2.claude_dir.mkdir()
    migracion.set_fase(env, "plugin")
    migracion.set_fase(env2, "legacy")
    assert migracion.fase(env) == "plugin"
    assert migracion.fase(env2) == "legacy"


def test_fase_symlink_comparte_estado_y_persiste_metadatos(env, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(env.claude_dir, target_is_directory=True)
    env2 = Paths(home=env.home, claude_dir=alias)
    migracion.set_fase(env2, "plugin")
    assert migracion.fase(env) == "plugin"
    sha = hashlib.sha256(str(env.claude_dir.resolve()).encode()).hexdigest()[:12]
    folder = env.state_dir / "migracion"
    data = json.loads((folder / f"{sha}.json").read_text())
    assert data["fase"] == "plugin"
    assert data["claude_dir"] == str(env.claude_dir.resolve())
    assert isinstance(data["ts"], float)
    assert not list(folder.glob("*.tmp"))


def test_paths_plugin_respetan_entorno(env, monkeypatch):
    monkeypatch.setenv("ORQUESTA_HOME", str(env.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(env.claude_dir))
    paths = default_paths()
    assert paths.plugin_root == env.home / "plugin"
    assert paths.plugin_dir == env.home / "plugin" / "orquesta"
    assert paths.plugin_manifest == env.home / "plugin" / ".orquesta-manifest.json"
    assert paths.legacy_manifest == paths.manifest == env.agents_dir / ".orquesta-manifest.json"


def test_corte_con_plugin_deshabilitado_lo_habilita_antes_de_retirar(deploy):
    run = ClaudeFalso(deploy)
    run.marketplace = True
    deploy.plugins_dir.mkdir(exist_ok=True)
    (deploy.plugins_dir / "known_marketplaces.json").write_text(json.dumps(
        {"orquesta-local": {"source": {"source": "directory", "path": str(deploy.plugin_root)}}}))
    run.instalado, run.habilitado = True, False
    assert corte(deploy, run)[0] == "OK"
    assert ["claude", "plugin", "enable", "orquesta@orquesta-local", "-s", "user"] in run.calls
    assert run.habilitado and not deploy.agents_dir.exists()


def test_corte_no_retira_si_el_plugin_desaparece_entre_corridas(deploy, monkeypatch):
    from orquesta import migracion_cmd
    run = ClaudeFalso(deploy)
    marcar = migracion_cmd._marcar
    def interrumpir(paths, diario, nombre):
        marcar(paths, diario, nombre)
        if nombre == "plugin_verificado":
            raise RuntimeError("interrupción")
    with monkeypatch.context() as m:
        m.setattr(migracion_cmd, "_marcar", interrumpir)
        with pytest.raises(RuntimeError):
            corte(deploy, run)
    run.habilitado = False  # el usuario lo deshabilitó entre corridas
    antes = snapshot(deploy.agents_dir), deploy.claude_md.read_bytes()
    estado, datos = corte(deploy, run)
    assert estado == "ERROR" and "no se retira" in datos["motivo"]
    assert antes == (snapshot(deploy.agents_dir), deploy.claude_md.read_bytes())


def test_marketplace_ajeno_frena_con_instrucciones_y_el_rollback_no_lo_borra(deploy, tmp_path):
    run = ClaudeFalso(deploy)
    deploy.plugins_dir.mkdir(exist_ok=True)
    ajeno = {"orquesta-local": {"source": {"source": "directory", "path": str(tmp_path / "otro")}}}
    (deploy.plugins_dir / "known_marketplaces.json").write_text(json.dumps(ajeno))
    estado, datos = corte(deploy, run)
    assert estado == "ERROR" and "marketplace remove orquesta-local" in datos["motivo"]
    assert json.loads((deploy.plugins_dir / "known_marketplaces.json").read_text()) == ajeno
    assert rollback(deploy, run)[0] == "OK"
    assert json.loads((deploy.plugins_dir / "known_marketplaces.json").read_text()) == ajeno
