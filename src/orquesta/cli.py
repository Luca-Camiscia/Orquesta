"""CLI `orquesta`.

    orquesta [ui] [--port N] [--no-browser]   abre la UI local (http://127.0.0.1:8787)
    orquesta generate [--dry-run] [-m MSG] [--confirm AGENTE ...]
    orquesta run AGENTE [--cwd DIR] [--task SLUG] [--prompt-file F | stdin] [--ping]
    orquesta doctor [--json] [--no-cache] [--skip-live] [--only ID]
    orquesta wt list | new | clean RAMA [--force] | prune
    orquesta review RAMA [--repo DIR] [--revisor NOMBRE]
    orquesta rrhh triage | evaluar | catalogo | sugerir | reclutador | validar | memoria | nota | abrir

Códigos de salida de `generate`: 0 ok/sin cambios, 1 errores, 3 requiere confirmación.
`run` usa los códigos de runner.EXIT_CODES.
"""
from __future__ import annotations

import argparse
import shlex
import os
import sys
from pathlib import Path

from orquesta.paths import default_paths
from orquesta import manifest as mf

RESTART_NOTE = "Reiniciá la sesión de Claude Code para cargar los cambios."


def _confirmar_humano(esperado: str, comando: str, detalle: str) -> bool:
    """Confirmación por /dev/tty para comandos de texto (no JSON). False = no se confirmó (ya avisó)."""
    from orquesta import confirmar
    try:
        if confirmar.pedir_confirmacion(texto_esperado=esperado, comando=comando, detalle=detalle):
            return True
        print("❌ Confirmación cancelada: no se aplicó nada.")
    except confirmar.SinTTY as exc:
        print(f"⛔ Necesito que lo confirmes vos: corré en tu terminal\n  {exc.comando_sugerido}")
    return False


def cmd_generate(args: argparse.Namespace) -> int:
    from orquesta.generate import GenerateError, TestsFailed, apply_plan, build_plan

    paths = default_paths()
    plan = build_plan(paths, confirmations=args.confirm or [])
    if args.confirm and not args.dry_run and not plan.errors and not plan.violations and not plan.empty:
        # --confirm solo vale si lo confirma una persona en la terminal (docs/plugin-plan.md, decisión 3).
        confirmadas = [v for v in build_plan(paths).violations if v.agent in set(args.confirm)]
        if confirmadas and not _confirmar_humano(
                " ".join(sorted({v.agent for v in confirmadas})),
                "orquesta generate " + " ".join(f"--confirm {a}" for a in args.confirm)
                + (f" -m {shlex.quote(args.message)}" if args.message else ""),
                "Aplicar cambios que requieren confirmación:\n" + "\n".join(f"- {v}" for v in confirmadas)):
            return 3
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


def cmd_migrar(args: argparse.Namespace, *, run=None, leer_tty=None) -> int:
    """Migra el deploy con runner y confirmación inyectables, y salida JSON versionada."""
    from filelock import Timeout
    from orquesta import cli_json, migracion_cmd, runner

    paths = default_paths()
    comando = f"migrar {args.subcomando}"
    try:
        off = runner.find_orquesta_off(Path.cwd()) or runner.find_orquesta_off(paths.home)
        if off:
            raise ValueError(f"orquesta está desactivada por {off}; sacá .orquesta-off para migrar")
        run = run or migracion_cmd.run_claude
        if args.subcomando == "estado":
            estado, datos = cli_json.OK, migracion_cmd.migrar_estado(paths, run)
        else:
            funcion = getattr(migracion_cmd, f"migrar_{args.subcomando}")
            estado, datos = funcion(paths, run, args.confirm, leer_tty=leer_tty)
        errores = [datos["motivo"]] if estado == cli_json.ERROR and "motivo" in datos else []
        return cli_json.emit(cli_json.sobre(comando, estado, datos=datos, errores=errores))
    except (OSError, ValueError, Timeout) as exc:
        return cli_json.emit(cli_json.sobre(comando, cli_json.ERROR, errores=[str(exc)]))


