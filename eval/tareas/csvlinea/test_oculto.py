from codigo import parse_csv_line


def test_comillas_con_coma():
    assert parse_csv_line('a,"b,c",d') == ["a", "b,c", "d"]


def test_comillas_escapadas():
    assert parse_csv_line('"dijo ""hola""",x') == ['dijo "hola"', "x"]


def test_campos_vacios():
    assert parse_csv_line("a,,") == ["a", "", ""]
    assert parse_csv_line("") == [""]
