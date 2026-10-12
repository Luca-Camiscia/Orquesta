from codigo import slugify


def test_basico():
    assert slugify("Hola Mundo") == "hola-mundo"
