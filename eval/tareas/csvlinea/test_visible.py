from codigo import parse_csv_line


def test_simple():
    assert parse_csv_line("a,b,c") == ["a", "b", "c"]
