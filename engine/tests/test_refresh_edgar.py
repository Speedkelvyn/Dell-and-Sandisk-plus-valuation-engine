import gzip
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import edgar  # noqa: E402
import refresh_edgar  # noqa: E402


def _read(path):
    with gzip.open(path, "rt") as f:
        return json.load(f)


def _stub(tmp_path, monkeypatch, fail):
    for t in ("AAA", "BBB"):
        with gzip.open(tmp_path / f"{t}_edgar.json.gz", "wt") as f:
            f.write('{"old": true}')

    def fake(t, since):
        if t in fail:
            raise LookupError("not a registrant")
        return {"facts": {"Revenues": {}}}

    monkeypatch.setattr(edgar, "fetch", fake)


def test_refresh_rewrites_files_and_keeps_old_ones_when_a_ticker_fails(tmp_path, monkeypatch):
    _stub(tmp_path, monkeypatch, fail={"BBB"})
    done, failed = refresh_edgar.refresh(tmp_path, pause=0)
    assert done == ["AAA"] and list(failed) == ["BBB"]
    assert _read(tmp_path / "AAA_edgar.json.gz")["facts"]
    assert _read(tmp_path / "BBB_edgar.json.gz") == {"old": True}
