"""Medidor de uso por backend, en ventanas de 5 horas (así se miden los límites de las suscripciones).

Cada invocación (runner y chat) queda en state/usage.jsonl. Con eso se calcula el consumo de la ventana
actual y, si alguna vez hubo rate limit, un "límite estimado": lo consumido en las 5 h previas a ese
rate limit. Al pasar el 80% de ese límite, el riesgo es alto y conviene repartir la carga.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from filelock import FileLock

from orquesta.paths import Paths

WINDOW = timedelta(hours=5)


def _file(paths: Paths):
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    return paths.state_dir / "usage.jsonl"


def record(paths: Paths, *, backend: str, agent: str, status: str, tokens_in: int | None = None,
           tokens_out: int | None = None, cost_usd: float | None = None, latency_s: float | None = None,
           ts: datetime | None = None) -> None:
    row = {"ts": (ts or datetime.now(timezone.utc)).isoformat(), "backend": backend, "agent": agent,
           "status": status, "tokens_in": tokens_in, "tokens_out": tokens_out, "cost_usd": cost_usd,
           "latency_s": latency_s}
    with FileLock(str(paths.state_dir / "usage.lock"), timeout=10):
        with open(_file(paths), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")


def load(paths: Paths) -> list[dict]:
    try:
        lines = _file(paths).read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        try:
            r = json.loads(ln)
            r["_ts"] = datetime.fromisoformat(r["ts"])
            out.append(r)
        except (ValueError, KeyError):
            continue
    return out


def _tokens(r: dict) -> int:
    return (r.get("tokens_in") or 0) + (r.get("tokens_out") or 0)


def window(paths: Paths, now: datetime | None = None) -> dict[str, dict]:
    now = now or datetime.now(timezone.utc)
    rows = load(paths)
    out: dict[str, dict] = {}
    for backend in sorted({r["backend"] for r in rows}):
        mine = [r for r in rows if r["backend"] == backend]
        cur = [r for r in mine if now - WINDOW <= r["_ts"] <= now]
        limits = [r for r in mine if r["status"] == "RATE_LIMIT"]
        estimates = [sum(_tokens(x) for x in mine if lim["_ts"] - WINDOW <= x["_ts"] <= lim["_ts"])
                     for lim in limits]
        estimates = [e for e in estimates if e > 0]
        used = sum(_tokens(r) for r in cur)
        limit = min(estimates) if estimates else None
        if limit is None:
            risk = "desconocido"
        elif used >= 0.8 * limit:
            risk = "alto"
        elif used >= 0.5 * limit:
            risk = "medio"
        else:
            risk = "bajo"
        out[backend] = {
            "llamadas": len(cur), "tokens": used,
            "costo_usd": round(sum(r.get("cost_usd") or 0 for r in cur), 4),
            "errores": sum(1 for r in cur if r["status"] not in ("OK",)),
            "limite_estimado": limit, "riesgo": risk,
            "ultimo_rate_limit": max((r["ts"] for r in limits), default=None),
        }
    return out
