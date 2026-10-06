"""La confirmación exige el texto humano y nunca depende del entorno."""
from __future__ import annotations

import inspect
import io

import pytest

from orquesta import confirmar


@pytest.mark.parametrize("linea,esperado", [
    ("CONFIRMAR", True),
    ("  CONFIRMAR\n", True),
    ("confirmar\n", False),
    ("sí\n", False),
    ("CONFIRMAR extra\n", False),
    ("", False),
])
def test_confirmacion_exige_coincidencia_exacta(linea, esperado):
    assert confirmar.pedir_confirmacion("CONFIRMAR", "orquesta generate --confirm",
                                       leer_tty=lambda prompt: linea) is esperado


def test_prompt_en_espanol_rioplatense():
    prompts = []

    def leer(prompt):
        prompts.append(prompt)
        return "borrar agente\n"

    assert confirmar.pedir_confirmacion("borrar agente", "orquesta borrar --confirm",
                                       leer_tty=leer) is True
    assert prompts == ["Para confirmar, escribí «borrar agente»: "]


def test_sin_tty_incluye_comando_sugerido(monkeypatch):
    def sin_tty(*args, **kwargs):
        raise OSError("Sin terminal")

    monkeypatch.setattr("builtins.open", sin_tty)
    comando = "orquesta generate --confirm"
    with pytest.raises(confirmar.SinTTY) as exc:
        confirmar.pedir_confirmacion("CONFIRMAR", comando)
    assert exc.value.comando_sugerido == comando


def test_tty_real_se_abre_binario_muestra_prompt_y_lee_una_linea(monkeypatch):
    class TTY(io.BytesIO):
        salida = b""

        def write(self, datos):
            self.salida += datos
            return len(datos)

    tty = TTY("sí\nNO\n".encode("utf-8"))

    def abrir(path, mode, *, buffering):
        assert path == "/dev/tty"
        assert mode == "r+b"
        assert buffering == 0
        return tty

    monkeypatch.setattr("builtins.open", abrir)
    assert confirmar.pedir_confirmacion("sí", "orquesta generate --confirm") is True
    assert tty.salida.decode("utf-8") == "Para confirmar, escribí «sí»: "
    assert tty.closed


def test_modulo_no_lee_variables_de_entorno():
    source = inspect.getsource(confirmar)
    assert "environ" not in source
    assert "getenv" not in source


def test_texto_esperado_vacio_no_se_acepta():
    with pytest.raises(ValueError):
        confirmar.pedir_confirmacion("  ", "orquesta merge x --confirm", leer_tty=lambda _p: "")
