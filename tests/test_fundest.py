"""
Tests for core.fundest - the Day estimate arithmetic. Synthetic frames only.

    python -m pytest tests/test_fundest.py -q
"""
import numpy as np
import pandas as pd
import pytest

from core import fundest as fe


def _long(series: dict) -> pd.DataFrame:
    """{fund_id: {date: close}} -> long frame."""
    rows = [{"fund_id": f, "date": d, "close": c}
            for f, s in series.items() for d, c in s.items()]
    return pd.DataFrame(rows)


DAYS = ["2026-11-02", "2026-11-03", "2026-11-04"]


def test_fx_fund_id():
    assert fe.fx_fund_id("USD") == "YF:GBPUSD=X"
    assert fe.fx_fund_id("gbp") is None
    assert fe.fx_fund_id(None) is None


def test_scaled_estimate_with_fx():
    closes = _long({
        "YF:AAA": dict(zip(DAYS, [100, 100, 110])),        # USD, +10%
        "YF:BBB.L": dict(zip(DAYS, [500, 500, 495])),      # GBp, -1%
        "YF:GBPUSD=X": dict(zip(DAYS, [1.30, 1.30, 1.313])),  # pound +1%
    })
    holdings = pd.DataFrame({"name": ["A", "B"],
                             "fund_id": ["YF:AAA", "YF:BBB.L"],
                             "weight_pct": [30.0, 10.0]})
    cur = {"YF:AAA": "USD", "YF:BBB.L": "GBP"}
    s, d = fe.estimate(holdings, closes, cur, {"YF:AAA", "YF:BBB.L"},
                       "2026-11-04")
    a_gbp = 1.10 / 1.01 - 1
    assert s["local"] == pytest.approx((30 * 0.10 + 10 * -0.01) / 40)
    assert s["gbp"] == pytest.approx((30 * a_gbp + 10 * -0.01) / 40)
    assert s["fx"] == pytest.approx(s["gbp"] - s["local"])
    assert s["method"] == "scaled" and s["priced_weight"] == 40.0
    # Contributions add up to the estimate.
    assert d["contribution"].sum() == pytest.approx(s["gbp"])
    a = d.set_index("name").loc["A"]
    assert (1 + a["local"]) * (1 + a["fx"]) == pytest.approx(1 + a["gbp"])


def test_not_traded_gets_fx_only_and_statuses():
    closes = _long({
        "YF:US": {"2026-11-02": 50, "2026-11-03": 51},   # no row on the 4th
        "YF:GBPUSD=X": dict(zip(DAYS, [1.30, 1.30, 1.326])),  # pound +2%
        "YF:EMPTY": {},
    })
    holdings = pd.DataFrame({
        "name": ["US", "Gone", "Unmapped"],
        "fund_id": ["YF:US", "YF:NOTHERE", None],
        "weight_pct": [20.0, 5.0, 3.0]})
    s, d = fe.estimate(holdings, closes, {"YF:US": "USD"}, {"YF:US"},
                       "2026-11-04")
    row = d.set_index("name")
    assert row.at["US", "status"] == fe.STATUS_NO_MOVE
    assert row.at["US", "local"] == 0.0
    assert row.at["US", "gbp"] == pytest.approx(1 / 1.02 - 1)
    assert row.at["US", "fx"] == pytest.approx(1 / 1.02 - 1)   # effect
    assert row.at["Gone", "status"] == fe.STATUS_MISSING
    assert row.at["Unmapped", "status"] == fe.STATUS_UNMAPPED
    assert s["priced_weight"] == 20.0 and s["moved_weight"] == 0.0
    assert s["listed_weight"] == 28.0


def test_date_with_no_prices_yet_is_no_move():
    closes = _long({"YF:X": {"2026-11-02": 10, "2026-11-03": 11}})
    holdings = pd.DataFrame({"name": ["X"], "fund_id": ["YF:X"],
                             "weight_pct": [50.0]})
    s, d = fe.estimate(holdings, closes, {"YF:X": "GBP"}, {"YF:X"},
                       "2026-11-04")
    assert d.at[0, "status"] == fe.STATUS_NO_MOVE
    assert s["gbp"] == 0.0


def test_proxy_fills_the_rest():
    closes = _long({
        "YF:A": dict(zip(DAYS, [10, 10, 11])),       # +10%
        "YF:P": dict(zip(DAYS, [100, 100, 102])),    # proxy +2%
    })
    holdings = pd.DataFrame({"name": ["A"], "fund_id": ["YF:A"],
                             "weight_pct": [40.0]})
    cur = {"YF:A": "GBP", "YF:P": "GBP"}
    s, d = fe.estimate(holdings, closes, cur, {"YF:A", "YF:P"}, "2026-11-04",
                       residual_id="YF:P")
    assert s["gbp"] == pytest.approx((40 * 0.10 + 60 * 0.02) / 100)
    assert s["method"] == "proxy P"
    assert d["contribution"].sum() == pytest.approx(s["gbp"])
    assert d.iloc[-1]["name"].startswith("Rest of fund")


def test_hedged_headline_is_local():
    closes = _long({
        "YF:A": dict(zip(DAYS, [10, 10, 11])),
        "YF:GBPUSD=X": dict(zip(DAYS, [1.3, 1.3, 1.4])),
    })
    holdings = pd.DataFrame({"name": ["A"], "fund_id": ["YF:A"],
                             "weight_pct": [100.0]})
    s, _ = fe.estimate(holdings, closes, {"YF:A": "USD"}, {"YF:A"},
                       "2026-11-04", hedged=True)
    assert s["headline"] == pytest.approx(0.10)
    assert s["method"] == "full"


def test_actual_returns_drop_repeats_and_convert():
    closes = _long({
        "F": {"2026-11-02": 1.0, "2026-11-03": 1.0, "2026-11-04": 1.1},
        "YF:GBPUSD=X": dict(zip(DAYS, [1.3, 1.3, 1.3 * 1.05])),
    })
    gbp = fe.actual_returns(closes, "F", "USD", "pound")
    assert list(gbp.index) == [pd.Timestamp("2026-11-04")]
    assert gbp.iloc[0] == pytest.approx(1.1 / 1.05 - 1)
    usd = fe.actual_returns(closes, "F", "USD", "pound", hedged=True)
    assert usd.iloc[0] == pytest.approx(0.10)


def test_backtest_finds_next_day_alignment():
    dates = pd.bdate_range("2026-08-03", periods=40)
    rng = np.random.default_rng(7)
    moves = rng.normal(0, 0.01, len(dates))
    level = 100 * np.cumprod(1 + moves)
    stock = {d.strftime("%Y-%m-%d"): v for d, v in zip(dates, level)}
    # The fund reports each day's move one business day later.
    fund_level = [100.0] + list(100 * np.cumprod(1 + moves[:-1]))
    fund = {d.strftime("%Y-%m-%d"): v for d, v in zip(dates, fund_level)}
    closes = _long({"YF:S": stock, "FUND": fund})
    holdings = pd.DataFrame({"name": ["S"], "fund_id": ["YF:S"],
                             "weight_pct": [100.0]})
    actual = fe.actual_returns(closes, "FUND", "GBP", "pound")
    bt = fe.backtest(holdings, closes, {"YF:S": "GBP"}, {"YF:S"}, actual)
    f = fe.fit(bt)
    assert f["best"] == "next"
    assert f["next"]["mae"] == pytest.approx(0.0, abs=1e-12)
    assert f["same"]["mae"] > 0.001
