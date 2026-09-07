"""Regression tests for the core.roce fixes.

    python -m pytest tests/test_roce_edges.py -v

These cover the data-access and edge-case behaviour that tests/test_roce.py
does not: it exercises compute_position and aggregate on three clean ledgers,
which is why none of the bugs below showed up there.
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ASOF = pd.Timestamp("2026-07-01")


# --------------------------------------------------------------------------
# A synthetic database covering every conversion path
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def probe_db(tmp_path_factory):
    """Four instruments: GBP major, GBP pence, USD, and one with no price."""
    path = tmp_path_factory.mktemp("roce") / "probe.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE instruments (fund_id TEXT PRIMARY KEY, name TEXT,
                                  asset_type TEXT, currency TEXT,
                                  price_unit TEXT, category TEXT);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  fund_id TEXT, account TEXT, trade_date TEXT,
                                  type TEXT, quantity REAL, price REAL,
                                  currency TEXT, fx_rate REAL, commission REAL);
        CREATE TABLE prices (id INTEGER PRIMARY KEY AUTOINCREMENT, fund_id TEXT,
                             date TEXT, open REAL, high REAL, low REAL,
                             close REAL, volume INTEGER);
    """)
    con.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?)", [
        ("GBP.L",   "GBP major", "Equity", "GBP", "pound", "UK"),
        ("PENCE.L", "GBX pence", "Equity", "GBP", "pence", "UK"),
        ("USD.US",  "USD stock", "Equity", "USD", "pound", "US"),
        ("NOPX",    "No price",  "Equity", "GBP", "pound", "UK"),
    ])
    # Each buy is £1,000 of stock plus £4 commission => cash_flow -1004.
    con.executemany(
        "INSERT INTO transactions (fund_id,account,trade_date,type,quantity,"
        "price,currency,fx_rate,commission) VALUES (?,?,?,?,?,?,?,?,?)", [
            ("GBP.L",   "A", "2026-01-01", "BUY", 100, 10.0,   "GBP", 1.0,  4.0),
            ("PENCE.L", "A", "2026-01-01", "BUY", 100, 1000.0, "GBP", 1.0,  4.0),
            ("USD.US",  "A", "2026-01-01", "BUY", 100, 12.50,  "USD", 1.25, 5.0),
            ("NOPX",    "A", "2026-01-01", "BUY", 100, 10.0,   "GBP", 1.0,  0.0),
        ])
    con.executemany("INSERT INTO prices (fund_id,date,close) VALUES (?,?,?)", [
        ("GBP.L",   "2026-06-01", 11.0),     # £11.00
        ("PENCE.L", "2026-06-01", 1100.0),   # 1100p = £11.00
        ("USD.US",  "2026-06-01", 13.75),    # $13.75 / 1.25 = £11.00
        # The live cross the whole app marks USD positions with.
        ("YF:GBPUSD=X", "2026-06-01", 1.25),
        # NOPX deliberately absent.
    ])
    con.commit()
    con.close()
    return path


@pytest.fixture
def roce(probe_db, monkeypatch):
    from core import config
    import core.roce as _roce
    monkeypatch.setattr(config, "DB_PATH", probe_db)
    return _roce


# --------------------------------------------------------------------------
# Conversions
# --------------------------------------------------------------------------

def test_all_price_units_converge_on_the_same_gbp_cash_flow(roce):
    """Pence, pounds and USD all describe the same £1,004 outlay."""
    t = roce.load_trades().set_index("instrument")
    assert t.loc["GBP.L", "cash_flow"] == pytest.approx(-1004.0)
    assert t.loc["PENCE.L", "cash_flow"] == pytest.approx(-1004.0)
    assert t.loc["USD.US", "cash_flow"] == pytest.approx(-1004.0)


