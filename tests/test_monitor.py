"""Tests for core.monitor (pure sums) and core.repo.targets (on a scratch
database - never the real one).

    python -m pytest tests/test_monitor.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import monitor as mon

END = pd.Timestamp("2026-10-13")
RULES = mon.Rules()


def frame(fund_id, dates, closes):
    return pd.DataFrame({"fund_id": fund_id, "date": pd.DatetimeIndex(dates),
                         "close": np.asarray(closes, dtype=float)})


def weekdays(days=700):
    return pd.bdate_range(END - pd.Timedelta(days=days), END)


def wiggle(n, level=100.0, noise=0.005):
    """Flat series with an alternating +-noise daily wiggle."""
    rets = noise * np.where(np.arange(n) % 2 == 0, 1.0, -1.0)
    rets[0] = 0.0
    return level * np.cumprod(1 + rets)


def targets(**kw):
    row = {"fund_id": "A", "buy_target": None, "sell_target": None,
           "review_level": None, "note": None}
    row.update(kw)
    return pd.DataFrame([row])


# --- drawdown -------------------------------------------------------------------

def test_drawdown_from_the_52_week_high():
    d = weekdays()
    c = wiggle(len(d), noise=0.003)
    peak = len(d) - 40                        # high 40 trading days ago
    c[peak - 100:peak + 1] = np.linspace(100, 130, 101)   # steady rise...
    c[peak:] = np.linspace(130, 104, len(d) - peak)       # ...then a fall
    r = mon.metrics(frame("A", d, c)).iloc[0]
    assert r["high"] == pytest.approx(130.0)
    assert r["dd"] == pytest.approx((104 / 130 - 1) * 100)
    assert r["high_date"] == d[peak]
    assert r["days_since_high"] == (d[-1] - d[peak]).days
    assert r["status"] == "Review"            # -20% fast: far beyond 2.5 sigma
    assert "\u03c3" in r["reasons"]


def test_drawdown_sigma_by_hand():
    d = weekdays()
    c = wiggle(len(d))
    peak = len(d) - 61
    c[peak:] = 103 * np.linspace(1.0, 0.97, len(d) - peak)    # clear high
    r = mon.metrics(frame("A", d, c)).iloc[0]
    s = pd.Series(c, index=d)
    daily = s[s.index >= END - pd.Timedelta(days=365)]
    first = s.index.get_loc(daily.index[0])
    rets = (s.iloc[first - 1:].pct_change().dropna())
    sigma = max(rets.std(ddof=1), mon.VOL_FLOOR / np.sqrt(252))
    dd = c[-1] / c[peak] - 1
    assert r["dd_sigma"] == pytest.approx(dd / (sigma * np.sqrt(60)), rel=1e-9)


def test_old_high_outside_52_weeks_is_ignored():
    d = weekdays()
    c = wiggle(len(d))
    c[10] = 500.0                             # ~2 years ago
    r = mon.metrics(frame("A", d, c)).iloc[0]
    assert r["high"] < 110
    assert r["status"] == "OK"


def test_at_the_high_is_zero_drawdown():
    d = weekdays()
    c = np.linspace(80, 120, len(d))
    r = mon.metrics(frame("A", d, c)).iloc[0]
    assert r["dd"] == 0 and r["dd_sigma"] == 0 and r["days_since_high"] == 0


def test_percent_threshold_alone_triggers_review():
    # A slow 30% fall over the year: modest in sigma, but over 25%.
    d = weekdays()
    c = wiggle(len(d), noise=0.03)
    start = len(d) - 250
    c[start] = c[:start].max() * 1.05
    c[start + 1:] = c[start] * np.linspace(0.99, 0.70, len(d) - start - 1)
    r = mon.metrics(frame("A", d, c)).iloc[0]
    assert -r["dd"] >= 25
    assert r["status"] == "Review" and "%" in r["reasons"]


# --- 200-day average ----------------------------------------------------------------

def test_days_below_the_200_day_average():
    d = weekdays()
    c = wiggle(len(d), noise=0.001)
    c[-12:] = 95.0                            # last 12 closes below the average
    r = mon.metrics(frame("A", d, c)).iloc[0]
    assert r["days_below_ma"] == 12
    assert r["vs_ma"] < 0
    assert r["status"] in ("Watch", "Review")
    assert "200-day" in r["reasons"] or r["status"] == "Review"


def test_not_enough_history_for_the_average():
    d = weekdays(100)
    r = mon.metrics(frame("A", d, wiggle(len(d)))).iloc[0]
    assert r["ma"] is None and r["vs_ma"] is None and r["days_below_ma"] == 0
    assert r["short"]


# --- your levels --------------------------------------------------------------------

def test_targets_near_and_hit():
    d = weekdays()
    c = wiggle(len(d))
    price = c[-1]
    t = targets(buy_target=price * 0.97, sell_target=price * 1.10,
                review_level=price * 0.80, note="thesis")
    r = mon.metrics(frame("A", d, c), t).iloc[0]
    assert r["buy_state"] == "near"           # 3% below: within 5%
    assert r["sell_state"] is None            # 10% above: not near
    assert r["review_state"] is None
    assert r["dist_buy"] == pytest.approx(-3.0)
    assert r["note"] == "thesis"

    t = targets(buy_target=price * 1.01, sell_target=price * 0.99)
    r = mon.metrics(frame("A", d, c), t).iloc[0]
    assert r["buy_state"] == "hit" and r["sell_state"] == "hit"
    assert r["status"] == "OK"                # targets never set the status


def test_review_level_sets_status():
    d = weekdays()
    c = wiggle(len(d))
    price = c[-1]
    near = mon.metrics(frame("A", d, c), targets(review_level=price * 0.97))
    hit = mon.metrics(frame("A", d, c), targets(review_level=price * 1.02))
    assert near.iloc[0]["status"] == "Watch"
    assert hit.iloc[0]["status"] == "Review"
    assert "review level" in hit.iloc[0]["reasons"]


def test_rules_are_parameters():
    d = weekdays()
    c = wiggle(len(d))
    c[-12:] = 95.0
    strict = mon.metrics(frame("A", d, c), rules=mon.Rules(ma_watch_days=20,
                                                          watch_sigma=99,
                                                          review_sigma=99,
                                                          review_pct=99))
    assert strict.iloc[0]["status"] == "OK"


def test_seven_day_calendar_vol():
    d7 = pd.date_range(END - pd.Timedelta(days=700), END)
    r = mon.metrics(frame("BTC", d7, wiggle(len(d7), noise=0.02))).iloc[0]
    assert r["vol"] == pytest.approx(0.02 * np.sqrt(365) * 100, rel=0.02)


def test_empty_input():
    assert mon.metrics(pd.DataFrame(columns=["fund_id", "date", "close"])).empty


# --- targets table ---------------------------------------------------------------

@pytest.fixture
def scratch_db(tmp_path, monkeypatch):
    from core import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "scratch.db")
    from core.repo import targets
    return targets


def test_targets_save_update_clear(scratch_db):
    t = scratch_db
    assert t.get("YF:SHEL.L") is None
    assert t.save("YF:SHEL.L", buy_target="2300", review_level=2100,
                  note="  gas thesis  ") == "saved"
    row = t.get("YF:SHEL.L")
    assert row["buy_target"] == 2300 and row["review_level"] == 2100
    assert row["sell_target"] is None or pd.isna(row["sell_target"])
    assert row["note"] == "gas thesis"

    t.save("YF:SHEL.L", buy_target=None, sell_target=2900, review_level=2100,
           note="gas thesis")
    row = t.get("YF:SHEL.L")
    assert pd.isna(row["buy_target"]) and row["sell_target"] == 2900

    assert t.save("YF:SHEL.L", None, "", 0, "  ") == "cleared"
    assert t.get("YF:SHEL.L") is None


def test_targets_filter_by_ids(scratch_db):
    t = scratch_db
    t.save("A", buy_target=1)
    t.save("B", sell_target=2)
    assert set(t.all_targets(["A"])["fund_id"]) == {"A"}
    assert set(t.all_targets()["fund_id"]) == {"A", "B"}


# --- 50-day average and the 50/200 cross -------------------------------------------

def _cross_by_hand(d, c, fast=50, slow=200):
    s = pd.Series(c, index=d)
    diff = (s.rolling(fast).mean() - s.rolling(slow).mean()).dropna()
    sign = np.sign(diff)
    changes = sign[sign != sign.shift()].index[1:]
    return changes[-1] if len(changes) else None


def test_golden_cross_after_a_turn():
    d = weekdays()
    n = len(d)
    c = np.concatenate([np.linspace(120, 90, n - 70),     # long decline...
                        np.linspace(90, 130, 70)])         # ...then a rally
    r = mon.metrics(frame("A", d, c)).iloc[0]
    when = _cross_by_hand(d, c)
    assert r["cross"] == "Golden"
    assert r["cross_date"] == when
    assert r["cross_days"] == (d[-1] - when).days
    ma50 = pd.Series(c).rolling(50).mean().iloc[-1]
    assert r["ma_fast"] == pytest.approx(ma50)
    assert r["vs_ma_fast"] == pytest.approx((c[-1] / ma50 - 1) * 100)


def test_death_cross_state():
    d = weekdays()
    n = len(d)
    c = np.concatenate([np.linspace(90, 130, n - 70),
                        np.linspace(130, 95, 70)])
    r = mon.metrics(frame("A", d, c)).iloc[0]
    assert r["cross"] == "Death"
    assert r["cross_days"] == (d[-1] - _cross_by_hand(d, c)).days


def test_no_cross_within_the_window():
    d = weekdays()
    r = mon.metrics(frame("A", d, np.linspace(80, 140, len(d)))).iloc[0]
    assert r["cross"] == "Golden"
    assert r["cross_days"] is None or pd.isna(r["cross_days"])
    assert r["cross_span"] > 300


def test_cross_off_when_fast_is_not_shorter():
    d = weekdays()
    r = mon.metrics(frame("A", d, wiggle(len(d))),
                    rules=mon.Rules(ma_fast_days=200)).iloc[0]
    assert r["cross"] is None
    assert r["ma_fast"] is not None


def test_with_ma_adds_the_fast_average():
    d = weekdays(400)
    out = mon.with_ma(pd.Series(wiggle(len(d)), index=d), 200, 50)
    assert {"close", "ma", "ma_fast"} <= set(out.columns)
    assert out["ma_fast"].notna().sum() == len(d) - 49


def test_table_rows_turn_nan_back_into_none():
    # The stored rows go through a DataFrame before rendering; a column with
    # some numbers and some None comes back as NaN, which must not print.
    from pages.monitor import _records
    df = pd.DataFrame([{"buy_target": 10.0, "vs_ma": None, "note": None},
                       {"buy_target": None, "vs_ma": 2.5, "note": "x"}])
    rows = _records(df)
    assert rows[0]["vs_ma"] is None and rows[1]["buy_target"] is None
    assert rows[0]["buy_target"] == 10.0 and rows[1]["note"] == "x"


# --- trend phase and the next cross -------------------------------------------

def _row(c, rules=RULES, days=700):
    d = weekdays(days)
    return mon.metrics(frame("A", d, np.asarray(c)[-len(d):]), rules=rules).iloc[0]


def _base_then_rally(n):
    """Peak, slide, a long base, then a rally: the 50-day still below the
    200-day but catching up - the set-up just before a golden cross."""
    return np.concatenate([np.linspace(480, 545, n - 200),
                           np.linspace(520, 400, 60), np.full(110, 420.0),
                           np.linspace(400, 460, 30)])


def _flat_days_by_hand(c, fast=50, slow=200, limit=mon.CROSS_LIMIT):
    above = c[-fast:].mean() > c[-slow:].mean()
    path = np.asarray(c, dtype=float)
    for k in range(1, limit + 1):
        path = np.append(path, c[-1])
        if (path[-fast:].mean() > path[-slow:].mean()) != above:
            return k
    return None


def test_the_four_phases():
    n = len(weekdays())
    rise, fall = np.linspace(80, 140, n), np.linspace(140, 80, n)
    assert _row(rise)["phase"] == "Uptrend"
    assert _row(fall)["phase"] == "Downtrend"
    dip = np.concatenate([np.linspace(80, 140, n - 15), np.linspace(140, 125, 15)])
    r = _row(dip)
    assert (r["cross"], r["phase"]) == ("Golden", "Weakening")
    bounce = np.concatenate([np.linspace(140, 80, n - 15), np.linspace(80, 90, 15)])
    r = _row(bounce)
    assert (r["cross"], r["phase"]) == ("Death", "Improving")


def test_next_cross_with_the_price_flat_matches_a_day_by_day_walk():
    n = len(weekdays())
    c = _base_then_rally(n)
    r = _row(c)
    k = _flat_days_by_hand(c)
    assert (r["cross"], r["phase"], r["next_cross"]) == ("Death", "Improving",
                                                         "Golden")
    assert k is not None and r["next_days"] == k
    assert r["next_due"] == (k if k <= RULES.cross_horizon else None)
    assert r["ma_gap"] == pytest.approx(
        (c[-50:].mean() / c[-200:].mean() - 1) * 100)
    # The level where they meet sits between the two averages' paths.
    assert c[-50:].mean() < r["next_level"] < c[-1]
    # Weekday instrument: the date is k business days on.
    expected = pd.Timestamp(np.busday_offset(np.datetime64(END, "D"), k,
                                             roll="forward"))
    assert r["next_date"] == expected


def test_next_cross_beyond_the_horizon_is_not_due():
    c = _base_then_rally(len(weekdays()))
    r = _row(c, rules=mon.Rules(cross_horizon=3))
    assert r["next_days"] > 3 and r["next_due"] is None


def test_breakeven_puts_the_averages_level_on_day_k():
    c = _base_then_rally(len(weekdays()))
    for k in (1, 5, 10, 20, 60, 150):
        p = mon.cross_breakeven(c, k, 50, 200)
        path = np.append(c, np.full(k, p))
        assert path[-50:].mean() == pytest.approx(path[-200:].mean(), rel=1e-9)
    assert mon.cross_breakeven(c, 200, 50, 200) is None
    assert mon.cross_breakeven(c[:150], 10, 50, 200) is None


def test_breakeven_matches_the_row():
    c = _base_then_rally(len(weekdays()))
    r = _row(c)
    assert (r["be_near_k"], r["be_far_k"]) == (10, RULES.cross_horizon)
    assert r["be_near"] == pytest.approx(mon.cross_breakeven(c, 10, 50, 200))
    assert r["be_far"] == pytest.approx(mon.cross_breakeven(c, 20, 50, 200))


def test_trend_slope_of_an_exponential():
    c = 100 * np.exp(0.002 * np.arange(80))
    assert mon.trend_slope(c, 50) == pytest.approx(0.002)
    assert mon.trend_slope(c[:10], 50) is None


def test_trend_case_walks_the_price_on_its_slope():
    c = _base_then_rally(len(weekdays()))
    r = _row(c)
    g = mon.trend_slope(c, 50)
    path = c[-1] * np.exp(g * np.arange(1, mon.CROSS_LIMIT + 1))
    k, _ = mon.project_cross(c, path, 50, 200)
    assert r["trend_slope"] == pytest.approx(g * 100)
    assert r["trend_days"] == k
    assert k <= r["next_days"]                # a rising price gets there first


def test_no_phase_or_next_cross_without_both_averages():
    d = weekdays(200)                         # ~140 weekdays: no 200-day
    r = mon.metrics(frame("A", d, wiggle(len(d)))).iloc[0]
    assert r["phase"] is None and r["next_cross"] is None
    assert r["next_due"] is None


def test_next_cross_hover_and_phase_note():
    from pages.monitor import _next_title, _phase_note
    c = _base_then_rally(len(weekdays()))
    rec = _row(c).to_dict()
    rec["unit"] = "USD"
    text = _next_title(rec, 50, 200)
    assert "50-day" in text and "below the 200-day" in text
    assert f"golden cross in ~{rec['next_days']} trading days" in text
    assert "by day 10: price at or above" in text
    note = str(_phase_note("Improving", 50, 200))
    assert all(name in note for name, _ in mon.PHASE_TEXT)
    assert "{fast}" not in note and "50-day" in note
