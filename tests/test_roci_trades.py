"""Last buy / last sell on P&L ROCI: the engine fields (core.roce) and the
page's cell builder (pages.pnl_analysis). Synthetic data only.

    python -m pytest tests/test_roci_trades.py -q
"""
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import roce as r

ASOF = pd.Timestamp("2026-11-02")


def ledger(rows):
    """rows: (date, qty, price). cash_flow is qty * price, sign by side."""
    df = pd.DataFrame(rows, columns=["date", "qty", "px_native"])
    df["date"] = pd.to_datetime(df["date"])
    df["cash_flow"] = -df["qty"] * df["px_native"]
    return df


# --------------------------------------------------------------------------
# Engine
# --------------------------------------------------------------------------

def test_last_buy_combines_same_day_fills_by_quantity():
    df = ledger([
        ("2026-05-14", 100, 390.0),
        ("2026-10-20", 20, 505.0),
        ("2026-10-20", 10, 499.0),
    ])
    p = r.compute_position(df, latest_price=520.0, asof=ASOF)
    assert p.last_buy_date == pd.Timestamp("2026-10-20")
    assert p.last_buy_px == pytest.approx((20 * 505 + 10 * 499) / 30)   # 503
    assert p.ret_since_buy == pytest.approx(520 / 503 - 1)
    assert p.last_sell_date is None and p.last_sell_px is None
    assert p.ret_since_sell is None
    assert p.held_since == pd.Timestamp("2026-05-14")


def test_last_sell_return_is_positive_when_the_price_rose_after_selling():
    df = ledger([("2026-01-05", 1000, 10.0), ("2026-08-20", -400, 11.5)])
    p = r.compute_position(df, latest_price=12.65, asof=ASOF)
    assert p.last_sell_date == pd.Timestamp("2026-08-20")
    assert p.last_sell_px == pytest.approx(11.5)
    assert p.ret_since_sell == pytest.approx(0.10)
    assert p.ret_since_buy == pytest.approx(0.265)


def test_reopened_holding_starts_again_from_the_rebuy():
    df = ledger([
        ("2025-11-20", 800, 5.8),
        ("2026-03-17", -800, 7.3),      # out
        ("2026-05-07", 1000, 8.1),      # back in
    ])
    p = r.compute_position(df, latest_price=9.4, asof=ASOF)
    assert p.held_since == pd.Timestamp("2026-05-07")
    assert p.last_sell_date == pd.Timestamp("2026-03-17")
    assert p.last_buy_date == pd.Timestamp("2026-05-07")


def test_closed_position_has_no_holding_start():
    df = ledger([("2025-07-28", 1000, 14.0), ("2026-06-25", -1000, 14.6)])
    p = r.compute_position(df, latest_price=16.1, asof=ASOF)
    assert p.status == "closed"
    assert p.held_since is None
    assert p.ret_since_sell == pytest.approx(16.1 / 14.6 - 1)


def test_dividends_are_not_trades():
    df = ledger([("2026-01-05", 100, 10.0)])
    div = pd.DataFrame({"date": pd.to_datetime(["2026-09-01"]), "qty": [0.0],
                        "px_native": [float("nan")], "cash_flow": [25.0]})
    p = r.compute_position(pd.concat([df, div]), latest_price=11.0, asof=ASOF)
    assert p.last_buy_date == pd.Timestamp("2026-01-05")
    assert p.last_sell_date is None


def test_ledger_without_prices_still_gives_dates():
    """Older callers (tests, why_disagree.py) pass no px_native column."""
    df = ledger([("2026-01-05", 100, 10.0)]).drop(columns="px_native")
    p = r.compute_position(df, latest_price=11.0, asof=ASOF)
    assert p.last_buy_date == pd.Timestamp("2026-01-05")
    assert p.last_buy_px is None and p.ret_since_buy is None


def test_missing_mark_gives_no_return():
    df = ledger([("2026-01-05", 100, 10.0)])
    p = r.compute_position(df, latest_price=None, asof=ASOF)
    assert p.ret_since_buy is None


