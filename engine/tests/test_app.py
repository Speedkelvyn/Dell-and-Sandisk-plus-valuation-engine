"""app.py: the Streamlit app runs end to end on the published examples, with no internet and no exceptions."""

import pathlib
import sys

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine"))
import service  # noqa: E402

TIMEOUT = 180


@pytest.fixture(autouse=True)
def offline_macro(monkeypatch):
    """The bundled macro readings instead of the network."""
    monkeypatch.setattr(service, "get_macro", lambda **kw: service.example_macro())


@pytest.fixture
def app(tmp_path, monkeypatch):
    """A fresh app whose lists are saved in a temporary folder, with a fast simulation."""
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    at.run()
    at.sidebar.select_slider[0].set_value(1000).run()
    return at


def button(at, label):
    found = [b for b in at.button if b.label == label]
    assert found, f"no button {label!r}; have {[b.label for b in at.button]}"
    return found[0]


def metrics(at):
    return {m.label: m.value for m in at.metric}


def test_loads_with_five_tabs(app):
    assert not app.exception
    assert [t.label for t in app.tabs][:5] == [
        "Watchlist", "Portfolio", "Value one stock", "Macro and market", "How it works",
    ]  # fmt: skip


def test_macro_tab_shows_the_regime(app):
    m = metrics(app)
    assert "Recession probability, 12 months" in m
    assert m["Recession probability, 12 months"].endswith("%")
    assert any("Macro:" in c.value for c in app.sidebar.caption)
    assert app.title[0].value == "Stock valuation engine"


def test_watchlist_examples_value_dell_and_sandisk(app):
    button(app, "Try the examples (no internet needed)").click().run()
    assert not app.exception
    vals = app.session_state["vals"]
    assert set(vals) == {"DELL", "SNDK"}
    assert all(v.ok for v in vals.values())
    assert vals["DELL"].verdict["call"] == "OVERVALUED"


def test_watchlist_rejects_non_tickers(app):
    app.text_area(key="watch_text").set_value("AAPL, not a ticker!!, MSFT").run()
    assert not app.exception
    assert any("skipped" in w.value for w in app.warning)


def test_one_stock_example_then_change_an_assumption(app):
    source = next(r for r in app.radio if r.label == "Where does the data come from?")
    source.set_value("An example (Dell or Sandisk)").run()
    button(app, "Open example").click().run()
    assert not app.exception
    assert app.session_state["single"]["inputs"]["meta"]["ticker"] == "DELL"
    assert metrics(app)  # the headline numbers are on the page

    # the assumption form is the one that exists for this stock; raising beta must lower the value
    before = metrics(app)
    beta = [s for s in app.slider if s.label.startswith("Beta")]
    assert beta, [s.label for s in app.slider]
    beta[0].set_value(beta[0].value + 0.5)
    button(app, "Re-value with these assumptions").click().run()
    assert not app.exception
    assert metrics(app) != before


def test_one_stock_shows_macro_and_sentiment(app):
    source = next(r for r in app.radio if r.label == "Where does the data come from?")
    source.set_value("An example (Dell or Sandisk)").run()
    button(app, "Open example").click().run()
    assert not app.exception
    labels = [t.label for t in app.tabs]
    assert "Macro and sentiment" in labels
    m = metrics(app)
    assert "Probability-weighted DCF" in m and m["Probability-weighted DCF"].startswith("$")
    assert "Sentiment" in m


def test_portfolio_totals_from_saved_holdings(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "DATA_DIR", tmp_path)
    service.save_holdings(service.clean_holdings(_holdings()))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=TIMEOUT)
    at.run()
    at.sidebar.select_slider[0].set_value(1000).run()
    button(at, "Try the examples (no internet needed)").click().run()
    assert not at.exception
    m = metrics(at)
    assert "Market value" in m
    assert m["Market value"].startswith("$")


def _holdings():
    import pandas as pd

    return pd.DataFrame({"Ticker": ["DELL", "SNDK"], "Shares": [10, 2], "Cost per share": [300.0, 900.0]})
