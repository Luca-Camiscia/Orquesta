#!/usr/bin/env python3
"""Small evaluation: one agent vs. the orquesta loop, scored by hidden tests.

Each task in eval/tareas/<name>/ has buggy code, a vague issue (issue.md), a visible test the agents
can run, and hidden tests (test_oculto.py) that no agent sees. Two conditions, same author model:

  A  single agent   codex-forjador fixes the issue in a worktree; its result is taken as is.
  B  orquesta loop  same first run, then `orquesta review` by another model family (tests on the
                    reviewed SHA + verdict); if the verdict is not APROBAR, one more round for the
                    author with the review findings, from where it left off.
  D  loop+checklist B, plus the same self-review checklist as C in the author's second round: isolates
                    what the external reviewer adds beyond the checklist.
  C  self-review    control for B's extra round: same first run, then always one more round where
                    the author is asked to review its own attempt critically. No external reviewer.

The final commit of each condition is scored with the hidden tests. Real backends, real quota:
this calls Codex and Gemini through the same runner the plugin uses, in a throwaway ORQUESTA_HOME
(clone of this repo) so your own state is not touched.

    uv run python eval/run_eval.py [--tareas a,b] [--condiciones A,B,C] [--repeticiones N] > eval/results.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TAREAS = HERE / "tareas"


def sh(cwd: Path, *args: str) -> str:
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def preparar_home(tmp: Path, simulado: bool = False) -> None:
    """ORQUESTA_HOME descartable: clon del repo con el registro desplegado (sin tocar ~/.claude)."""
    home = tmp / "home"
    sh(REPO, "git", "clone", "-q", str(REPO), str(home))
    shutil.rmtree(home / "eval")  # el home de orquesta no necesita las tareas: los tests ocultos, fuera
    if simulado:  # backends guionados de la demo: prueba la plomería sin gastar cuota
        sys.path.insert(0, str(REPO / "examples" / "demo"))
        from run_demo import backends, dump
        dump(backends(), home / "backends.yaml")
        sh(home, "git", "-c", "user.name=eval", "-c", "user.email=eval@example.com", "commit", "-qam", "simulado")
    os.environ.update(ORQUESTA_HOME=str(home), CLAUDE_CONFIG_DIR=str(tmp / "claude"))
    (tmp / "claude").mkdir()
    sys.path.insert(0, str(REPO / "src"))
    from orquesta.generate import apply_plan, build_plan
    from orquesta.paths import default_paths
    apply_plan(build_plan(default_paths()), "deploy", run_tests=None)


def preparar_proyecto(tmp: Path, tarea: str, cond: str, rep: int = 1) -> Path:
    proj = tmp / "proyectos" / f"{tarea}-{cond}-{rep}"
    shutil.copytree(TAREAS / tarea, proj, ignore=shutil.ignore_patterns("test_oculto.py", "__pycache__"))
    (proj / ".orquesta").mkdir()
    (proj / ".orquesta" / "proyecto.toml").write_text(
        f'[worktree]\ntests = "{sys.executable} -m pytest -q -p no:cacheprovider test_visible.py"\n')
    sh(proj, "git", "init", "-q", "-b", "main")
    sh(proj, "git", "add", "-A")
    sh(proj, "git", "-c", "user.name=eval", "-c", "user.email=eval@example.com", "commit", "-qm", "inicial")
    return proj


def ocultos(tarea: str, worktree: Path) -> dict:
    """Corre los tests ocultos sobre el resultado, en una copia: no deja rastro en el worktree."""
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(worktree, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
        shutil.copy(TAREAS / tarea / "test_oculto.py", d)
        p = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_oculto.py"],
                           cwd=d, capture_output=True, text=True, timeout=120)
    ultima = (p.stdout.strip().splitlines() or [""])[-1]
    return {"ok": p.returncode == 0, "resumen": ultima}


def correr_agente(cwd: Path, task: str, prompt: str):
    from orquesta import runner
    from orquesta.paths import default_paths
    t = time.monotonic()
    r = runner.run(runner.RunRequest(agent="codex-forjador", cwd=cwd, prompt=prompt, task=task,
                                     wait_s=3600), default_paths())
    return r, round(time.monotonic() - t, 1)


def revisar(branch: str, proj: Path) -> tuple[dict, str, float]:
    from orquesta import chat, cli
    textos: list[str] = []

    def executor(paths, project, agente, prompt):
        reply = chat.default_executor(paths, project, agente, prompt)
        textos.append(reply.text or reply.error or "")
        return reply

    import contextlib
    import io
    buf = io.StringIO()
    t = time.monotonic()
    with contextlib.redirect_stdout(buf):
        cli.cmd_review(cli.build_parser().parse_args(["review", branch, "--repo", str(proj)]), executor=executor)
    sobre = json.loads(buf.getvalue() or "{}")
    return sobre, (textos[-1] if textos else ""), round(time.monotonic() - t, 1)


def entorno() -> dict:
    """Versiones y modelo del autor, para poder rastrear de dónde salió cada fila."""
    def salida(*cmd: str) -> str | None:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout.strip() or None
        except (OSError, subprocess.TimeoutExpired):
            return None
    import tomllib
    try:
        codex = tomllib.loads((Path.home() / ".codex" / "config.toml").read_text())
    except (OSError, ValueError):
        codex = {}
    return {"codex": salida("codex", "--version"), "agy": salida("agy", "--version"),
            "modelo_autor": codex.get("model"), "esfuerzo_autor": codex.get("model_reasoning_effort"),
            "orquesta": sh(REPO, "git", "rev-parse", "--short", "HEAD")}


def guardar_traza(trazas: Path | None, fila: dict, worktree: Path, base: str, revision: str) -> None:
    if trazas is None:
        return
    d = trazas / f"{fila['tarea']}-{fila['condicion']}-{fila['rep']}"
    d.mkdir(parents=True, exist_ok=True)
    def limpio(texto: str) -> str:  # sin rutas del usuario en lo que se publica
        return texto.replace(str(Path.home()), "~")
    (d / "final.patch").write_text(limpio(sh(worktree, "git", "diff", base, "HEAD")), encoding="utf-8")
    if revision:
        (d / "review.md").write_text(limpio(revision), encoding="utf-8")


def trial(tmp: Path, tarea: str, cond: str, rep: int = 1, trazas: Path | None = None) -> dict:
    proj = preparar_proyecto(tmp, tarea, cond, rep)
    base = sh(proj, "git", "rev-parse", "HEAD")
    texto = ""
    issue = (TAREAS / tarea / "issue.md").read_text().strip()
    fila: dict = {"tarea": tarea, "condicion": cond, "rep": rep, "llamadas_autor": 0, "llamadas_revisor": 0,
                  "segundos": 0.0}
    r, s = correr_agente(proj, f"eval-{tarea}-{cond.lower()}{rep}", issue)
    fila["llamadas_autor"] += 1
    fila["segundos"] += s
    if r.status != "OK" or not r.meta.get("worktree"):
        return {**fila, "error": f"{r.status}: {r.detail}", "ocultos_ok": False}
    worktree, branch = Path(r.meta["worktree"]), r.meta["branch"]
    if cond in ("B", "D"):
        sobre, texto, s = revisar(branch, proj)
        fila["llamadas_revisor"] += 1
        fila["segundos"] += s
        datos = sobre.get("datos") or {}
        fila["veredicto_1"] = datos.get("veredicto")
        if sobre.get("estado") != "OK":
            # Una review que falló (sesión vencida, timeout) no es una condición B: la fila no cuenta.
            return {**fila, "error": f"review {sobre.get('estado')}: {texto[:300]}", "ocultos_ok": None}
        if datos.get("veredicto") != "APROBAR" and texto:
            prompt = (f"{issue}\n\nYa hiciste un primer intento (está commiteado en este worktree). Una "
                      f"review independiente de otro modelo dijo:\n\n{texto[-6000:]}\n\nCorregí lo que "
                      f"corresponda; si un hallazgo no aplica, ignoralo.")
            if cond == "D":  # mismo checklist que C: aísla el aporte del revisor externo
                prompt += ("\n\nAdemás, revisá críticamente tu intento como lo haría un revisor exigente: "
                           "casos borde, entradas inválidas, tests que faltan.")
            r2, s = correr_agente(worktree, f"eval-{tarea}-{cond.lower()}{rep}r2", prompt)
            fila["llamadas_autor"] += 1
            fila["segundos"] += s
            if r2.status == "OK" and r2.meta.get("worktree"):
                worktree = Path(r2.meta["worktree"])
            else:
                fila["error_ronda_2"] = f"{r2.status}: {r2.detail}"
    if cond == "C":  # control: misma segunda vuelta para el autor, sin revisor externo
        prompt = (f"{issue}\n\nYa hiciste un primer intento (está commiteado en este worktree). Revisalo "
                  f"críticamente como lo haría un revisor exigente: casos borde, entradas inválidas, "
                  f"tests que faltan. Corregí lo que corresponda.")
        r2, s = correr_agente(worktree, f"eval-{tarea}-c{rep}r2", prompt)
        fila["llamadas_autor"] += 1
        fila["segundos"] += s
        if r2.status == "OK" and r2.meta.get("worktree"):
            worktree = Path(r2.meta["worktree"])
        else:
            fila["error_ronda_2"] = f"{r2.status}: {r2.detail}"
    fila.update(ocultos=ocultos(tarea, worktree), ocultos_ok=None)
    fila["ocultos_ok"] = fila["ocultos"]["ok"]
    fila["sha_final"] = sh(worktree, "git", "rev-parse", "--short", "HEAD")
    guardar_traza(trazas, fila, worktree, base, texto)
    return fila


def _trial(tmp: Path, trazas: Path | None, ent: dict, tc: tuple[str, str, int]) -> dict:
    try:
        return {**trial(tmp, *tc, trazas=trazas), "entorno": ent}
    except Exception as exc:  # una tarea rota no tira abajo las demás
        return {"tarea": tc[0], "condicion": tc[1], "rep": tc[2], "error": repr(exc), "ocultos_ok": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tareas", default=",".join(sorted(p.name for p in TAREAS.iterdir() if p.is_dir())))
    ap.add_argument("--condiciones", default="A,B,C")
    ap.add_argument("--repeticiones", type=int, default=1)
    ap.add_argument("--paralelo", type=int, default=2, help="tareas a la vez (codex max_parallel = 2)")
    ap.add_argument("--simulado", action="store_true", help="backends guionados: sin cuota, solo plomería")
    ap.add_argument("--trazas", type=Path, help="carpeta donde guardar el parche final y la review de cada corrida")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="orquesta-eval-", dir=Path.home() / ".cache"))
    preparar_home(tmp, args.simulado)
    trabajos = [(t, c, n) for n in range(1, args.repeticiones + 1)
                for t in args.tareas.split(",") for c in args.condiciones.split(",")]
    # Procesos y no hilos: la review captura stdout con redirect_stdout, que es global al proceso.
    with ProcessPoolExecutor(args.paralelo) as pool:
        ent = entorno()
        for fila in pool.map(partial(_trial, tmp, args.trazas, ent), trabajos):
            print(json.dumps(fila, ensure_ascii=False), flush=True)
    print(f"sandbox: {tmp}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