def test_non_gbp_position_uses_the_native_mark():
    """USD: fill and mark both in dollars, so the GBP mark is not used."""
    df = ledger([("2026-01-05", 100, 20.0)])
    p = r.compute_position(df, latest_price=15.0, asof=ASOF, currency="USD",
                           native_latest_price=22.0, fx_now=1.30)
    assert p.ret_since_buy == pytest.approx(0.10)


@pytest.fixture
def units_db(tmp_path, monkeypatch):
    """Same £10 -> £11 move quoted in pounds, pence and dollars."""
    path = tmp_path / "units.db"
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
        ("GBP.L", "GBP major", "Equity", "GBP", "pound", "UK"),
        ("PENCE.L", "GBX pence", "Equity", "GBP", "pence", "UK"),
        ("USD.US", "USD stock", "Equity", "USD", "pound", "US"),
    ])
    con.executemany(
        "INSERT INTO transactions (fund_id,account,trade_date,type,quantity,"
        "price,currency,fx_rate,commission) VALUES (?,?,?,?,?,?,?,?,?)", [
            ("GBP.L", "A", "2026-01-01", "BUY", 100, 10.0, "GBP", 1.0, 4.0),
            ("PENCE.L", "A", "2026-01-01", "BUY", 100, 1000.0, "GBP", 1.0, 4.0),
            ("USD.US", "A", "2026-01-01", "BUY", 100, 12.50, "USD", 1.25, 5.0),
            ("GBP.L", "A", "2026-03-01", "DIVIDEND", 30, 1.0, "GBP", 1.0, 0.0),
        ])
    con.executemany("INSERT INTO prices (fund_id,date,close) VALUES (?,?,?)", [
        ("GBP.L", "2026-10-30", 11.0),
        ("PENCE.L", "2026-10-30", 1100.0),
        ("USD.US", "2026-10-30", 13.75),
        ("YF:GBPUSD=X", "2026-10-30", 1.25),
    ])
    con.commit()
    con.close()
    from core import config
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def test_fill_prices_are_major_units_and_commission_free(units_db):
    t = r.load_trades()
    buys = t[t["type"] == "BUY"].set_index("instrument")
    assert buys.loc["GBP.L", "px_native"] == pytest.approx(10.0)
    assert buys.loc["PENCE.L", "px_native"] == pytest.approx(10.0)
    assert buys.loc["USD.US", "px_native"] == pytest.approx(12.50)
    assert t.loc[t["type"] == "DIVIDEND", "px_native"].isna().all()


def test_every_price_unit_reports_the_same_return(units_db):
    by_id = {p.instrument: p for p in r.compute_all(asof=ASOF)}
    for fid in ("GBP.L", "PENCE.L", "USD.US"):
        assert by_id[fid].ret_since_buy == pytest.approx(0.10), fid
        assert by_id[fid].mark_date == pd.Timestamp("2026-10-30")


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw, short", [
    ("iShares MSCI Global Semiconductors UCITS ETF (SEMI.L)",
     "iShares MSCI Global Semiconductors (SEMI.L)"),
    ("iShares Nasdaq US Biotechnology UCITS ETF GBP (BTEK.L)",
     "iShares Nasdaq US Biotechnology (BTEK.L)"),
    ("UBS CMCI Composite SF UCITS ETF USD (UC14.L)", "UBS CMCI Composite SF (UC14.L)"),
    ("Microsoft Corporation (MSFT)", "Microsoft (MSFT)"),
    ("S&P Global Inc. (SPGI)", "S&P Global (SPGI)"),
    ("Cheniere Energy Inc (LNG)", "Cheniere Energy (LNG)"),
    ("Alphabet Inc.", "Alphabet"),
    ("Shell plc", "Shell"),
    ("AstraZeneca PLC", "AstraZeneca"),
    ("Türkiye Petrol Rafinerileri A.S.", "Türkiye Petrol Rafinerileri"),
    # Left alone: no filler, or the word only starts with a filler.
    ("HSBC Holdings", "HSBC Holdings"),
    ("Bitcoin GBP", "Bitcoin GBP"),
    ("Fidelity Asia Fund W Acc", "Fidelity Asia Fund W Acc"),
    ("Royal London Short Income Fund", "Royal London Short Income Fund"),
])
def test_short_name(raw, short):
    from pages.pnl_analysis import short_name
    assert short_name(raw) == short


