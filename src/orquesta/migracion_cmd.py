"""Corte y rollback del deploy, con backup fijado y diario reanudable.

El rollback conserva las ediciones externas de CLAUDE.md; las ediciones DENTRO
del bloque de orquesta se pierden al recuperar el bloque del backup.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from filelock import Timeout

from orquesta import cli_json, confirmar as confirmacion, generate, migracion
from orquesta.claude_md import START, END, apply_block, extract_inner, find_block, remove_block
from orquesta.paths import Paths
from orquesta.plugins import Runner

PLUGIN = "orquesta@orquesta-local"


def run_claude(cmd: list[str], *, env: dict) -> tuple[int, str, str]:
    """Ejecuta la CLI en la configuración indicada, nunca heredando el destino."""
    try:
        # cwd neutro: `claude plugin list` también mira los plugins del proyecto del cwd.
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=tempfile.gettempdir(),
                              stdin=subprocess.DEVNULL, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def _ejecutar(paths: Paths, run: Runner, *args: str) -> str:
    """Inyecta CLAUDE_CONFIG_DIR en todas las invocaciones del runner."""
    code, out, err = run(["claude", "plugin", *args],
                         env=dict(os.environ, CLAUDE_CONFIG_DIR=str(paths.claude_dir)))
    if code:
        raise ValueError(f"claude plugin {' '.join(args)} falló: {(err or out).strip()}")
    return out


def _plugin_generado(paths: Paths) -> bool:
    """Reconoce el descriptor estándar del generador y el descriptor directo."""
    return any(p.is_file() for p in (paths.plugin_dir / ".claude-plugin" / "plugin.json",
                                     paths.plugin_dir / "plugin.json"))


def _estado_plugin(paths: Paths, run: Runner) -> str:
    """Estado real según `claude plugin list --json`: "ausente", "deshabilitado", "otra_carpeta" u "ok".

    Solo cuenta `readFromFolder` (la carpeta desde la que Claude Code lee el plugin); `installPath` es la
    copia en plugins/cache y no prueba nada.
    """
    data = json.loads(_ejecutar(paths, run, "list", "--json"))
    entries = data.get("plugins", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return "ausente"
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("id") != PLUGIN:
            continue
        origen = entry.get("readFromFolder")
        if not (isinstance(origen, str) and Path(origen).resolve() == paths.plugin_dir.resolve()):
            return "otra_carpeta"
        return "ok" if entry.get("enabled") is True else "deshabilitado"
    return "ausente"


def _plugin_instalado(paths: Paths, run: Runner) -> bool:
    """Instalado, habilitado y leyendo de paths.plugin_dir."""
    return _estado_plugin(paths, run) == "ok"


def _marketplace_presente(paths: Paths) -> bool:
    """Estado real del marketplace local (lo que escribe `claude plugin marketplace add`)."""
    try:
        data = json.loads((paths.claude_dir / "plugins" / "known_marketplaces.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict) or "orquesta-local" not in data:
        return False
    origen = ((data["orquesta-local"] or {}).get("source") or {}).get("path")
    if not (isinstance(origen, str) and Path(origen).resolve() == paths.plugin_root.resolve()):
        raise ValueError(f"ya existe un marketplace `orquesta-local` que apunta a {origen!r}, no a "
                         f"{paths.plugin_root}: sacalo con `claude plugin marketplace remove orquesta-local` "
                         "y volvé a correr el corte")
    return True


def _diario_path(paths: Paths) -> Path:
    """Diario separado del archivo de fase existente, uno por claude_dir."""
    sha = migracion._sha12_claude_dir(paths.claude_dir)
    return paths.state_dir / "migracion" / f"diario-{sha}.json"


def _leer_diario(paths: Paths) -> dict:
    """Lee el diario sin crear estado durante una consulta."""
    try:
        diario = json.loads(_diario_path(paths).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"fase": migracion.fase(paths), "claude_dir": str(paths.claude_dir.resolve()), "pasos": {}}
    if not isinstance(diario, dict) or not isinstance(diario.get("pasos"), dict):
        raise ValueError("el diario de migración está corrupto")
    if diario.get("claude_dir") != str(paths.claude_dir.resolve()):
        raise ValueError("el diario corresponde a otra configuración de Claude")
    return diario


def _guardar(paths: Paths, diario: dict) -> None:
    """Persiste el diario por tmp + rename, con el lock compartido ya tomado."""
    path = _diario_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(diario, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _marcar(paths: Paths, diario: dict, paso: str) -> None:
    """Registra un paso completo; los pasos inversos van en su propia sección."""
    section = "rollback" if paso.startswith("rollback_") else "pasos"
    diario.setdefault(section, {})[paso] = time.time()
    _guardar(paths, diario)


def _backup_fijado(paths: Paths, diario: dict) -> Path:
    """Copia el legacy fuera de backups/, publicándolo solo cuando está completo."""
    if "backup" not in diario:
        diario["backup"] = str(_diario_path(paths).parent / f"backup-{datetime.now():%Y%m%d-%H%M%S-%f}")
        _guardar(paths, diario)
    backup = Path(diario["backup"])
    if backup.is_dir():
        return backup
    tmp = backup.with_name(backup.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    if paths.agents_dir.is_dir():
        shutil.copytree(paths.agents_dir, tmp / "agents")
    if paths.claude_md.is_file():
        text = generate._read(paths.claude_md)
        find_block(text)  # No retirar nada si los marcadores están rotos.
        shutil.copy2(paths.claude_md, tmp / "CLAUDE.md")
    if paths.legacy_manifest.is_file():
        shutil.copy2(paths.legacy_manifest, tmp / "legacy-manifest.json")
    tmp.replace(backup)
    return backup


def _generar_plugin(paths: Paths) -> None:
    """Reutiliza generate sin confirmaciones, commits ni modificaciones del legacy."""
    plan = generate.build_plan(paths)
    if plan.fase != "legacy":
        raise ValueError("el plugin debe generarse en fase legacy antes del corte")
    if plan.errors or plan.violations:
        raise generate.GenerateError("\n".join(plan.errors + list(map(str, plan.violations))))
    generate._verify_fresh(plan)
    generate._recuperar_plugin(paths)
    generate._write_plugin(plan)


def _confirmar(accion: str, paths: Paths, confirmar: bool, generacion: str | None, leer_tty):
    """Devuelve un resultado pendiente salvo confirmación humana exacta."""
    comando = f"orquesta migrar {accion} --confirm --json"
    pendiente = cli_json.NECESITO_CONFIRMACION, {"comando": comando}
    if not confirmar:
        return pendiente
    detalle = (f"{accion.capitalize()} de {paths.claude_dir} con plugin {paths.plugin_dir}. "
               + ("Se retirarán los agentes legacy y el bloque de orquesta; se guardará un backup fijado."
                  if accion == "corte" else
                  "Se restaurará el backup legacy y se desinstalará el plugin. "
                  "Las ediciones DENTRO del bloque de orquesta se pierden."))
    if generacion:
        detalle += f" Generación: {generacion}."
    try:
        if not confirmacion.pedir_confirmacion(accion, comando, detalle=detalle, leer_tty=leer_tty):
            return pendiente
    except confirmacion.SinTTY as exc:
        return cli_json.NECESITO_CONFIRMACION, {"comando": exc.comando_sugerido}
    return None


def migrar_estado(paths: Paths, run: Runner) -> dict:
    """Consulta fase, deploys y pasos completados sin escribir archivos."""
    try:
        instalado = _plugin_instalado(paths, run)
    except (OSError, ValueError):
        instalado = False
    return {"fase": migracion.fase(paths), "plugin_generado": _plugin_generado(paths),
            "plugin_instalado": instalado, "deploy_viejo": paths.agents_dir.exists(),
            "diario": _leer_diario(paths)["pasos"]}


def migrar_corte(paths: Paths, run: Runner, confirmar: bool, generacion: str | None = None,
                 *, leer_tty=None) -> tuple[str, dict]:
    """Reanuda el corte confirmado; verifica el origen del plugin antes de retirar legacy."""
    pendiente = _confirmar("corte", paths, confirmar, generacion, leer_tty)
    if pendiente:
        return pendiente
    try:
        with generate.get_lock(paths):
            diario = _leer_diario(paths)
            if diario.get("rollback"):
                if "rollback_fase_restaurada" not in diario["rollback"]:
                    raise ValueError("hay un rollback pendiente: completalo antes de otro corte")
                diario = {"fase": "legacy", "claude_dir": str(paths.claude_dir.resolve()), "pasos": {}}
            pasos = diario["pasos"]
            if "backup_ok" not in pasos:
                _backup_fijado(paths, diario)
                _marcar(paths, diario, "backup_ok")
            if "plugin_generado" not in pasos:
                _generar_plugin(paths)  # siempre: un plugin viejo o editado a mano no se instala
                _marcar(paths, diario, "plugin_generado")
            if "plugin_instalado" not in pasos:
                # Se reconcilia con el estado real, no con el diario: si una corrida anterior se cortó
                # después de que Claude Code completó la operación, no se repite.
                agregado_ahora = False
                if not _marketplace_presente(paths):
                    diario["marketplace_agregando"] = time.time()  # intención: si se corta acá, es nuestro
                    _guardar(paths, diario)
                    _ejecutar(paths, run, "marketplace", "add", str(paths.plugin_root))
                    agregado_ahora = True
                    diario["marketplace_agregado"] = time.time()  # solo si lo agregó orquesta
                    _guardar(paths, diario)
                estado_plugin = _estado_plugin(paths, run)
                if estado_plugin == "otra_carpeta":
                    raise ValueError(f"{PLUGIN} está instalado desde otra carpeta: desinstalalo con "
                                     f"`claude plugin uninstall {PLUGIN} -s user` y volvé a correr el corte")
                if estado_plugin == "deshabilitado":
                    _ejecutar(paths, run, "enable", PLUGIN, "-s", "user")
                elif estado_plugin == "ausente":
                    try:
                        _ejecutar(paths, run, "install", PLUGIN, "-s", "user")
                    except ValueError:
                        if agregado_ahora:  # no dejar un marketplace a medias si el install falla
                            run(["claude", "plugin", "marketplace", "remove", "orquesta-local"],
                                env=dict(os.environ, CLAUDE_CONFIG_DIR=str(paths.claude_dir)))
                            diario.pop("marketplace_agregado", None)
                            _guardar(paths, diario)
                        raise
                _marcar(paths, diario, "plugin_instalado")
            if "plugin_verificado" not in pasos:
                if not _plugin_instalado(paths, run):
                    raise ValueError(f"orquesta no está instalado con Read from: {paths.plugin_dir.resolve()}")
                _marcar(paths, diario, "plugin_verificado")
            if "legacy_retirado" not in pasos:
                # Se verifica de nuevo con el estado real justo antes de retirar: la marca del diario puede
                # ser de una corrida anterior y el plugin pudo desinstalarse o deshabilitarse en el medio.
                if not _plugin_instalado(paths, run):
                    raise ValueError("el plugin ya no está instalado y habilitado: no se retira el deploy viejo")
                original = generate._read(paths.claude_md)
                nuevo = remove_block(original) if original is not None else None
                if paths.agents_dir.exists():
                    shutil.rmtree(paths.agents_dir)
                if nuevo != original:
                    generate._write(paths, generate.Change(paths.claude_md, original, nuevo))
                _marcar(paths, diario, "legacy_retirado")
            migracion.set_fase(paths, "plugin")
            diario["fase"] = "plugin"
            _guardar(paths, diario)
            return cli_json.OK, diario
    except (OSError, ValueError, generate.GenerateError, Timeout) as exc:
        return cli_json.ERROR, {"motivo": str(exc)}


def _restaurar_bloque(paths: Paths, backup: Path) -> None:
    """Reinserta el bloque en su ubicación previa, conservando ediciones externas."""
    saved = generate._read(backup / "CLAUDE.md")
    span = find_block(saved) if saved is not None else None
    if span is None:
        return
    actual = generate._read(paths.claude_md) or ""
    if find_block(actual) is None:
        # Traducir la posición de inserción usando el texto externo del backup.
        externo = remove_block(saved)
        pos = span[0]
        for match in SequenceMatcher(None, externo, actual, autojunk=False).get_matching_blocks():
            if match.a <= pos <= match.a + match.size:
                pos = match.b + pos - match.a
                break
        else:
            pos = min(pos, len(actual))
        actual = actual[:pos] + START + END + actual[pos:]
    nuevo = apply_block(actual, extract_inner(saved))
    # Recuperar también el formato interno exacto del backup (incluye CRLF mixtos).
    i, j = find_block(nuevo)
    nuevo = nuevo[:i] + saved[span[0]:span[1]] + nuevo[j:]
    original = generate._read(paths.claude_md)
    if nuevo != original:
        generate._write(paths, generate.Change(paths.claude_md, original, nuevo))


def _verificar_legacy(paths: Paths, backup: Path) -> None:
    """Compara los agentes restaurados con los hashes del manifiesto fijado."""
    path = backup / "legacy-manifest.json"
    if not path.exists():
        if paths.agents_dir.exists() and any(paths.agents_dir.rglob("*.md")):
            raise ValueError("falta el manifiesto legacy del backup; no se desinstaló el plugin")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    agents = data.get("agents") if isinstance(data, dict) else None
    if not isinstance(agents, dict):
        raise ValueError("manifiesto legacy del backup corrupto")
    for name, entry in agents.items():
        filename = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"ruta de agente inválida en el manifiesto: {name}")
        content = generate._read(paths.agents_dir / filename)
        if content is None or generate.manifest.sha256(content) != entry.get("sha256"):
            raise ValueError(f"hash legacy no coincide: {name}; no se desinstaló el plugin")
    esperados = {entry["file"] for entry in agents.values()}
    if {p.name for p in paths.agents_dir.glob("*.md")} != esperados:
        raise ValueError("hay agentes legacy fuera del manifiesto; no se desinstaló el plugin")


def migrar_rollback(paths: Paths, run: Runner, confirmar: bool, generacion: str | None = None,
                    *, leer_tty=None) -> tuple[str, dict]:
    """Restaura y verifica legacy antes de desinstalar; pierde las ediciones dentro del bloque."""
    pendiente = _confirmar("rollback", paths, confirmar, generacion, leer_tty)
    if pendiente:
        return pendiente
    try:
        with generate.get_lock(paths):
            diario = _leer_diario(paths)
            if not diario.get("backup") or not diario["pasos"].get("backup_ok"):
                raise ValueError("no hay un backup fijado de corte para restaurar")
            pasos = diario.setdefault("rollback", {})
            if "rollback_fase_restaurada" in pasos:
                return cli_json.OK, diario
            backup = Path(diario["backup"])
            if not backup.is_dir():
                raise ValueError(f"falta el backup fijado: {backup}")
            if "rollback_legacy_restaurado" not in pasos:
                if paths.agents_dir.exists():
                    shutil.rmtree(paths.agents_dir)
                if (backup / "agents").is_dir():
                    shutil.copytree(backup / "agents", paths.agents_dir)
                _restaurar_bloque(paths, backup)
                _marcar(paths, diario, "rollback_legacy_restaurado")
            _verificar_legacy(paths, backup)
            if "rollback_legacy_verificado" not in pasos:
                _marcar(paths, diario, "rollback_legacy_verificado")
            if "rollback_plugin_desinstalado" not in pasos:
                if _estado_plugin(paths, run) in ("ok", "deshabilitado"):  # estado real: cubre un install sin marca
                    _ejecutar(paths, run, "uninstall", PLUGIN, "-s", "user")
                _marcar(paths, diario, "rollback_plugin_desinstalado")
            if "rollback_marketplace_retirado" not in pasos:
                if (diario.get("marketplace_agregado") or diario.get("marketplace_agregando")) \
                        and _marketplace_presente(paths):
                    _ejecutar(paths, run, "marketplace", "remove", "orquesta-local")
                _marcar(paths, diario, "rollback_marketplace_retirado")
            migracion.set_fase(paths, "legacy")
            diario["fase"] = "legacy"
            _marcar(paths, diario, "rollback_fase_restaurada")
            return cli_json.OK, diario
    except (OSError, ValueError, generate.GenerateError, Timeout) as exc:
        return cli_json.ERROR, {"motivo": str(exc)}
