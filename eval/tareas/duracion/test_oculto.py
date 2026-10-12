import pytest
from codigo import parse_duration


@pytest.mark.parametrize("texto,seg", [("45s", 45), ("2h", 7200), ("1h0m5s", 3605), ("90m", 5400)])
def test_validos(texto, seg):
    assert parse_duration(texto) == seg


@pytest.mark.parametrize("texto", ["", "abc", "1x", "h", "30m1h", "-5m", "1.5h"])
def test_invalidos(texto):
    with pytest.raises(ValueError):
        parse_duration(texto)
