"""CLI `orquesta`.

    orquesta                         abre la UI local (paso 6)
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
                                              prompt=prompt, task=args.task), paths)
    print(runner.format_result(result))
    return result.exit_code


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="orquesta", description="Ecosistema multi-modelo para Claude Code.")
    sub = p.add_subparsers(dest="cmd")

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
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("doctor", help="chequeos de salud del ecosistema (sin LLM)")
    d.add_argument("--json", action="store_true", help="salida JSON (para la UI)")
    d.add_argument("--no-cache", action="store_true", help="ignorar el cache (auth, pings, versiones)")
    d.add_argument("--skip-live", action="store_true", help="saltear los chequeos que gastan cuota")
    d.add_argument("--only", metavar="ID", help="solo los chequeos cuyo id empieza así")
    d.set_defaults(func=cmd_doctor)

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
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