def _cells(rows, latest=11.0, mark_date="2026-10-30"):
    from pages.pnl_analysis import trade_cells
    p = r.compute_position(ledger(rows), latest_price=latest, asof=ASOF,
                           mark_date=pd.Timestamp(mark_date))
    return trade_cells(p, ASOF)


def test_cells_for_a_held_position_trimmed_recently():
    c = _cells([("2026-01-05", 100, 10.0), ("2026-10-20", -20, 10.5)])
    assert c["last_buy"] == "2026-01-05" and c["last_sell"] == "2026-10-20"
    assert c["_buy_age"] == 301
    assert c["_sell_age"] == 13
    assert c["_hold_age"] == -1
    assert c["_stale"] == 0


def test_cells_for_a_never_sold_holding():
    c = _cells([("2026-07-01", 100, 10.0)])
    assert c["last_sell"] == "—" and c["ret_sell"] is None
    assert c["_sell_age"] == -1
    assert c["_hold_age"] == 124


def test_cells_for_a_holding_bought_back_after_an_exit():
    c = _cells([("2025-11-20", 800, 5.8), ("2026-03-17", -800, 7.3),
                ("2026-05-07", 1000, 8.1)])
    assert c["_sell_age"] == -1, "the sale was before this holding opened"
    assert c["_hold_age"] == 179


def test_cells_for_a_closed_position_have_no_date_colour():
    c = _cells([("2025-07-28", 1000, 14.0), ("2026-06-25", -1000, 14.6)])
    assert (c["_buy_age"], c["_sell_age"], c["_hold_age"]) == (-1, -1, -1)
    assert c["ret_sell"] == pytest.approx(11.0 / 14.6 - 1)


def test_old_mark_is_flagged_stale():
    c = _cells([("2026-07-01", 100, 10.0)], mark_date="2026-10-06")
    assert c["_stale"] == 1
    from pages.pnl_analysis import tooltips
    tip = tooltips([c], ASOF)[0]
    assert "2026-10-06" in tip["ret_buy"]


def test_tooltips_say_how_long_ago():
    from pages.pnl_analysis import tooltips
    c = _cells([("2026-01-05", 100, 10.0), ("2026-10-20", -20, 10.5)])
    tip = tooltips([c], ASOF)[0]
    assert tip["last_buy"] == "301 days ago"
    assert tip["last_sell"] == "13 days ago"
    never = tooltips([_cells([("2026-07-01", 100, 10.0)])], ASOF)[0]
    assert never["last_sell"] == "Never sold. Held 124 days without selling"


def test_age_bands_never_colour_a_blank():
    """Every date rule must exclude the -1 'no colour' marker."""
    from pages.pnl_analysis import BUY_AGE_BANDS, SELL_AGE_BANDS, _age_bands
    for rules in (_age_bands("_buy_age", "last_buy", BUY_AGE_BANDS),
                  _age_bands("_sell_age", "last_sell", SELL_AGE_BANDS)):
        assert all(">= 0" in x["if"]["filter_query"] for x in rules)


def test_optional_return_bands_guard_blanks_and_reverse_swaps_colours():
    from pages.pnl_analysis import NEG_FILL, POS_FILL, _rate_bands
    normal = _rate_bands("ret_buy", optional=True)
    rev = _rate_bands("ret_sell", reverse=True, optional=True)
    assert all(x["if"]["filter_query"].startswith("{ret_buy} is num")
               for x in normal)
    # Same band, opposite colour: a rise after selling is shown red.
    assert normal[0]["backgroundColor"] == POS_FILL[0]
    assert rev[0]["backgroundColor"] == NEG_FILL[0]


def test_no_two_adjacent_columns_share_a_bottom_header():
    """merge_duplicate_headers joins neighbours with the same name on any row,
    which once swallowed P&L 'Total' into ROCE 'Total'."""
    from pages.pnl_analysis import TABLE_COLUMNS
    bottom = [c["name"][1] for c in TABLE_COLUMNS]
    assert all(a != b for a, b in zip(bottom, bottom[1:])), bottom
