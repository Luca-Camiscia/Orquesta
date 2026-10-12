from codigo import merge_intervals


def test_desordenados():
    assert merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]


def test_que_se_tocan():
    assert merge_intervals([[1, 2], [2, 3]]) == [[1, 3]]


def test_vacio_y_contenido():
    assert merge_intervals([]) == []
    assert merge_intervals([[1, 10], [2, 3]]) == [[1, 10]]


def test_no_muta_la_entrada():
    entrada = [[1, 3], [2, 6]]
    merge_intervals(entrada)
    assert entrada == [[1, 3], [2, 6]]