def cmd_reunion(args: argparse.Namespace) -> int:
    from orquesta import chat, cli_json, runner

    cwd = Path(args.cwd or os.getcwd()).expanduser().resolve()
    datos = {"conversation_id": None, "modo": args.modo, "titulo": args.titulo,
             "cwd": str(cwd), "mensajes": []}
    errors: list[str] = []
    avisos: list[str] = []
    estado = cli_json.OK

    def executor(paths, project, participant, prompt):
        reply = chat.default_executor(paths, project, participant, prompt)
        if reply.error:
            errors.append(reply.error)
        return reply

    off = runner.find_orquesta_off(cwd)
    if off:
        estado = cli_json.ORQUESTA_OFF
        errors.append(f"la delegación está desactivada por {off}; sacá .orquesta-off para ejecutar la reunión")
    else:
        store = None
        conv = None
        try:
            question = (sys.stdin.readline() if sys.stdin.isatty() else sys.stdin.read()).strip()
            if not question:
                raise chat.ChatError("pasá la pregunta por stdin")
            paths = default_paths()
            store = chat.Store(paths)
            project = store.project_for_dir(str(cwd))
            participants = [p.strip() for p in args.participantes.split(",") if p.strip()]
            aspectos = [a.strip() for a in args.aspectos.split(";") if a.strip()] if args.aspectos else None
            conv = store.create_conversation(project["id"], args.titulo, "reunion", participants,
                                             mode=args.modo, origen="terminal", aspectos=aspectos)
            datos.update(conversation_id=conv["id"], titulo=conv["title"])
            chat.run_turn(store, conv["id"], question, executor=executor)
        except (chat.ChatError, mf.ManifestError, OSError) as exc:
            errors.append(str(exc))
        if conv is not None:
            conv = store.get_conversation(conv["id"])
            labels = chat._labels(conv)
            for m in conv["messages"]:
                who = m["author"]
                if who != chat.USER and who not in labels:
                    labels[who] = f"Participante {chr(65 + len(labels))}"
            datos["mensajes"] = [
                {"autor": labels.get(m["author"], m["author"]), "texto": chat._redact_map(m["text"], labels),
                 "confianza": ((m.get("meta") or {}).get("contrato") or runner.parse_contract(m["text"])).get("confianza")}
                for m in conv["messages"]
            ]
            verdict = next((m for m in reversed(conv["messages"])
                            if (m.get("meta") or {}).get("rol") == "veredicto"), None)
            if verdict is not None:
                judge = verdict["author"]
                datos["juez"] = judge
                if args.modo == "critica":
                    datos["veredicto"] = chat._redact_map(verdict["text"], labels)
                try:
                    if any(chat._family(store, p) == chat._family(store, judge) for p in conv["participants"]):
                        datos["aviso"] = f"el juez ({judge}) es de una familia que participa; evaluá el veredicto con ese sesgo en cuenta"
                        avisos.append(datos["aviso"])
                except mf.ManifestError as exc:
                    errors.append(str(exc))
        if errors:
            estado = cli_json.OCUPADO if any("ESTADO: OCUPADO" in e for e in errors) else cli_json.ERROR
    if errors:
        datos["error"] = "\n".join(dict.fromkeys(errors))
    return cli_json.emit(cli_json.sobre("reunion", estado, datos=datos, errores=errors, avisos=avisos))


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
    if args.pl_cmd in ("install", "uninstall"):
        objetivo = args.plugin_id
        comando = shlex.join(["orquesta", "plugins", args.pl_cmd]
                             + ([args.repo] if args.pl_cmd == "install" else []) + [objetivo, "--yes"])
        if not _confirmar_humano(objetivo, comando,
                                 f"{'Instalar' if args.pl_cmd == 'install' else 'Desinstalar'} el plugin {objetivo} "
                                 "en la config global de Claude Code"):
            return 3
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
            if args.force:  # descarta trabajo sin mergear: lo confirma una persona, como `descartar`
                from orquesta import confirmar
                try:
                    if not confirmar.pedir_confirmacion(
                            texto_esperado=args.branch, comando=f"orquesta wt clean {args.branch} --force",
                            detalle=f"Borrar {args.branch} sin mergear: se pierden los cambios"):
                        print("❌ Confirmación cancelada.")
                        return 1
                except confirmar.SinTTY as exc:
                    print(f"⚠️  Necesito confirmación: corré en tu terminal `{exc.comando_sugerido}`")
                    return 3
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


