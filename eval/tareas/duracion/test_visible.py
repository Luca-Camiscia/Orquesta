from codigo import parse_duration


def test_basico():
    assert parse_duration("1h30m") == 5400
