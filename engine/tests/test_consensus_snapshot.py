"""consensus_snapshot.py: trimming Yahoo's quoteSummary and writing the file (no network)."""

import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import consensus_snapshot as C

SUMMARY = {
    "price": {
        "regularMarketPrice": {"raw": 10.5, "fmt": "10.50"},
        "shortName": "Acme",
        "maxAge": 1,
        "extra": 1,
    },
    "financialData": {"targetMeanPrice": {"raw": 12.0, "fmt": "12"}, "currentPrice": {}, "junk": {"raw": 1}},
    "defaultKeyStatistics": {"forwardEps": {"raw": 1.1, "fmt": "1.1"}},
    "earningsTrend": {
        "trend": [
            {
                "period": "0y",
                "maxAge": 1,
                "earningsEstimate": {
                    "avg": {"raw": 1.0, "fmt": "1"},
                    "low": {},
                    "numberOfAnalysts": {"raw": 9},
                },
            }
        ]
    },
    "recommendationTrend": {"trend": [{"period": "0m", "buy": 5, "hold": 2}]},
}


def test_trim_keeps_raw_numbers_and_only_the_named_fields():
    t = C.trim(SUMMARY)
    assert t["price"] == {"regularMarketPrice": 10.5, "shortName": "Acme"}
    assert t["financialData"] == {"targetMeanPrice": 12.0}
    assert t["earningsTrend"] == [{"period": "0y", "earningsEstimate": {"avg": 1.0, "numberOfAnalysts": 9}}]
    assert t["recommendationTrend"][0]["buy"] == 5


def test_trim_survives_missing_modules():
    t = C.trim({})
    assert t["earningsTrend"] == [] and t["price"] == {}


def test_write_round_trips_and_names_the_file_by_date(tmp_path):
    p = C.write({"A": C.trim(SUMMARY)}, {"B": "boom"}, tmp_path, today="2026-11-06")
    assert p.name == "2026-11-06.json.gz"
    j = json.loads(gzip.decompress(p.read_bytes()))
    assert j["asof"] == "2026-11-06" and j["tickers"]["A"]["price"]["regularMarketPrice"] == 10.5
    assert j["errors"] == {"B": "boom"}


def test_main_fails_when_too_few_tickers_come_back(tmp_path, monkeypatch):
    uni = tmp_path / "u.txt"
    uni.write_text("A\nB\nC\nD\nE\n")
    monkeypatch.setattr(C, "snapshot", lambda tickers: ({"A": {}}, dict.fromkeys("BCDE", "x")))
    assert C.main([str(uni), "--out", str(tmp_path)]) == 1
    monkeypatch.setattr(C, "snapshot", lambda tickers: ({t: {} for t in "ABCDE"}, {}))
    assert C.main([str(uni), "--out", str(tmp_path)]) == 0