def _confirmar_tarea(args: argparse.Namespace, repo: Path, *, leer_tty=None, detalle: str = "",
                     esperado: str | None = None) -> int | None:
    from orquesta import cli_json, confirmar
    comando = _comando_confirmado(args, repo)
    if not args.confirm:
        return _emit_tarea(args.cmd, cli_json.NECESITO_CONFIRMACION, {"comando": comando})
    try:
        coincide = confirmar.pedir_confirmacion(texto_esperado=esperado or args.branch, comando=comando,
                                                 detalle=detalle,
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
        revisor = args.revisor
        if revisor is not None:
            motivo = revision.validar_revisor(chat.mf.load(paths) or {}, wt.agent, revisor)
            if motivo is not None:
                return _emit_tarea(args.cmd, cli_json.ERROR, motivo=motivo)
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
        if revisor is None:
            revisor = revision.elegir_revisor(chat.mf.load(paths) or {}, wt.agent)
        if revisor is None:
            return _emit_tarea(args.cmd, cli_json.ERROR, motivo="no hay un revisor de otra familia disponible")
        tests = worktrees.run_tests(wt)  # siempre el comando actual de .orquesta/proyecto.toml
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
            "sha_revisado": sha if not reply.error and veredicto is not None else None, "tests_sha": tests_sha,
            "tests_ok": tests["ok"] if tests is not None else None,
            "tests_cmd": tests["cmd"] if tests is not None else None,
            "veredicto": veredicto, "revisor": revisor,
            "confianza_autor": registro.get("confianza_autor"),
        }, str(wt.repo), paths=paths)
        if reply.error or veredicto is None:
            return _emit_tarea(args.cmd, cli_json.ERROR, registro,
                               reply.error or "la review no devolvió un veredicto válido")
        return _emit_tarea(args.cmd, cli_json.OK, registro)
    except (worktrees.WorktreeError, mf.ManifestError, OSError, ValueError) as exc:
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
        detalle = f"Mergear {sha[:8]} de {wt.branch} en {registro['destino']} ({wt.repo})"
        esperado = wt.branch
        if razones:
            detalle += "\nFORZADO: se saltean:\n- " + "\n- ".join(razones)
            esperado = f"forzar {wt.branch}"
        resultado = _confirmar_tarea(args, wt.repo, leer_tty=leer_tty, detalle=detalle, esperado=esperado)
        if resultado is not None:
            return resultado
        # Mientras el usuario confirmaba pudo correr otra review: si la tarea cambió, no se usa la vieja.
        # La verificación y el merge van bajo el mismo lock que usa la review para invalidar la evidencia.
        claves = ("status", "sha_revisado", "tests_sha", "tests_ok", "veredicto", "confianza_autor")

        def mergear():
            if not worktrees._is_merged(wt.repo, registro["base_sha"]):
                raise worktrees.WorktreeError("el SHA base ya no es ancestro del destino")
            return worktrees.merge_sha(paths, wt.branch, sha, registro["destino"], repo=wt.repo)

        try:
            datos = tareas.cerrar_si_vigente(pid, wt.branch, str(wt.repo), {k: registro.get(k) for k in claves},
                                             mergear, status="mergeado", paths=paths)
        except worktrees.WorktreeError as exc:
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO, motivo=str(exc))
        if datos is None:
            return _emit_tarea(args.cmd, cli_json.BLOQUEADO,
                               motivo="la tarea cambió mientras confirmabas: revisala y volvé a correr el merge")
        avisos = [] if registro.get("tests_ok") is not None else [
            "el proyecto no tiene comando de tests (.orquesta/proyecto.toml): se mergeó solo con la review"]
        return cli_json.emit(cli_json.sobre(args.cmd, cli_json.OK, datos=datos, avisos=avisos))
    except (worktrees.WorktreeError, mf.ManifestError, OSError, ValueError) as exc:
        return _emit_tarea(args.cmd, cli_json.ERROR, motivo=str(exc))


