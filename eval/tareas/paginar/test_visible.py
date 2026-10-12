from codigo import paginate


def test_primera_pagina():
    assert paginate(list(range(10)), 1, 3) == [0, 1, 2]
