"""Bloque marcado de ~/.claude/CLAUDE.md.

Solo se reescribe lo que está entre los marcadores. Todo lo demás se conserva byte a byte. Si los
marcadores están rotos (duplicados, sin cierre, invertidos), no se toca nada y se pide arreglarlo a
mano.
"""
from __future__ import annotations

START = "<!-- orquesta:start -->"
END = "<!-- orquesta:end -->"


class MarkerError(ValueError):
    pass


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def find_block(text: str) -> tuple[int, int] | None:
    """(inicio del marcador START, fin del marcador END), o None si no hay marcadores."""
    n_start, n_end = text.count(START), text.count(END)
    if n_start == 0 and n_end == 0:
        return None
    if n_start != 1 or n_end != 1:
        raise MarkerError(f"CLAUDE.md tiene {n_start} marcador(es) {START} y {n_end} {END}: tiene "
                          f"que haber exactamente uno de cada. Arreglalo a mano; orquesta no tocó "
                          f"el archivo.")
    i, j = text.index(START), text.index(END)
    if j < i:
        raise MarkerError(f"En CLAUDE.md {END} aparece antes que {START}. Arreglalo a mano; "
                          f"orquesta no tocó el archivo.")
    return i, j + len(END)


def extract_inner(text: str) -> str | None:
    """Contenido entre los marcadores (normalizado a \\n), o None si no hay bloque."""
    span = find_block(text)
    if span is None:
        return None
    i, j = span
    return text[i + len(START):j - len(END)].replace("\r\n", "\n").strip("\n")


def remove_block(original: str) -> str:
    """Quita ambos marcadores y su contenido, sin tocar ningún byte externo."""
    span = find_block(original)
    if span is None:
        return original
    i, j = span
    return original[:i] + original[j:]


def apply_block(original: str | None, inner: str) -> str:
    """Contenido nuevo de CLAUDE.md con `inner` entre los marcadores."""
    inner = inner.replace("\r\n", "\n").strip("\n")
    if not original:
        return f"{START}\n{inner}\n{END}\n"
    nl = _newline(original)
    block = f"{START}{nl}{inner.replace(chr(10), nl)}{nl}{END}"
    span = find_block(original)
    if span is None:
        if original.endswith(nl + nl):
            sep = ""
        elif original.endswith(nl):
            sep = nl
        else:
            sep = nl + nl
        return f"{original}{sep}{block}{nl}"
    i, j = span
    return original[:i] + block + original[j:]
