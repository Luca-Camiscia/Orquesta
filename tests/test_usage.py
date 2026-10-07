"""Uso por agente: métricas, ventana temporal y salida de la CLI."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from orquesta import cli, usage

NOW = datetime(2026, 10, 6, tzinfo=timezone.utc)
DAY = 86400


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def row(**changes):
    return {"timestamp": NOW.timestamp() - DAY, "backend": "codex", "agent": "forjador",
            "status": "ok", "tokens_in": 10, "tokens_out": 5, "latency_s": 2.0, **changes}


@pytest.mark.parametrize("tokens_in,tokens_out,expected", [(None, None, 0), (None, 5, 5), (10, None, 10)])
def test_tokens_none_cuentan_como_cero(tmp_path, tokens_in, tokens_out, expected):
    path = write_rows(tmp_path / "usage.jsonl", [row(tokens_in=tokens_in, tokens_out=tokens_out)])
    result = usage.por_agente([path], now=NOW)["forjador"]
    assert result["llamadas"] == 1
    assert result["tokens"] == expected


@pytest.mark.parametrize("dias,expected", [(None, 2), (7, 1)])
def test_ventana_default_y_custom_con_limites(tmp_path, dias, expected):
    path = write_rows(tmp_path / "usage.jsonl", [
        row(timestamp=NOW.timestamp() - 30 * DAY),
        row(timestamp=NOW.timestamp() - 7 * DAY),
        row(timestamp=NOW.timestamp() - 30 * DAY - 1),
        row(timestamp=NOW.timestamp()),
        row(timestamp=NOW.timestamp() + 1),
    ])
    kwargs = {} if dias is None else {"dias": dias}
    result = usage.por_agente([path], now=NOW.timestamp(), **kwargs)["forjador"]
    assert result["llamadas"] == expected
    assert result["tokens"] == expected * 15


def test_metricas_agrupadas_por_agente_entre_archivos(tmp_path):
    first = write_rows(tmp_path / "first.jsonl", [row(), row(status="ERROR", latency_s=4)])
    second = write_rows(tmp_path / "second.jsonl", [row(agent="critico", latency_s=9)])
    assert usage.por_agente([first, second], now=NOW) == {
        "forjador": {"llamadas": 2, "tokens": 30, "latencia_media_s": 3.0,
                     "errores": 1, "tasa_error": 0.5, "backend": "codex"},
        "critico": {"llamadas": 1, "tokens": 15, "latencia_media_s": 9.0,
                    "errores": 0, "tasa_error": 0.0, "backend": "codex"},
    }


def test_multiples_backends_reportan_none(tmp_path):
    path = write_rows(tmp_path / "usage.jsonl", [row(), row(backend="agy")])
    assert usage.por_agente([path], now=NOW)["forjador"]["backend"] is None


def test_latencias_none_no_se_promedian_como_cero(tmp_path):
    path = write_rows(tmp_path / "usage.jsonl", [
        row(latency_s=None), row(latency_s=4), row(agent="critico", latency_s=None),
    ])
    result = usage.por_agente([path], now=NOW)
    assert result["forjador"]["latencia_media_s"] == 4.0
    assert result["critico"]["latencia_media_s"] == 0.0


def test_lee_formato_real_de_record(tmp_path):
    from orquesta.paths import Paths

    paths = Paths(home=tmp_path, claude_dir=tmp_path / "claude")
    paths.state_dir.mkdir()
    usage.record(paths, backend="codex", agent="forjador", status="OK", tokens_in=12,
                 tokens_out=3, latency_s=2, ts=datetime(2026, 10, 5, tzinfo=timezone.utc))
    result = usage.por_agente([paths.state_dir / "usage.jsonl"], now=NOW)["forjador"]
    assert result["tokens"] == 15
    assert result["errores"] == 0


def test_archivo_ausente_y_lineas_invalidas(tmp_path):
    path = write_rows(tmp_path / "usage.jsonl", [row()])
    with path.open("a", encoding="utf-8") as fh:
        fh.write('invalid\n{}\nnull\n{"timestamp": null}\n')
    result = usage.por_agente([tmp_path / "missing.jsonl", path], now=NOW)
    assert result["forjador"]["llamadas"] == 1
    assert usage.por_agente([], now=NOW) == {}


@pytest.mark.parametrize("args,expected", [([], 2), (["--dias", "7"], 1)])
def test_cli_usage_json_con_ventana_default_y_custom(tmp_path, monkeypatch, capsys, args, expected):
    now = datetime.now(timezone.utc).timestamp()
    monkeypatch.setenv("ORQUESTA_HOME", str(tmp_path))
    write_rows(tmp_path / "state" / "usage.jsonl", [
        row(timestamp=now - DAY), row(timestamp=now - 10 * DAY),
        row(timestamp=now - 31 * DAY),
    ])
    assert cli.main(["usage", *args]) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"agent": "forjador", "llamadas": expected, "tokens": expected * 15,
         "latencia_media_s": 2.0, "errores": 0, "tasa_error": 0.0, "backend": "codex"},
    ]


def test_cli_usage_sin_registro_devuelve_array_vacio(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ORQUESTA_HOME", str(tmp_path))
    assert cli.main(["usage"]) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert not (tmp_path / "state").exists()


@pytest.mark.parametrize("changes", [
    pytest.param({"latency_s": "bad"}, id="latencia-string"),
    pytest.param({"tokens_in": "bad"}, id="tokens-in-string"),
    pytest.param({"tokens_out": "bad"}, id="tokens-out-string"),
    pytest.param({"backend": []}, id="backend-lista"),
    pytest.param({"backend": {}}, id="backend-dict"),
    pytest.param({"backend": None}, id="backend-null"),
    pytest.param({"backend": ""}, id="backend-vacio"),
    pytest.param({"backend": "  "}, id="backend-espacios"),
    pytest.param({"agent": None}, id="agente-null"),
    pytest.param({"agent": ""}, id="agente-vacio"),
    pytest.param({"agent": "  "}, id="agente-espacios"),
    pytest.param({"timestamp": None, "ts": None}, id="sin-timestamp"),
    pytest.param({"tokens_in": True}, id="tokens-in-true"),
    pytest.param({"tokens_in": False}, id="tokens-in-false"),
    pytest.param({"tokens_out": True}, id="tokens-out-true"),
    pytest.param({"tokens_out": False}, id="tokens-out-false"),
    pytest.param({"latency_s": True}, id="latencia-true"),
    pytest.param({"latency_s": False}, id="latencia-false"),
])
def test_fila_con_tipos_invalidos_se_omite_entera(tmp_path, changes):
    invalid = row(status="ERROR", tokens_in=1000, tokens_out=500, latency_s=100)
    invalid.update(changes)
    path = write_rows(tmp_path / "usage.jsonl", [invalid, row(), invalid,
                                               row(status="ERROR", latency_s=4)])
    assert usage.por_agente([path], now=NOW) == {
        "forjador": {"llamadas": 2, "tokens": 30, "latencia_media_s": 3.0,
                     "errores": 1, "tasa_error": 0.5, "backend": "codex"},
    }


@pytest.mark.parametrize("stamp", [NOW.timestamp() - DAY, "2026-10-05T00:00:00+00:00"])
def test_timestamp_null_usa_ts_valido(tmp_path, stamp):
    path = write_rows(tmp_path / "usage.jsonl", [row(timestamp=None, ts=stamp), row()])
    assert usage.por_agente([path], now=NOW) == {
        "forjador": {"llamadas": 2, "tokens": 30, "latencia_media_s": 2.0,
                     "errores": 0, "tasa_error": 0.0, "backend": "codex"},
    }


@pytest.mark.parametrize("dias", ["0", "-1"])
def test_cli_usage_rechaza_dias_no_positivos(tmp_path, monkeypatch, capsys, dias):
    monkeypatch.setenv("ORQUESTA_HOME", str(tmp_path))
    write_rows(tmp_path / "state" / "usage.jsonl", [row()])
    assert cli.main(["usage", "--dias", dias]) == 1
    captured = capsys.readouterr()
    assert "--dias debe estar entre 1 y 36500" in captured.err
    assert captured.out == ""


def test_bytes_no_utf8_se_omiten_sin_abortar(tmp_path):
    path = tmp_path / "usage.jsonl"
    good = json.dumps(row()).encode("utf-8") + b"\n"
    path.write_bytes(b'{"agent": "ni\xf1o"\xff}\n' + good)
    assert usage.por_agente([path], now=NOW)["forjador"]["llamadas"] == 1


@pytest.mark.parametrize("campo", ["tokens_in", "tokens_out", "latency_s"])
@pytest.mark.parametrize("valor", ["NaN", "Infinity", "-Infinity"])
def test_nan_e_infinity_invalidan_la_linea(tmp_path, campo, valor):
    path = tmp_path / "usage.jsonl"
    mala = json.dumps(row()).replace(f'"{campo}": ' + str(row()[campo]), f'"{campo}": {valor}')
    assert valor in mala
    path.write_text(mala + "\n" + json.dumps(row()) + "\n", encoding="utf-8")
    resultado = usage.por_agente([path], now=NOW)
    assert resultado["forjador"]["llamadas"] == 1
    json.dumps(resultado, allow_nan=False)  # la salida es JSON estricto


def test_cli_dias_gigante_da_error_y_no_traceback(capsys):
    assert cli.main(["usage", "--dias", "9" * 300]) == 1
    assert "--dias debe estar entre 1 y 36500" in capsys.readouterr().err


def test_iso_sin_zona_se_interpreta_como_utc(tmp_path, monkeypatch):
    monkeypatch.setenv("TZ", "America/Argentina/Buenos_Aires")
    import time
    time.tzset()
    try:
        path = tmp_path / "usage.jsonl"
        # 2026-10-05T23:00 sin zona: en UTC entra en la ventana de 1 día que termina en NOW (2026-10-06T00:00Z)
        path.write_text(json.dumps(row(timestamp="2026-10-05T23:00:00")) + "\n", encoding="utf-8")
        assert usage.por_agente([path], now=NOW, dias=1)["forjador"]["llamadas"] == 1
        # y 2026-10-06T01:00 sin zona es posterior a NOW en UTC: afuera (en hora local AR seguiría adentro)
        path.write_text(json.dumps(row(timestamp="2026-10-06T01:00:00")) + "\n", encoding="utf-8")
        assert usage.por_agente([path], now=NOW, dias=1) == {}
    finally:
        monkeypatch.undo()
        time.tzset()
