"""Subprocesos cancelables.

Los CLIs (claude, codex, agy y bwrap) lanzan sus propios hijos. Por eso cada proceso arranca en su
propio grupo (`start_new_session=True`) y cancelar mata el grupo entero. Los procesos se registran
bajo una clave (por ejemplo, el id de una conversación) para que la UI pueda cortarlos con «Detener».
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
from dataclasses import dataclass

_lock = threading.Lock()
_active: dict[str, list[subprocess.Popen]] = {}
_cancelled: set[str] = set()


@dataclass
class ProcResult:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


def _kill_group(p: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(p.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def run(cmd: list[str], cwd=None, input: str | None = None, timeout: float | None = None,
        key: str | None = None, env: dict | None = None) -> ProcResult:
    p = subprocess.Popen(cmd, cwd=cwd, text=True, start_new_session=True, env=env,
                         stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if key:
        with _lock:
            _active.setdefault(key, []).append(p)
    timed_out = False
    try:
        try:
            out, err = p.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(p, signal.SIGKILL)
            out, err = p.communicate()
    finally:
        if key:
            with _lock:
                procs = _active.get(key, [])
                if p in procs:
                    procs.remove(p)
                if not procs:
                    _active.pop(key, None)
    with _lock:
        cancelled = bool(key) and key in _cancelled
    return ProcResult(p.returncode, out or "", err or "", timed_out, cancelled)


def active(key: str) -> bool:
    with _lock:
        return bool(_active.get(key))


def cancel(key: str) -> int:
    """Marca la clave como cancelada y mata los grupos de procesos activos. Devuelve cuántos."""
    with _lock:
        _cancelled.add(key)
        procs = list(_active.get(key, []))
    for p in procs:
        _kill_group(p, signal.SIGTERM)
    if procs:
        threading.Timer(3.0, lambda: [_kill_group(p, signal.SIGKILL) for p in procs if p.poll() is None]).start()
    return len(procs)


def is_cancelled(key: str) -> bool:
    with _lock:
        return key in _cancelled


def reset(key: str) -> None:
    with _lock:
        _cancelled.discard(key)
