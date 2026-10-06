"""CLI `orquesta`.

    orquesta [ui] [--port N] [--no-browser]   abre la UI local (http://127.0.0.1:8787)
    orquesta generate [--dry-run] [-m MSG] [--confirm AGENTE ...]
    orquesta run AGENTE [--cwd DIR] [--task SLUG] [--prompt-file F | stdin] [--ping]
    orquesta doctor [--json] [--no-cache] [--skip-live] [--only ID]
    orquesta wt list | new | clean RAMA [--force] | prune

Códigos de salida de `generate`: 0 ok/sin cambios, 1 errores, 3 requiere confirmación.
`run` usa los códigos de runner.EXIT_CODES.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from orquesta.paths import default_paths

RESTART_NOTE = "Reiniciá la sesión de Claude Code para cargar los cambios."


def cmd_generate(args: argparse.Namespace) -> int:
    from orquesta.generate import GenerateError, TestsFailed, apply_plan, build_plan

    paths = default_paths()
    plan = build_plan(paths, confirmations=args.confirm or [])
    if plan.errors:
        print("❌ Hay errores en el registro; no se aplicó nada:")
        print("\n".join(f"  - {e}" for e in plan.errors))
        return 1
    if plan.empty:
        print("✅ Todo sincronizado: no hay cambios para aplicar.")
        return 0
    print(plan.diff())
    print("Resumen:")
    print("\n".join(f"  {s}" for s in plan.summary))
    if plan.violations:
        print("\n⛔ Estos cambios requieren confirmación explícita del usuario:")
        print("\n".join(f"  - {v}" for v in plan.violations))
        return 3
    if args.dry_run:
        print("\n(dry-run: no se escribió nada)")
        return 0
    try:
        res = apply_plan(plan, args.message or "Aplicar cambios del registro")
    except TestsFailed as exc:
        print(f"\n❌ {exc}")
        return 1
    except GenerateError as exc:
        print(f"\n❌ {exc}")
        return 1
    print(f"\n✅ Aplicado (commit {res.commit}). Backup en {paths.pretty(res.backup)}.")
    print(RESTART_NOTE)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from orquesta import runner

    paths = default_paths()
    if args.ping:
        result = runner.ping(paths, args.agent)
    else:
        if args.prompt_file:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        elif not sys.stdin.isatty():
            prompt = sys.stdin.read()
        else:
            print("ESTADO: ERROR — pasá el pedido por stdin (heredoc) o con --prompt-file")
            return runner.EXIT_CODES["ERROR"]
        result = runner.run(runner.RunRequest(agent=args.agent, cwd=Path(args.cwd or os.getcwd()),
                                              prompt=prompt, task=args.task,
                                              timeout_s=args.timeout), paths)
    print(runner.format_result(result))
    return result.exit_code


def cmd_ui(args: argparse.Namespace) -> int:
    import socket
    import threading
    import webbrowser

    port = args.port
    with socket.socket() as probe:
        # Igual que uvicorn: sin esto, conexiones viejas en FIN-WAIT/TIME-WAIT dan falso "ocupado".
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            print(f"❌ El puerto {port} está ocupado. Probá con `orquesta ui --port {port + 1}`.")
            return 1
    import uvicorn

    from orquesta.ui.app import create_app

    url = f"http://127.0.0.1:{port}/"
    app = create_app(default_paths(), port=port)
    print(f"orquesta UI en {url} (Ctrl+C para cerrar)")
    if not args.no_browser:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    import json

    from orquesta import doctor

    report = doctor.run_doctor(default_paths(), use_cache=not args.no_cache, live=not args.skip_live,
                               only=args.only)
    if args.json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(report.render())
    return 1 if report.overall == "fail" else 0


def cmd_plugins(args: argparse.Namespace) -> int:
    from orquesta import plugins
    from orquesta.config import load_registry

    paths = default_paths()
    if args.pl_cmd in ("install", "uninstall") and not args.yes:
        print("⛔ Instalar o desinstalar plugins toca la config global de Claude Code: agregá --yes "
              "para confirmar explícitamente.")
        return 2
    try:
        if args.pl_cmd == "install":
            info = plugins.install(paths, args.repo, args.plugin_id)
            print(f"✅ Instalado {info.id} {info.version} (commit {info.sha[:12]} = auditado). "
                  f"{RESTART_NOTE}")
        elif args.pl_cmd == "uninstall":
            plugins.uninstall(paths, args.plugin_id)
            print(f"✅ Desinstalado {args.plugin_id}. {RESTART_NOTE}")
        else:
            inst = plugins.installed(paths)
            try:
                reg = load_registry(paths)
            except Exception:
                reg = None
            if not inst:
                print("No hay plugins instalados.")
            for pid, info in sorted(inst.items()):
                used = plugins.users(reg, pid) if reg else []
                print(f"{pid}  {info.version}  commit {info.sha[:7] or '?'}")
                print(f"    skills: {', '.join(info.skills) or '-'}")
                print(f"    mcp: {', '.join(info.mcp_tools) or '-'}")
                print(f"    lo usan: {', '.join(used) or 'ningún agente de orquesta'}")
    except plugins.PluginError as exc:
        print(f"❌ {exc}")
        return 1
    return 0


def cmd_wt(args: argparse.Namespace) -> int:
    from orquesta import worktrees

    paths = default_paths()
    try:
        if args.wt_cmd == "new":
            wt = worktrees.create(paths, repo=Path(args.repo).resolve(), agent=args.agent,
                                  task=args.task)
            print(f"worktree: {wt.path}\nrama: {wt.branch}\nbase: {wt.base_sha[:12]}")
        elif args.wt_cmd == "clean":
            worktrees.clean(paths, args.branch, force=args.force)
            print(f"✅ Worktree y rama {args.branch} eliminados.")
        elif args.wt_cmd == "prune":
            gone = worktrees.prune(paths)
            print("✅ Registro limpio." if not gone else "Sacados del registro: " + ", ".join(gone))
        else:
            items = worktrees.list_worktrees(paths)
            if not items:
                print("No hay worktrees de orquesta abiertos.")
                return 0
            orphan = {o.branch: o.reason for o in worktrees.orphans(paths)}
            for w in items:
                mark = f"  ⚠ {orphan[w.branch]}" if w.branch in orphan else ""
                print(f"{w.branch}  ({w.agent})  {w.path}{mark}")
    except worktrees.WorktreeError as exc:
        print(f"❌ {exc}")
        return 1
    return 0


def _emit_tarea(comando: str, estado: str, datos=None, motivo: str | None = None) -> int:
    from orquesta import cli_json
    return cli_json.emit(cli_json.sobre(comando, estado, datos=datos,
                                       errores=[motivo] if motivo else []), stream=sys.stdout)


def _comando_confirmado(args: argparse.Namespace, repo: Path) -> str:
    import shlex
    partes = ["orquesta", args.cmd, args.branch, "--repo", str(repo.resolve()), "--confirm"]
    if getattr(args, "forzar", False):
        partes.append("--forzar")
    return shlex.join(partes)


def _confirmar_tarea(args: argparse.Namespace, repo: Path, *, leer_tty=None) -> int | None:
    from orquesta import cli_json, confirmar
    comando = _comando_confirmado(args, repo)
    if not args.confirm:
        return _emit_tarea(args.cmd, cli_json.NECESITO_CONFIRMACION, {"comando": comando})
    try:
        coincide = confirmar.pedir_confirmacion(texto_esperado=args.branch, comando=comando,
                                                 **({"leer_tty": leer_tty} if leer_tty is not None else {}))
    except confirmar.SinTTY:
        return _emit_tarea(args.cmd, cli_json.NECESITO_CONFIRMACION, {"comando": comando})
    if not coincide:
        return _emit_tarea(args.cmd, cli_json.ERROR, motivo="confirmación cancelada")
    return None


def cmd_review(args: argparse.Namespace, *, executor=None) -> int:
    from orquesta import chat, cli_json, revision, tareas, worktrees

    paths = default_paths()
    try:
        wt = worktrees._find(paths, args.branch, args.repo)
        pid = worktrees.project_id(wt.repo)
        registro = tareas.obtener(pid, wt.branch, str(wt.repo), paths=paths)
        if registro is None:
            destino = worktrees._git(wt.repo, "symbolic-ref", "--short", "HEAD")
            registro = tareas.registrar(pid, {
                "repo": str(wt.repo), "rama": wt.branch, "agente": wt.agent, "destino": destino,
                "base_sha": worktrees._git(wt.repo, "merge-base", wt.branch, destino),
            }, paths=paths)
        if registro["status"] != "abierto":
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO, motivo="la tarea no está abierta")
        sha = worktrees._branch_tip(wt.repo, wt.branch)
        if worktrees._git(wt.path, "rev-parse", "HEAD") != sha or worktrees._git(wt.path, "status", "--porcelain"):
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO, motivo="el worktree debe estar limpio y en la rama de la tarea")
        # Invalidar la evidencia anterior incluso si falla el executor o no devuelve un veredicto.
        tareas.actualizar(pid, wt.branch, {"sha_revisado": None, "tests_sha": None,
                          "tests_ok": None, "veredicto": None, "revisor": None}, str(wt.repo), paths=paths)
        revisor = revision.elegir_revisor(chat.mf.load(paths) or {}, wt.agent)
        if revisor is None:
            return _emit_tarea(args.cmd, cli_json.ERROR, motivo="no hay un revisor de otra familia disponible")
        tests = worktrees.run_tests(wt, registro.get("tests_cmd"))
        tests_sha = worktrees._git(wt.path, "rev-parse", "HEAD") if tests is not None else None
        evidencia = (f"Resultado de los tests del proyecto: {'PASARON' if tests['ok'] else 'FALLARON'} "
                     f"(`{tests['cmd']}`).\n```\n{tests['salida']}\n```" if tests is not None else
                     "No hay comando de tests configurado para el proyecto (.orquesta/proyecto.toml).")
        diff = worktrees._git(wt.repo, "diff", f"{registro['base_sha']}..{sha}")[:chat.REVIEW_DIFF_CHARS]
        project = {"name": wt.repo.name, "dir": str(wt.path)}
        prompt = revision.prompt_review(project["name"], project["dir"], diff, evidencia)
        reply = (executor or chat.default_executor)(paths, project, revisor, prompt)
        veredicto = revision.parsear_veredicto(reply.text) if not reply.error else None
        registro = tareas.actualizar(pid, wt.branch, {
            "sha_revisado": sha if not reply.error else None, "tests_sha": tests_sha,
            "tests_ok": tests["ok"] if tests is not None else None,
            "tests_cmd": tests["cmd"] if tests is not None else registro.get("tests_cmd"),
            "veredicto": veredicto, "revisor": revisor,
            "confianza_autor": registro.get("confianza_autor"),
        }, str(wt.repo), paths=paths)
        if reply.error or veredicto is None:
            return _emit_tarea(args.cmd, cli_json.ERROR, registro,
                               reply.error or "la review no devolvió un veredicto válido")
        return _emit_tarea(args.cmd, cli_json.OK, registro)
    except (worktrees.WorktreeError, OSError, ValueError) as exc:
        return _emit_tarea(args.cmd, cli_json.ERROR, motivo=str(exc))


def cmd_merge(args: argparse.Namespace, *, leer_tty=None) -> int:
    from orquesta import cli_json, revision, tareas, worktrees

    paths = default_paths()
    try:
        wt = worktrees._find(paths, args.branch, args.repo)
        pid = worktrees.project_id(wt.repo)
        registro = tareas.obtener(pid, wt.branch, str(wt.repo), paths=paths)
        if not args.confirm:
            return _confirmar_tarea(args, wt.repo, leer_tty=leer_tty)
        if registro is None:
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO,
                               motivo=f"no hay review registrada: corré `orquesta review {wt.branch}`")
        if registro["status"] != "abierto":
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO, motivo="la tarea no está abierta")
        sha = worktrees._branch_tip(wt.repo, wt.branch)
        if sha != registro.get("sha_revisado"):
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO,
                               motivo=f"review vieja: corré `orquesta review {wt.branch}`")
        # tests_sha solo se exige igual si hubo tests (tests_ok no es None)
        if registro.get("tests_ok") is not None and sha != registro.get("tests_sha"):
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO,
                               motivo=f"review vieja: corré `orquesta review {wt.branch}`")
        razones = revision.razones_bloqueo(tareas.a_registro_review(registro))
        if razones and not args.forzar:
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO, {"razones": razones}, "\n".join(razones))
        resultado = _confirmar_tarea(args, wt.repo, leer_tty=leer_tty)
        if resultado is not None:
            return resultado
        try:
            if not worktrees._is_merged(wt.repo, registro["base_sha"]):
                raise worktrees.WorktreeError("el SHA base ya no es ancestro del destino")
            datos = worktrees.merge_sha(paths, wt.branch, sha, registro["destino"], repo=wt.repo)
        except worktrees.WorktreeError as exc:
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO, motivo=str(exc))
        tareas.actualizar(pid, wt.branch, {"status": "mergeado"}, str(wt.repo), paths=paths)
        return _emit_tarea(args.cmd, cli_json.OK, datos)
    except (worktrees.WorktreeError, OSError, ValueError) as exc:
        return _emit_tarea(args.cmd, cli_json.ERROR, motivo=str(exc))


def cmd_descartar(args: argparse.Namespace, *, leer_tty=None) -> int:
    from orquesta import cli_json, tareas, worktrees

    paths = default_paths()
    try:
        wt = worktrees._find(paths, args.branch, args.repo)
        pid = worktrees.project_id(wt.repo)
        registro = tareas.obtener(pid, wt.branch, str(wt.repo), paths=paths)
        resultado = _confirmar_tarea(args, wt.repo, leer_tty=leer_tty)
        if resultado is not None:
            return resultado
        worktrees.discard(paths, wt.branch, repo=wt.repo)
        if registro is None:
            tareas.registrar(pid, {"repo": str(wt.repo), "rama": wt.branch, "agente": wt.agent,
                                  "base_sha": wt.base_sha, "status": "descartado"}, paths=paths)
        else:
            tareas.actualizar(pid, wt.branch, {"status": "descartado"}, str(wt.repo), paths=paths)
        return _emit_tarea(args.cmd, cli_json.OK, {"rama": wt.branch, "status": "descartado"})
    except (worktrees.WorktreeError, OSError, ValueError) as exc:
        return _emit_tarea(args.cmd, cli_json.ERROR, motivo=str(exc))


def cmd_tareas(args: argparse.Namespace) -> int:
    from orquesta import cli_json, revision, tareas, worktrees

    paths = default_paths()
    try:
        repo = worktrees.git_toplevel(Path.cwd())
        if repo is None:
            raise worktrees.WorktreeError("el directorio actual no es un repo git")
        items = worktrees.list_worktrees(paths)
        repo = next((w.repo for w in items if w.path.resolve() == repo.resolve()), repo)
        registros = tareas.listar(worktrees.project_id(repo), "abierto", paths=paths)
        if args.linea:
            reviews = sum(r.get("veredicto") is not None for r in registros)
            bloqueadas = sum(bool(revision.razones_bloqueo(tareas.a_registro_review(r))) for r in registros)
            print(f"{len(registros)} tareas abiertas ({reviews} en review, {bloqueadas} bloqueadas)")
            return 0
        return _emit_tarea(args.cmd, cli_json.OK, {
            "tareas": registros, "worktrees": [w.to_json() for w in items if w.repo.resolve() == repo.resolve()],
        })
    except (worktrees.WorktreeError, OSError, ValueError) as exc:
        if args.linea:
            print("Error: " + str(exc).replace("\n", " "))
            return 1
        return _emit_tarea(args.cmd, cli_json.ERROR, motivo=str(exc))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orquesta", description="Ecosistema multi-modelo para Claude Code.")
    sub = p.add_subparsers(dest="cmd")

    u = sub.add_parser("ui", help="abrir la UI local (es lo que corre `orquesta` sin argumentos)")
    u.add_argument("--port", type=int, default=int(os.environ.get("ORQUESTA_PORT", "8787")))
    u.add_argument("--no-browser", action="store_true", help="no abrir el navegador")
    u.set_defaults(func=cmd_ui)

    g = sub.add_parser("generate", help="orquesta.yaml → agentes .md y bloque de CLAUDE.md")
    g.add_argument("--dry-run", action="store_true", help="mostrar el diff sin escribir")
    g.add_argument("-m", "--message", help="mensaje del commit y de la entrada del CHANGELOG")
    g.add_argument("--confirm", action="append", metavar="AGENTE",
                   help="confirmación EXPLÍCITA del usuario para un cambio protegido/escalada")
    g.set_defaults(func=cmd_generate)

    r = sub.add_parser("run", help="delegar en el backend de un agente (lee el pedido de stdin)")
    r.add_argument("agent")
    r.add_argument("--cwd", help="directorio del proyecto (default: el actual)")
    r.add_argument("--task", help="slug de la tarea (obligatorio para agentes con worktree)")
    r.add_argument("--prompt-file", help="leer el pedido de este archivo en vez de stdin")
    r.add_argument("--ping", action="store_true", help="ping mínimo por el backend del agente")
    r.add_argument("--timeout", type=float, help="segundos; pisa el timeout del modo (para correr en background)")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("doctor", help="chequeos de salud del ecosistema (sin LLM)")
    d.add_argument("--json", action="store_true", help="salida JSON (para la UI)")
    d.add_argument("--no-cache", action="store_true", help="ignorar el cache (auth, pings, versiones)")
    d.add_argument("--skip-live", action="store_true", help="saltear los chequeos que gastan cuota")
    d.add_argument("--only", metavar="ID", help="solo los chequeos cuyo id empieza así")
    d.set_defaults(func=cmd_doctor)

    pl = sub.add_parser("plugins", help="plugins de Claude Code: listar, instalar (auditado), desinstalar")
    plsub = pl.add_subparsers(dest="pl_cmd")
    plsub.add_parser("list", help="instalados, sus skills/MCP y qué agentes los usan")
    pi = plsub.add_parser("install", help="instalar (exige auditoría ok del commit exacto)")
    pi.add_argument("repo", help="owner/repo de GitHub del marketplace")
    pi.add_argument("plugin_id", help="plugin@marketplace")
    pi.add_argument("--yes", action="store_true", help="confirmación explícita del usuario")
    pu = plsub.add_parser("uninstall", help="desinstalar (se niega si algún agente lo usa)")
    pu.add_argument("plugin_id")
    pu.add_argument("--yes", action="store_true", help="confirmación explícita del usuario")
    pl.set_defaults(func=cmd_plugins)

    w = sub.add_parser("wt", help="worktrees de agentes write")
    wsub = w.add_subparsers(dest="wt_cmd")
    wsub.add_parser("list", help="listar worktrees abiertos y huérfanos")
    n = wsub.add_parser("new", help="crear un worktree orquesta/<agente>/<tarea>")
    n.add_argument("--agent", required=True)
    n.add_argument("--task", required=True)
    n.add_argument("--repo", default=".")
    c = wsub.add_parser("clean", help="borrar un worktree y su rama")
    c.add_argument("branch")
    c.add_argument("--force", action="store_true", help="descartar aunque no esté mergeada")
    wsub.add_parser("prune", help="sacar del registro los worktrees que ya no existen")
    w.set_defaults(func=cmd_wt)
    for nombre, funcion in (("review", cmd_review), ("merge", cmd_merge), ("descartar", cmd_descartar)):
        comando = sub.add_parser(nombre, help=f"{nombre} una tarea registrada")
        comando.add_argument("branch", metavar="RAMA")
        comando.add_argument("--repo", help="repo de la tarea (desambigua ramas repetidas)")
        if nombre != "review":
            comando.add_argument("--confirm", action="store_true", help="pedir confirmación humana por TTY")
        if nombre == "merge":
            comando.add_argument("--forzar", action="store_true", help="ignorar bloqueadores de tests y veredicto")
        comando.set_defaults(func=funcion)
    t = sub.add_parser("tareas", help="listar tareas abiertas del proyecto actual")
    formato = t.add_mutually_exclusive_group()
    formato.add_argument("--json", action="store_true", help="salida JSON (default)")
    formato.add_argument("--linea", action="store_true", help="resumen en una sola línea de texto")
    t.set_defaults(func=cmd_tareas)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["ui"])  # `orquesta` a secas abre la UI
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
