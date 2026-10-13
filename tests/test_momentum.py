"""Tests for core.momentum on synthetic price series - no database needed.

    python -m pytest tests/test_momentum.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import momentum as mom

END = pd.Timestamp("2026-10-13")
MEDIUM = mom.parse_horizon("6M,1M,1W", "6M,1M,1W")


def frame(fund_id, dates, closes):
    return pd.DataFrame({"fund_id": fund_id, "date": pd.DatetimeIndex(dates),
                         "close": np.asarray(closes, dtype=float)})


def weekdays(days=500, end=END):
    return pd.bdate_range(end - pd.Timedelta(days=days), end)


def zigzag(n, drift=0.001, noise=0.01, start=100.0):
    """Deterministic daily returns: drift +/- noise, alternating."""
    rets = drift + noise * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    rets[0] = 0.0
    return start * np.cumprod(1 + rets)


def by_hand(dates, closes, trend_days, recent_days, floor=mom.VOL_FLOOR,
            periods=252):
    """The same sums done the slow, obvious way with pandas, as a check."""
    s = pd.Series(closes, index=pd.DatetimeIndex(dates))
    end = s.index[-1]
    base = s[s.index <= end - pd.Timedelta(days=trend_days)].index[-1]
    win = s[s.index >= base]
    daily = win.pct_change().dropna()
    sigma = max(daily.std(ddof=1), floor / np.sqrt(periods))
    x = (s.iloc[-1] / win.iloc[0] - 1) / (sigma * np.sqrt(len(daily)))
    rbase = s[s.index <= end - pd.Timedelta(days=recent_days)].index[-1]
    n_recent = (s.index > rbase).sum()
    y = (s.iloc[-1] / s[rbase] - 1) / (sigma * np.sqrt(n_recent))
    return x, y


# --- presets ---------------------------------------------------------------

def test_parse_valid_and_lowercase():
    h = mom.parse_horizon("1y, 3m, 1m", "6M,1M,1W")
    assert (h.trend, h.recent, h.step) == ("1Y", "3M", "1M")
    assert (h.trend_days, h.recent_days, h.step_days) == (365, 91, 30)


@pytest.mark.parametrize("bad", ["1M,3M,1W", "2M,1M,1W", "6M,1M", "",
                                 None, "6M,YTD,1W", "6M,6M,1W"])
def test_parse_invalid_falls_back_to_default(bad):
    h = mom.parse_horizon(bad, "6M,1M,1W")
    assert (h.trend, h.recent, h.step) == ("6M", "1M", "1W")


def test_defaults_all_parse():
    for name, text in mom.HORIZON_DEFAULTS.items():
        assert mom._parse(text) is not None, name


def test_tactical_two_week_window():
    h = mom.parse_horizon(mom.HORIZON_DEFAULTS["Tactical"], "6M,1M,1W")
    assert (h.trend_days, h.recent_days, h.step_days) == (91, 14, 7)
    d = weekdays()
    c = zigzag(len(d))
    row = mom.scores(frame("A", d, c), h).iloc[0]
    x, y = by_hand(d, c, 91, 14)
    assert row["x"] == pytest.approx(x, rel=1e-9)
    assert row["y"] == pytest.approx(y, rel=1e-9)


# --- the sums ----------------------------------------------------------------

def test_scores_match_hand_calculation():
    d = weekdays()
    c = zigzag(len(d))
    out = mom.scores(frame("A", d, c), MEDIUM)
    x, y = by_hand(d, c, 182, 30)
    row = out.iloc[0]
    assert row["x"] == pytest.approx(x, rel=1e-9)
    assert row["y"] == pytest.approx(y, rel=1e-9)
    assert row["quadrant"] == "Rising"
    assert not row["short"]


def test_signs_and_quadrants():
    d = weekdays()
    n = len(d)
    # Falls steadily, then bounces over the last month: Recovering.
    rets = np.full(n, -0.002) + 0.01 * np.where(np.arange(n) % 2, 1, -1)
    rets[-22:] = 0.006 + 0.01 * np.where(np.arange(22) % 2, 1, -1)
    rets[0] = 0
    c = 100 * np.cumprod(1 + rets)
    row = mom.scores(frame("B", d, c), MEDIUM).iloc[0]
    assert row["x"] < 0 < row["y"]
    assert row["quadrant"] == "Recovering"


def test_vol_floor_stops_flat_series_exploding():
    # A money-market-like series: +0.01% every day, no noise at all.
    d = weekdays()
    c = 100 * np.cumprod(np.full(len(d), 1.0001))
    row = mom.scores(frame("MMF", d, c), MEDIUM).iloc[0]
    x, _ = by_hand(d, c, 182, 30)          # by_hand applies the same floor
    assert row["x"] == pytest.approx(x, rel=1e-9)
    assert row["vol"] == pytest.approx(mom.VOL_FLOOR * 100)
    assert row["x"] < 10                   # ~0.2% over 6M is not a "trend"


def test_benchmark_relative():
    d = weekdays()
    bench = pd.Series(zigzag(len(d), drift=0.0005), index=d)
    # Fund = benchmark x constant: zero relative move.
    same = mom.scores(frame("C", d, bench.values * 3), MEDIUM, bench=bench)
    assert same.iloc[0]["x"] == pytest.approx(0, abs=1e-9)
    assert same.iloc[0]["y"] == pytest.approx(0, abs=1e-9)
    # Fund = benchmark x its own path: relative score = score of that path.
    own = zigzag(len(d), drift=0.001, noise=0.005)
    rel = mom.scores(frame("D", d, bench.values * own), MEDIUM, bench=bench)
    alone = mom.scores(frame("D", d, own), MEDIUM)
    assert rel.iloc[0]["x"] == pytest.approx(alone.iloc[0]["x"], rel=1e-9)


def test_benchmark_on_a_different_calendar_is_forward_filled():
    # Fund trades every day, benchmark only on weekdays.
    d7 = pd.date_range(END - pd.Timedelta(days=400), END)
    bench = pd.Series(100.0, index=weekdays(400))
    out = mom.scores(frame("BTC", d7, zigzag(len(d7))), MEDIUM, bench=bench)
    assert len(out) == 1 and np.isfinite(out.iloc[0]["x"])


# --- history and calendars -----------------------------------------------------

def test_short_history_is_flagged_not_dropped():
    d = weekdays(60)
    row = mom.scores(frame("NEW", d, zigzag(len(d))), MEDIUM).iloc[0]
    assert row["short"]


def test_too_little_history_gives_no_row():
    d = weekdays(7)
    assert mom.scores(frame("TINY", d, zigzag(len(d))), MEDIUM).empty


def test_sparse_series_is_flagged():
    d = weekdays()[::2]                      # every other trading day
    row = mom.scores(frame("GAPPY", d, zigzag(len(d))), MEDIUM).iloc[0]
    assert row["short"]


def test_seven_day_calendar_counts_its_own_days():
    d7 = pd.date_range(END - pd.Timedelta(days=500), END)
    c = zigzag(len(d7))
    row = mom.scores(frame("BTC", d7, c), MEDIUM).iloc[0]
    x, _ = by_hand(d7, c, 182, 30, periods=365)
    assert row["n_trend"] == 182
    assert row["x"] == pytest.approx(x, rel=1e-9)
    assert not row["short"]


def test_each_fund_is_anchored_on_its_own_last_price():
    d = weekdays()
    lagging = d[:-2]                          # NAV two days behind
    px = pd.concat([frame("A", d, zigzag(len(d))),
                    frame("LAG", lagging, zigzag(len(lagging)))])
    out = mom.scores(px, MEDIUM).set_index("fund_id")
    assert out.loc["LAG", "as_of"] == lagging[-1]
    assert out.loc["A", "as_of"] == d[-1]


# --- trails --------------------------------------------------------------------

def test_trails_step_back_in_time():
    d = weekdays()
    t = mom.trails(frame("A", d, zigzag(len(d))), MEDIUM, points=6)
    assert sorted(t["step"]) == list(range(7))
    dates = t.sort_values("step")["as_of"].tolist()
    assert dates == sorted(dates, reverse=True)
    assert (dates[0] - dates[-1]).days == pytest.approx(42, abs=3)


def test_empty_input():
    assert mom.trails(pd.DataFrame(columns=["fund_id", "date", "close"]),
                      MEDIUM, points=3).empty
