"""core.risk: Vol, Max DD, Sharpe and Sortino while held, on hand-checkable
series, plus the database loader on a scratch file. Synthetic data only.

    python -m pytest tests/test_risk.py -q
"""
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import risk

ASOF = pd.Timestamp("2026-11-04")


def series(start, values):
    return pd.Series(values, index=pd.date_range(start, periods=len(values)),
                     dtype=float)


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------

def test_max_drawdown_is_the_worst_fall_from_a_peak():
    # Index 1.20, 0.90, 1.00: the fall from 1.20 to 0.90 is -25%.
    s = risk.stats(series("2026-01-02", [0.20, -0.25, 1.0 / 0.9 - 1]))
    assert s["max_dd"] == pytest.approx(-0.25)


def test_a_fall_on_the_first_day_counts_as_drawdown():
    s = risk.stats(series("2026-01-02", [-0.10, 0.05]))
    assert s["max_dd"] == pytest.approx(-0.10)


def test_vol_sharpe_sortino_match_the_formulas():
    r = series("2026-01-02", [0.01, -0.02, 0.015, 0.0, -0.005, 0.02])
    rf = pd.Series(0.0001, index=r.index)
    s = risk.stats(r, rf)
    ex = r - 0.0001
    vol = r.std(ddof=1) * math.sqrt(365)
    down = math.sqrt((ex.clip(upper=0) ** 2).mean()) * math.sqrt(365)
    assert s["vol"] == pytest.approx(vol)
    assert s["sharpe"] == pytest.approx(ex.mean() * 365 / vol)
    assert s["sortino"] == pytest.approx(ex.mean() * 365 / down)
    assert s["n_days"] == 6


def test_risk_free_lowers_sharpe_by_rate_over_vol():
    r = series("2026-01-02", [0.01, -0.01, 0.02, -0.015, 0.005] * 20)
    rate = 0.0365                                     # 3.65% a year
    with_rf = risk.stats(r, pd.Series(rate / 365, index=r.index))
    without = risk.stats(r)
    assert without["sharpe"] - with_rf["sharpe"] == pytest.approx(rate / with_rf["vol"])


def test_too_short_or_flat_series_give_blanks_not_errors():
    assert risk.stats(pd.Series(dtype=float))["max_dd"] is None
    one = risk.stats(series("2026-01-02", [0.01]))
    assert one["max_dd"] == 0.0 and one["vol"] is None and one["sharpe"] is None
    flat = risk.stats(series("2026-01-02", [0.0, 0.0, 0.0]))
    assert flat["vol"] is None and flat["sharpe"] is None and flat["sortino"] is None


def test_no_down_days_gives_no_sortino():
    s = risk.stats(series("2026-01-02", [0.01, 0.02, 0.005]))
    assert s["sharpe"] is not None and s["sortino"] is None


# --------------------------------------------------------------------------
# position_daily
# --------------------------------------------------------------------------

def test_only_days_held_at_the_previous_close_count():
    px = series("2026-01-01", [100, 110, 121, 133.1, 146.41])   # +10% a day
    qty = pd.Series([10.0, 0.0], index=pd.to_datetime(["2026-01-02", "2026-01-04"]))
    frame, missing = risk.position_daily(qty, px, pd.Timestamp("2026-01-05"))
    # Bought on the 2nd (not held at the 1st's close), sold on the 4th: the
    # 3rd and 4th are the held days. The 5th is after the exit.
    assert list(frame.index.day) == [3, 4]
    assert frame["r"].tolist() == pytest.approx([0.10, 0.10])
    assert frame["v_prev"].tolist() == pytest.approx([1100.0, 1210.0])
    assert missing == 0


