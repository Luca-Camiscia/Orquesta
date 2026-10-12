import pytest
from codigo import moving_average


def test_ventana_igual_al_largo():
    assert moving_average([1, 2, 3], 3) == [2.0]


def test_ventana_mayor_al_largo_es_vacia():
    assert moving_average([1, 2], 3) == []


@pytest.mark.parametrize("k", [0, -1])
def test_ventana_invalida(k):
    with pytest.raises(ValueError):
        moving_average([1, 2, 3], k)


def test_no_pierde_precision_con_floats():
    assert moving_average([0.1, 0.2, 0.3], 1) == [0.1, 0.2, 0.3]