def cmd_descartar(args: argparse.Namespace, *, leer_tty=None) -> int:
    from orquesta import cli_json, tareas, worktrees

    paths = default_paths()
    try:
        wt = worktrees._find(paths, args.branch, args.repo)
        pid = worktrees.project_id(wt.repo)
        registro = tareas.obtener(pid, wt.branch, str(wt.repo), paths=paths)
        resultado = _confirmar_tarea(args, wt.repo, leer_tty=leer_tty,
                                     detalle=f"Descartar {wt.branch} ({wt.repo}): se pierden los cambios")
        if resultado is not None:
            return resultado
        worktrees.discard(paths, wt.branch, repo=wt.repo)
        if registro is None:
            tareas.registrar(pid, {"repo": str(wt.repo), "rama": wt.branch, "agente": wt.agent,
                                  "base_sha": wt.base_sha, "status": "descartado"}, paths=paths)
        else:
            tareas.actualizar(pid, wt.branch, {"status": "descartado"}, str(wt.repo), paths=paths)
        return _emit_tarea(args.cmd, cli_json.OK, {"rama": wt.branch, "status": "descartado"})
    except (worktrees.WorktreeError, mf.ManifestError, OSError, ValueError) as exc:
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


def cmd_hook(args: argparse.Namespace) -> int:
    from orquesta import hooks
    try:
        paths = default_paths()
    except Exception:  # noqa: BLE001 — un hook roto no traba la sesión
        return 0
    return hooks.run(args.evento, paths)


def argv_rrhh(nueva: bool) -> list[str]:
    argv = ["claude", "--agent", "orquesta:rrhh", "-n", "orquesta-rrhh",
            "--allowedTools", "Bash(orquesta rrhh *)", "--disallowedTools", "Edit", "Write",
            "NotebookEdit", "--permission-mode", "dontAsk"]
    return argv if nueva else [*argv, "--continue"]


def cmd_rrhh_abrir(args: argparse.Namespace) -> int:
    import subprocess

    try:
        cwd = default_paths().state_dir / "rrhh"
        cwd.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(argv_rrhh(args.nueva), cwd=cwd)
        if result.returncode != 0 and not args.nueva:
            result = subprocess.run(argv_rrhh(True), cwd=cwd)
        return result.returncode
    except FileNotFoundError:
        print("No se encontró el binario 'claude'; instalá Claude Code y agregalo al PATH.", file=sys.stderr)
    except OSError as exc:
        print(f"No se pudo abrir la sesión de RR. HH.: {exc}", file=sys.stderr)
    return 1


