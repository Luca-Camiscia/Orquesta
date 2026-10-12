import pytest
from codigo import paginate


def test_ultima_pagina_incompleta():
    assert paginate(list(range(10)), 4, 3) == [9]


def test_pagina_fuera_de_rango_es_vacia():
    assert paginate(list(range(10)), 5, 3) == []


@pytest.mark.parametrize("page,per_page", [(0, 3), (-1, 3), (1, 0), (1, -2)])
def test_parametros_invalidos(page, per_page):
    with pytest.raises(ValueError):
        paginate([1, 2, 3], page, per_page)