def test_weekends_carry_the_last_close_so_they_return_zero():
    # Fri 2026-01-02 100, Mon 2026-01-05 105: Sat and Sun return 0.
    px = pd.Series([100.0, 105.0], index=pd.to_datetime(["2026-01-02", "2026-01-05"]))
    qty = pd.Series([1.0], index=pd.to_datetime(["2026-01-01"]))
    frame, missing = risk.position_daily(qty, px, pd.Timestamp("2026-01-05"))
    assert frame["r"].tolist() == pytest.approx([0.0, 0.0, 0.05])
    assert missing == 1, "the 2nd has no close for the 1st to compare with"


def test_size_changes_do_not_change_the_return():
    px = series("2026-01-01", [100, 110, 99, 108.9])
    one = pd.Series([1.0], index=pd.to_datetime(["2026-01-01"]))
    many = pd.Series([1.0, 50.0], index=pd.to_datetime(["2026-01-01", "2026-01-02"]))
    a, _ = risk.position_daily(one, px, pd.Timestamp("2026-01-04"))
    b, _ = risk.position_daily(many, px, pd.Timestamp("2026-01-04"))
    assert a["r"].tolist() == pytest.approx(b["r"].tolist())


def test_a_gap_between_holdings_is_left_out():
    px = series("2026-01-01", [100, 110, 50, 200, 210])
    # Held 1st-2nd, out on the 2nd, back on the 4th: the crash and the jump
    # on the 3rd and 4th happened while out.
    qty = pd.Series([1.0, 0.0, 1.0],
                    index=pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-04"]))
    frame, _ = risk.position_daily(qty, px, pd.Timestamp("2026-01-05"))
    assert list(frame.index.day) == [2, 5]
    assert frame["r"].tolist() == pytest.approx([0.10, 0.05])


# --------------------------------------------------------------------------
# basket
# --------------------------------------------------------------------------

def test_a_perfect_hedge_has_no_basket_risk():
    up_down = series("2026-01-01", [100, 110, 99, 108.9, 98.01])
    mirror = 200 - up_down                 # gains exactly what the other loses
    qty = pd.Series([1.0], index=pd.to_datetime(["2026-01-01"]))
    a, _ = risk.position_daily(qty, up_down, pd.Timestamp("2026-01-05"))
    b, _ = risk.position_daily(qty, mirror, pd.Timestamp("2026-01-05"))
    both = risk.basket([a, b])
    assert risk.stats(a["r"])["vol"] > 1.0
    assert both["vol"] is None or both["vol"] < 1e-9
    assert both["max_dd"] == pytest.approx(0.0, abs=1e-12)


def test_basket_of_one_equals_the_position():
    px = series("2026-01-01", [100, 104, 101, 107, 103, 108])
    qty = pd.Series([3.0], index=pd.to_datetime(["2026-01-01"]))
    a, _ = risk.position_daily(qty, px, pd.Timestamp("2026-01-06"))
    assert risk.basket([a])["sharpe"] == pytest.approx(risk.stats(a["r"])["sharpe"])


def test_empty_basket_is_blank():
    assert risk.basket([])["sharpe"] is None


# --------------------------------------------------------------------------
# compute_risk on a scratch database
# --------------------------------------------------------------------------

@pytest.fixture
def risk_db(tmp_path, monkeypatch):
    """Three instruments with the same 1% daily move in their own units:
    GBP pounds, GBP pence and USD (with a flat FX rate), plus one that is
    flat now, and a Bank Rate series."""
    path = tmp_path / "risk.db"
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
        CREATE TABLE macro_series (id TEXT PRIMARY KEY, source TEXT, source_id TEXT);
        CREATE TABLE macro_observations (series_id TEXT, date TEXT, value REAL);
    """)
    con.executemany("INSERT INTO instruments VALUES (?,?,?,?,?,?)", [
        ("GBP.L", "Pounds", "Equity", "GBP", "pound", "UK"),
        ("PENCE.L", "Pence", "Equity", "GBP", "pence", "UK"),
        ("USD.US", "Dollars", "Equity", "USD", "pound", "US"),
        ("OUT.L", "Closed", "Equity", "GBP", "pound", "UK"),
        ("YF:GBPUSD=X", "GBPUSD", "FX", "GBP", "pound", "FX"),
    ])
    days = pd.date_range("2026-09-01", "2026-11-04")
    rets = np.where(np.arange(len(days)) % 2 == 0, 0.01, -0.005)
    level = 100 * np.cumprod(1 + rets)
    rows = []
    for d, v in zip(days, level):
        ds = d.strftime("%Y-%m-%d")
        rows += [("GBP.L", ds, v), ("PENCE.L", ds, v * 100), ("USD.US", ds, v * 1.25),
                 ("OUT.L", ds, v), ("YF:GBPUSD=X", ds, 1.25)]
    con.executemany("INSERT INTO prices (fund_id,date,close) VALUES (?,?,?)", rows)
    con.executemany(
        "INSERT INTO transactions (fund_id,account,trade_date,type,quantity,"
        "price,currency,fx_rate,commission) VALUES (?,?,?,?,?,?,?,?,?)", [
            ("GBP.L", "A", "2026-09-10", "BUY", 10, 100, "GBP", 1, 0),
            ("PENCE.L", "A", "2026-09-10", "BUY", 10, 10000, "GBP", 1, 0),
            ("USD.US", "A", "2026-09-10", "BUY", 10, 125, "USD", 1.25, 0),
            ("OUT.L", "A", "2026-09-10", "BUY", 10, 100, "GBP", 1, 0),
            ("OUT.L", "A", "2026-10-01", "SELL", 10, 100, "GBP", 1, 0),
        ])
    con.execute("INSERT INTO macro_series VALUES ('BANKRATE', 'BOE', 'IUDBEDR')")
    # Stored only on the date it changed, months before the first trade.
    con.execute("INSERT INTO macro_observations VALUES ('BANKRATE', '2026-01-01', 3.65)")
    con.commit()
    con.close()
    from core import config
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def test_every_price_unit_gives_the_same_risk(risk_db):
    from core.roce import load_trades
    book = risk.compute_risk(load_trades(), ASOF)
    assert book.rf_source == "BANKRATE"
    gbp = book.by_id["GBP.L"]
    for fid in ("PENCE.L", "USD.US"):
        other = book.by_id[fid]
        assert other.vol == pytest.approx(gbp.vol), fid
        assert other.sharpe == pytest.approx(gbp.sharpe), fid
        assert other.max_dd == pytest.approx(gbp.max_dd), fid
    assert gbp.n_days == (ASOF - pd.Timestamp("2026-09-10")).days
    assert gbp.missing_days == 0


def test_bank_rate_is_the_risk_free_rate(risk_db, monkeypatch):
    from core.roce import load_trades
    trades = load_trades()
    with_rf = risk.compute_risk(trades, ASOF).by_id["GBP.L"]
    monkeypatch.setattr(risk, "RISK_FREE", None)
    book = risk.compute_risk(trades, ASOF)
    assert book.rf_source == "zero"
    without = book.by_id["GBP.L"]
    assert without.sharpe - with_rf.sharpe == pytest.approx(0.0365 / with_rf.vol)


def test_closed_position_stops_at_the_exit(risk_db):
    from core.roce import load_trades
    out = risk.compute_risk(load_trades(), ASOF).by_id["OUT.L"]
    assert out.n_days == (pd.Timestamp("2026-10-01") - pd.Timestamp("2026-09-10")).days
    assert out.daily.index.max() == pd.Timestamp("2026-10-01")


def test_missing_bank_rate_falls_back_to_zero_and_says_so(risk_db):
    con = sqlite3.connect(risk_db)
    con.execute("DELETE FROM macro_observations")
    con.commit()
    con.close()
    from core.roce import load_trades
    assert risk.compute_risk(load_trades(), ASOF).rf_source == "zero (no data)"
