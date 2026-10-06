"""Confirmación humana por terminal, con lectura inyectable para tests."""
from __future__ import annotations


class SinTTY(Exception):
    comando_sugerido: str

    def __init__(self, comando_sugerido: str):
        self.comando_sugerido = comando_sugerido
        super().__init__(f"Sin terminal disponible. Corré: {comando_sugerido}")


def pedir_confirmacion(texto_esperado: str, comando: str, *, leer_tty=None) -> bool:
    prompt = f"Para confirmar, escribí «{texto_esperado}»: "
    if leer_tty is not None:
        linea = leer_tty(prompt)
    else:
        try:
            tty = open("/dev/tty", "r+b", buffering=0)
        except OSError as exc:
            raise SinTTY(comando_sugerido=comando) from exc
        with tty:
            tty.write(prompt.encode("utf-8"))
            linea = tty.readline().decode("utf-8")
    return linea.strip() == texto_esperado