def test_latest_prices_convert_to_gbp(roce):
    prices, approximated, _ = roce.load_latest_prices()
    assert prices["GBP.L"] == pytest.approx(11.0)
    assert prices["PENCE.L"] == pytest.approx(11.0)
    # USD marked at the LIVE cross (YF:GBPUSD=X = 1.25), not the trade rate.
    assert prices["USD.US"] == pytest.approx(11.0)
    # Nothing is an approximation any more.
    assert approximated == []


# --------------------------------------------------------------------------
# BUG 1 — a missing mark must not read as a total loss
# --------------------------------------------------------------------------

def test_missing_price_is_absent_not_zero(roce):
    prices, _, _ = roce.load_latest_prices()
    assert "NOPX" not in prices, "a missing mark must not be stored as 0.0"


def test_open_position_without_a_mark_reports_realised_only(roce):
    by_id = {p.instrument: p for p in roce.compute_all(asof=ASOF)}
    nopx = by_id["NOPX"]
    assert nopx.priced is False
    assert nopx.status == "unpriced"
    assert nopx.unrealised == 0.0
    assert nopx.total_pnl == 0.0, "previously reported -1000.00, the whole book cost"


def test_unpriced_positions_are_excluded_from_the_basket(roce):
    members = roce.compute_all(asof=ASOF)
    basket = roce.aggregate(members)
    assert [p.instrument for p in basket.excluded] == ["NOPX"]
    assert all(p.priced for p in basket.members)


def test_closed_position_needs_no_mark():
    """qty == 0 means unrealised is genuinely zero, so priced stays True."""
    from core import roce as r
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-02-01"]),
        "qty": [100, -100],
        "cash_flow": [-1000.0, 1150.0],
    })
    p = r.compute_position(df, latest_price=None, asof=ASOF, instrument="C")
    assert p.priced is True
    assert p.status == "closed"
    assert p.total_pnl == pytest.approx(150.0)


# --------------------------------------------------------------------------
# BUG 2 — two sells on one date are two events
# --------------------------------------------------------------------------

def test_same_date_sells_are_not_collapsed():
    from core import roce as r
    df = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-02-01", "2026-02-01"]),
        "qty": [200, -100, -100],
        "cash_flow": [-2000.0, 1200.0, 1300.0],
    })
    p = r.compute_position(df, latest_price=0.0, asof=ASOF, instrument="X")
    assert p.realised == pytest.approx(500.0)
    assert len(p.realised_events) == 2, "dict() kept only the last event"
    assert p.realised_events.sum() == pytest.approx(p.realised)


# --------------------------------------------------------------------------
# BUG 3 — P&L with no capital employed is named, not a silent identity break
# --------------------------------------------------------------------------

def test_same_day_round_trip_is_reported_by_validate():
    from core import roce as r
    same = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-05", "2026-01-05"]),
        "qty": [100, -100],
        "cash_flow": [-1000.0, 1150.0],
    })
    held = pd.DataFrame({
        "date": pd.to_datetime(["2026-01-01", "2026-03-01"]),
        "qty": [200, -200],
        "cash_flow": [-2000.0, 2400.0],
    })
    a = r.compute_position(same, 0.0, ASOF, "SameDay")
    b = r.compute_position(held, 0.0, ASOF, "Held")
    assert a.capital_days == 0.0 and a.total_pnl == pytest.approx(150.0)

    basket = r.aggregate([a, b])
    trades = pd.concat([same.assign(instrument="SameDay"),
                        held.assign(instrument="Held")])
    named = {label: (ok, detail) for label, ok, detail in r.validate([a, b], basket, trades)}
    ok, detail = named["No P&L without capital employed"]
    assert ok is False and "SameDay" in detail


# --------------------------------------------------------------------------
# BUG 4 — one broken ledger must not take down every instrument
# --------------------------------------------------------------------------

def test_compute_position_still_rejects_an_oversell():
    """The primitive stays strict; only compute_all is forgiving."""
    from core import roce as r
    bad = pd.DataFrame({"date": pd.to_datetime(["2026-01-01"]),
                        "qty": [-50], "cash_flow": [500.0]})
    with pytest.raises(ValueError, match="exceeds holding"):
        r.compute_position(bad, 0.0, ASOF, instrument="OVERSOLD")


