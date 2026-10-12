from codigo import slugify


def test_acentos_y_enie():
    assert slugify("Año Nuevo en Córdoba") == "ano-nuevo-en-cordoba"


def test_puntuacion_y_espacios_repetidos():
    assert slugify("  ¡Hola,   Mundo!  ") == "hola-mundo"


def test_guiones_y_vacio():
    assert slugify("a - b") == "a-b"
    assert slugify("!!!") == ""
