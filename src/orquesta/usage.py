"""Medidor de uso por backend, en ventanas de 5 horas (así se miden los límites de las suscripciones).

Cada invocación (runner y chat) queda en state/usage.jsonl. Con eso se calcula el consumo de la ventana
actual y, si alguna vez hubo rate limit, un "límite estimado": lo consumido en las 5 h previas a ese
rate limit. Al pasar el 80% de ese límite, el riesgo es alto y conviene repartir la carga.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def por_agente(paths: list[Path], now: datetime | float | None = None, dias: int = 30) -> dict[str, dict]:
    """Analiza uso por agente en los últimos N días leyendo archivos JSONL línea por línea.

    `now` acepta datetime o segundos Unix; por defecto usa time.time(). La ventana es
    [now - dias * 86400, now). Acepta timestamp (Unix o ISO) y el ts ISO de record().
    Los tokens None cuentan como cero; la latencia media usa solo muestras disponibles
    (0.0 si no hay ninguna). Los estados ok y OK indican éxito.
    """
    end = time.time() if now is None else now.timestamp() if isinstance(now, datetime) else now
    start = end - dias * 86400
    totals: dict[str, dict] = {}
    for path in paths:
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                try:
                    row = json.loads(line)
                    stamp = row.get("timestamp")
                    if stamp is None:
                        stamp = row.get("ts")
                    agent = row.get("agent")
                    if not isinstance(agent, str) or not agent.strip():
                        continue
                    backend = row.get("backend")
                    if not isinstance(backend, str) or not backend.strip():
                        continue
                    tokens_in = row.get("tokens_in")
                    tokens_out = row.get("tokens_out")
                    latency = row.get("latency_s")
                    if any(value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                                  or not 0 <= value <= 10**12)
                           for value in (tokens_in, tokens_out)):
                        continue  # los tokens son un conteo: enteros, no negativos y acotados
                    if latency is not None and (isinstance(latency, bool) or not isinstance(latency, (int, float))
                                                or not math.isfinite(latency) or not 0 <= latency <= 10**7):
                        continue  # NaN e Infinity pasan json.loads pero romperían el JSON de salida
                    if isinstance(stamp, str):
                        momento = datetime.fromisoformat(stamp)
                        if momento.tzinfo is None:  # sin zona: UTC, como escribe record(), no la hora local
                            momento = momento.replace(tzinfo=timezone.utc)
                        ts = momento.timestamp()
                    else:
                        ts = stamp
                    if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
                        continue
                    if not start <= ts < end:
                        continue
                    total = totals.setdefault(agent, {"llamadas": 0, "tokens": 0, "errores": 0,
                                                       "latencia": 0.0, "muestras": 0, "backends": set()})
                    total["llamadas"] += 1
                    total["tokens"] += (tokens_in or 0) + (tokens_out or 0)
                    total["errores"] += row.get("status") not in ("ok", "OK")
                    total["backends"].add(backend)
                    if latency is not None:
                        total["latencia"] += latency
                        total["muestras"] += 1
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    continue
    return {
        agent: {"llamadas": total["llamadas"], "tokens": total["tokens"],
                "latencia_media_s": total["latencia"] / total["muestras"] if total["muestras"] else 0.0,
                "errores": total["errores"], "tasa_error": total["errores"] / total["llamadas"],
                "backend": next(iter(total["backends"])) if len(total["backends"]) == 1 else None}
        for agent, total in totals.items()
    }


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