def test_broken_ledger_becomes_a_row_not_an_exception(roce, probe_db):
    """A sell with no matching buy must not blank the whole page."""
    con = sqlite3.connect(probe_db)
    con.execute(
        "INSERT INTO transactions (fund_id,account,trade_date,type,quantity,"
        "price,currency,fx_rate,commission) VALUES "
        "('OVERSOLD','A','2026-02-01','SELL',50,10.0,'GBP',1.0,0.0)")
    con.execute("INSERT INTO instruments VALUES "
                "('OVERSOLD','Oversold','Equity','GBP','pound','UK')")
    con.commit()
    con.close()

    members = roce.compute_all(asof=ASOF)          # must not raise
    by_id = {p.instrument: p for p in members}
    assert by_id["OVERSOLD"].status == "error"
    assert "exceeds holding" in by_id["OVERSOLD"].error
    # The healthy instruments still came through.
    assert by_id["GBP.L"].total_pnl == pytest.approx(96.0)

    basket = roce.aggregate(members)
    assert "OVERSOLD" in [p.instrument for p in basket.excluded]


# --------------------------------------------------------------------------
# BUG 5 — the ROCI mark must match the P&L tab's mark
# --------------------------------------------------------------------------

def test_usd_position_uses_live_fx_not_the_trade_rate(tmp_path, monkeypatch):
    """Regression for SEMI.L: bought at 1.25, cross now 1.184.

    The old code marked the position at the trade's 1.25, understating it by
    5.6% and reporting a -6,190 loss where the P&L tab showed -1,750.
    """
    path = tmp_path / "fx.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE instruments (fund_id TEXT PRIMARY KEY, name TEXT,
                                  asset_type TEXT, currency TEXT,
                                  price_unit TEXT, category TEXT);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT,
                                  fund_id TEXT, account TEXT, trade_date TEXT,
                                  type TEXT, quantity REAL, price REAL,
                                  currency TEXT, fx_rate REAL, commission REAL);
        CREATE TABLE prices (id INTEGER PRIMARY KEY AUTOINCREMENT, fund_id TEXT,
                             date TEXT, open REAL, high REAL, low REAL,
                             close REAL, volume INTEGER);
    """)
    con.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?)", [
        ("SEMI.L",      "Semis ETF", "Equity", "USD", "pound", "Semi"),
        ("YF:GBPUSD=X", "GBPUSD",    "FX",     "GBP", "pound", "FX"),
    ])
    # Bought 1,000 at $20.00 when the cross was 1.25 => £16.00, book £16,000.
    con.execute("INSERT INTO transactions (fund_id,account,trade_date,type,"
                "quantity,price,currency,fx_rate,commission) VALUES "
                "('SEMI.L','A','2026-01-01','BUY',1000,20.0,'USD',1.25,0.0)")
    # Mark $19.00. At the live 1.184 that is £16.05 - a small gain.
    # At the stale trade rate of 1.25 it would be £15.20 - a £800 loss.
    con.executemany("INSERT INTO prices (fund_id,date,close) VALUES (?,?,?)", [
        ("SEMI.L",      "2026-06-01", 19.00),
        ("YF:GBPUSD=X", "2026-06-01", 1.184),
    ])
    con.commit(); con.close()

    from core import config
    import core.roce as r
    monkeypatch.setattr(config, "DB_PATH", path)

    prices, approximated, _ = r.load_latest_prices()
    assert prices["SEMI.L"] == pytest.approx(19.00 / 1.184, rel=1e-9)
    assert approximated == [], "no mark should be an approximation now"

    p = {x.instrument: x for x in r.compute_all(asof=ASOF)}["SEMI.L"]
    assert p.book_cost == pytest.approx(16000.0)
    assert p.unrealised == pytest.approx(1000 * (19.00 / 1.184) - 16000.0)
    assert p.unrealised > 0, "stale 1.25 would have shown a loss here"