def cmd_rrhh(args: argparse.Namespace) -> int:
    import json
    from dataclasses import asdict
    from datetime import datetime

    from filelock import FileLock, Timeout

    from orquesta import chat, cli_json, generate, guard, modelos, reclutador, rrhh
    from orquesta.config import load_backends, load_registry, parse_yaml_text, read_manual
    from orquesta.schema import ConfigError, check_registry, parse_registry

    comando = args.rrhh_cmd
    datos = {"errores": [], "violaciones": [], "comando_sugerido": None} if comando == "validar" else {}
    errores = []
    estado = cli_json.OK
    try:
        paths = default_paths()
        if comando in ("triage", "evaluar", "reclutador"):
            crudo = sys.stdin.read()
            try:
                pedido = json.loads(crudo)
            except ValueError:
                pedido = None
            if not isinstance(pedido, dict):
                if comando == "evaluar":
                    raise ValueError("el pedido JSON debe ser un objeto")
                pedido = {"texto": crudo}  # texto plano: `echo "pedido" | orquesta rrhh triage`
            if comando == "evaluar":
                campos = {"usos", "conversaciones", "calibracion"}
                if pedido.keys() - campos:
                    raise ValueError("campos desconocidos: " + ", ".join(sorted(pedido.keys() - campos)))
                if any(v is not None and (not isinstance(v, list) or
                       any(not isinstance(r, dict) for r in v)) for v in pedido.values()):
                    raise ValueError("usos, conversaciones y calibracion deben ser listas de objetos")
                datos = {"agentes": rrhh.evaluar(paths, **pedido)}
            else:
                texto = pedido.get("texto")
                if not isinstance(texto, str) or not texto.strip():
                    raise ValueError("texto debe ser una cadena no vacía")
                locales = modelos.ModelDiscovery().local_models()
                if comando == "triage":
                    datos = rrhh.triage(texto.strip(), load_registry(paths), locales)
                else:
                    datos = reclutador.consultar(texto, paths, locales)
                    if datos.get("error"):
                        errores = [datos["error"]]
                        estado = datos["estado"] if datos["estado"] in cli_json.ESTADOS_VALIDOS else cli_json.ERROR
        elif comando == "catalogo":
            datos = rrhh.catalogar(load_registry(paths))
        elif comando == "sugerir":
            conv = (chat.Store(paths).get_conversation(args.reunion) if args.reunion else
                    {"participants": [p.strip() for p in args.participantes.split(",") if p.strip()]})
            datos = rrhh.sugerir_participantes(conv, load_registry(paths))
        elif comando == "validar":
            reg = parse_registry(parse_yaml_text(sys.stdin.read(), "orquesta.yaml candidato"))
            errores = check_registry(reg, load_backends(paths))
            manuales = {a.name: text for a in reg.agents if a.manual
                        if (text := read_manual(paths, a.name)) is not None}
            violaciones = [asdict(v) for v in guard.check(generate._baseline(paths, manuales), reg, manuales)]
            datos["violaciones"] = [{"agente": v["agent"], "tipo": v["kind"], "detalle": v["detail"]}
                                    for v in violaciones]
            if violaciones:
                datos["comando_sugerido"] = "orquesta generate" + "".join(
                    f" --confirm {nombre}" for nombre in sorted({v["agent"] for v in violaciones}))
            if errores:
                estado = cli_json.ERROR
        elif comando in ("memoria", "nota"):
            archivo = paths.state_dir / "rrhh" / "memoria.md"
            if comando == "memoria":
                datos = {"texto": archivo.read_text(encoding="utf-8") if archivo.exists() else ""}
            else:
                texto = sys.stdin.read()
                if len(texto) > 20000:
                    raise ValueError("la nota supera el tope de 20000 caracteres")
                fecha = datetime.now().isoformat(timespec="seconds")
                entrada = f"\n## {fecha}\n\n{texto}\n"
                archivo.parent.mkdir(parents=True, exist_ok=True)
                with FileLock(str(archivo) + ".lock", timeout=30):
                    actual = archivo.read_text(encoding="utf-8") if archivo.exists() else ""
                    if len(actual) + len(entrada) > 200000:
                        raise ValueError("la memoria supera el tope total de 200000 caracteres")
                    with archivo.open("a", encoding="utf-8") as fh:
                        fh.write(entrada)
                datos = {"fecha": fecha}
    except ConfigError as exc:
        errores, estado = exc.errors, cli_json.ERROR
    except (ValueError, OSError, Timeout, chat.ChatError, mf.ManifestError) as exc:
        errores, estado = [str(exc)], cli_json.ERROR
    if comando == "validar":
        datos["errores"] = errores
    return cli_json.emit(cli_json.sobre(f"rrhh {comando}", estado, datos=datos, errores=errores))


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

    m = sub.add_parser("migrar", help="transicionar entre el deploy legacy y el plugin local")
    msub = m.add_subparsers(dest="subcomando", required=True)
    for nombre in ("estado", "corte", "rollback"):
        accion = msub.add_parser(nombre)
        accion.add_argument("--json", action="store_true", help="salida JSON (default)")
        if nombre != "estado":
            accion.add_argument("--confirm", action="store_true", help="pedir confirmación humana por TTY")
        accion.set_defaults(func=cmd_migrar)

    r = sub.add_parser("run", help="delegar en el backend de un agente (lee el pedido de stdin)")
    r.add_argument("agent")
    r.add_argument("--cwd", help="directorio del proyecto (default: el actual)")
    r.add_argument("--task", help="slug de la tarea (obligatorio para agentes con worktree)")
    r.add_argument("--prompt-file", help="leer el pedido de este archivo en vez de stdin")
    r.add_argument("--ping", action="store_true", help="ping mínimo por el backend del agente")
    r.add_argument("--timeout", type=float, help="segundos; pisa el timeout del modo (para correr en background)")
    r.set_defaults(func=cmd_run)

    reunion = sub.add_parser("reunion", help="ejecutar una reunión modal (lee la pregunta de stdin)")
    reunion.add_argument("--modo", required=True, choices=("rondas", "moderada", "critica", "posturas", "seccionado"))
    reunion.add_argument("--participantes", required=True, help="slugs de agentes separados por comas")
    reunion.add_argument("--aspectos", help="aspectos separados por punto y coma")
    reunion.add_argument("--cwd", help="directorio del proyecto (default: el actual)")
    reunion.add_argument("--titulo", help="título de la conversación")
    reunion.set_defaults(func=cmd_reunion)

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
        if nombre == "review":
            comando.add_argument("--revisor", metavar="NOMBRE", help="agente read-only de otra familia")
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
    h = sub.add_parser("hook", help="hooks del plugin de Claude Code (leen el evento por stdin)")
    h.add_argument("evento", choices=["session-start", "pre-bash"])
    h.set_defaults(func=cmd_hook)
    rh = sub.add_parser("rrhh", help="evaluar el plantel y consultar Recursos Humanos")
    rhsub = rh.add_subparsers(dest="rrhh_cmd", required=True)
    for nombre in ("triage", "evaluar", "catalogo", "reclutador", "validar", "memoria", "nota"):
        rhsub.add_parser(nombre).set_defaults(func=cmd_rrhh)
    sugerir = rhsub.add_parser("sugerir", help="sugerir participantes para una reunión")
    equipo = sugerir.add_mutually_exclusive_group(required=True)
    equipo.add_argument("--participantes", help="slugs de agentes separados por comas")
    equipo.add_argument("--reunion", metavar="ID", help="id de una conversación existente")
    sugerir.set_defaults(func=cmd_rrhh)
    abrir = rhsub.add_parser("abrir", help="abrir la sesión interactiva de RR. HH.")
    abrir.add_argument("--nueva", action="store_true", help="iniciar una sesión sin retomar la anterior")
    abrir.set_defaults(func=cmd_rrhh_abrir)
    _sin_abreviaturas(p)
    return p


def _sin_abreviaturas(parser: argparse.ArgumentParser) -> None:
    """argparse acepta `--conf` como `--confirm`: en una CLI que confirma acciones eso confunde al hook."""
    parser.allow_abbrev = False
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                _sin_abreviaturas(sub)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["ui"])  # `orquesta` a secas abre la UI
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
