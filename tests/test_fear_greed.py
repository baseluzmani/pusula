"""Tests for the Fear & Greed importer's parsing and core.sentiment - no
database and no network needed.

The payload below is cut down from CNN's real response on 20 Oct 2026, 16:58
UTC: the last few headline points, today's component scores, and the duplicate
momentum and volatility keys the importer is meant to ignore.

    python -m pytest tests/test_fear_greed.py -q
"""
import copy
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import sentiment                                    # noqa: E402
from importers import fear_greed as fg                        # noqa: E402

OCT16 = 1792108800000.0          # midnight UTC, 16 Oct 2026
OCT19 = 1792368000000.0
OCT20 = 1792454400000.0
LIVE = 1792515489000.0           # 20 Oct 2026 16:58:09 UTC

PAYLOAD = {
    "fear_and_greed": {
        "score": 27.2967032967033, "rating": "fear",
        "timestamp": "2026-10-20T16:58:09+00:00",
        "previous_close": 23.1071428571429,
    },
    "fear_and_greed_historical": {
        "timestamp": LIVE, "score": 27.2967032967033, "rating": "fear",
        "data": [
            {"x": OCT16, "y": 22.0732142857143, "rating": "extreme fear"},
            {"x": OCT19, "y": 20.8043956043956, "rating": "extreme fear"},
            {"x": OCT20, "y": 27.4351648351648, "rating": "fear"},
            {"x": LIVE,  "y": 27.2967032967033, "rating": "fear"},
        ],
    },
    "market_momentum_sp500":    {"timestamp": 1792514942000.0, "score": 35.8},
    "market_momentum_sp125":    {"timestamp": 1792514942000.0, "score": 35.8},
    "stock_price_strength":     {"timestamp": 1792514631000.0, "score": 13.4},
    "stock_price_breadth":      {"timestamp": 1792514628000.0, "score": 16},
    "put_call_options":         {"timestamp": 1792514643000.0, "score": 16},
    "market_volatility_vix":    {"timestamp": 1792514641000.0,
                                 "score": 43.8461538461538},
    "market_volatility_vix_50": {"timestamp": 1792514641000.0,
                                 "score": 43.8461538461538},
    "junk_bond_demand":         {"timestamp": 1792514642000.0, "score": 47.8},
    "safe_haven_demand":        {"timestamp": LIVE, "score": 19.2},
}


# --- dates -----------------------------------------------------------------

def test_midnight_utc_stays_on_its_trading_day():
    # Read as New York time this would be 18 Oct, the previous evening.
    assert fg._day(OCT19) == "2026-10-19"


def test_live_point_and_iso_headline_stamp():
    assert fg._day(LIVE) == "2026-10-20"
    assert fg._day("2026-10-20T16:58:09+00:00") == "2026-10-20"


# --- headline history ------------------------------------------------------

def test_history_one_row_per_day_live_point_wins():
    hist = fg.history(PAYLOAD)
    assert [d for d, _ in hist] == ["2026-10-16", "2026-10-19", "2026-10-20"]
    # 20 Oct appears twice (midnight 27.44, live 27.30): the later one wins.
    assert hist[-1][1] == pytest.approx(27.2967, abs=1e-4)


def test_history_order_does_not_depend_on_the_array():
    p = copy.deepcopy(PAYLOAD)
    p["fear_and_greed_historical"]["data"].reverse()
    assert fg.history(p) == fg.history(PAYLOAD)


def test_history_drops_off_scale_and_missing_points():
    p = copy.deepcopy(PAYLOAD)
    p["fear_and_greed_historical"]["data"] += [
        {"x": OCT16 - 86400000, "y": 140.0},
        {"x": OCT16 - 2 * 86400000, "y": None},
        {"y": 50.0},
    ]
    assert [d for d, _ in fg.history(p)] == ["2026-10-16", "2026-10-19",
                                             "2026-10-20"]


# --- components ------------------------------------------------------------

def test_components_one_each_duplicates_ignored():
    comps = fg.components(PAYLOAD)
    assert [sid for sid, _, _ in comps] == [c[0] for c in fg.COMPONENTS]
    assert {d for _, d, _ in comps} == {"2026-10-20"}
    assert dict((sid, v) for sid, _, v in comps)["CNNFG_VIX"] == \
        pytest.approx(43.846, abs=1e-3)


def test_check_passes_on_real_data():
    # Mean of the seven is 27.44 against a headline of 27.30: inside slack.
    assert fg.check(PAYLOAD, fg.components(PAYLOAD)) == ""


def test_check_flags_a_drifting_headline():
    p = copy.deepcopy(PAYLOAD)
    p["fear_and_greed"]["score"] = 35.0
    assert "component mean" in fg.check(p, fg.components(p))


def test_check_flags_a_missing_component():
    p = copy.deepcopy(PAYLOAD)
    del p["put_call_options"]
    assert "6 of 7" in fg.check(p, fg.components(p))


def test_score_rejects_junk():
    assert fg._score("abc") is None
    assert fg._score(-1) is None
    assert fg._score(100.5) is None
    assert fg._score("42.5") == 42.5


def test_unexpected_payload_fails_loudly():
    with pytest.raises(KeyError):
        fg.history({"fear_and_greed": {}})


# --- where a run starts ----------------------------------------------------

def test_first_run_asks_for_a_year():
    assert fg.start_for(None, today=date(2026, 10, 20)) == "2025-10-20"
    # pandas hands back NaN, not None, for MAX(date) on an empty series.
    assert fg.start_for(float("nan"), today=date(2026, 10, 20)) == "2025-10-20"


def test_later_runs_reach_back_over_the_revision_window():
    assert fg.start_for("2026-10-20") == "2026-10-10"
    assert fg.start_for("2026-10-20", full=True,
                        today=date(2026, 10, 20)) == "2025-10-20"


def test_catalogue_headline_charted_components_not():
    rows = {r[0]: r for r in fg.catalogue()}
    assert rows["CNNFG"][7] == 1
    assert all(rows[c[0]][7] == 0 for c in fg.COMPONENTS)
    assert {r[5] for r in rows.values()} == {"CNN"}


# --- zones -----------------------------------------------------------------

@pytest.mark.parametrize("score, word", [
    (0, "Extreme fear"), (24.99, "Extreme fear"), (25, "Fear"),
    (44.1, "Fear"), (45, "Neutral"), (54.9, "Neutral"), (55, "Greed"),
    (75, "Extreme greed"), (100, "Extreme greed"),
])
def test_zone_bands(score, word):
    assert sentiment.zone(score) == word


def day(n):
    return pd.Timestamp("2026-10-01") + pd.Timedelta(days=n)


def test_split_cuts_a_crossing_where_it_happens():
    s = pd.Series([30.0, 50.0], index=[day(0), day(1)])
    parts = sentiment.split_by_zone(s)
    assert set(parts) == {"Fear", "Neutral"}
    fx, fy = parts["Fear"]
    nx, ny = parts["Neutral"]
    # 45 is three quarters of the way from 30 to 50: 18:00 on day 0.
    cut = day(0) + pd.Timedelta(hours=18)
    assert fx == [day(0), cut] and fy == [30.0, 45.0]
    assert nx == [cut, day(1)] and ny == [45.0, 50.0]


def test_split_falling_through_two_boundaries():
    s = pd.Series([50.0, 20.0], index=[day(0), day(1)])
    parts = sentiment.split_by_zone(s)
    assert parts["Neutral"][1] == [50.0, 45.0]
    assert parts["Fear"][1] == [45.0, 25.0]
    assert parts["Extreme fear"][1] == [25.0, 20.0]
    # The pieces meet: each one starts where the last one ended.
    assert parts["Neutral"][0][-1] == parts["Fear"][0][0]
    assert parts["Fear"][0][-1] == parts["Extreme fear"][0][0]


def test_split_joins_a_run_and_breaks_a_return():
    s = pd.Series([30.0, 35.0, 40.0, 50.0, 40.0],
                  index=[day(i) for i in range(5)])
    xs, ys = sentiment.split_by_zone(s)["Fear"]
    # Three days in fear as one run, then a break, then the return.
    assert xs.count(None) == 1
    assert ys[:4] == [30.0, 35.0, 40.0, 45.0]
    assert ys[-2:] == [45.0, 40.0]


def test_split_single_point():
    s = pd.Series([27.3], index=[day(0)])
    assert sentiment.split_by_zone(s) == {"Fear": ([day(0)], [27.3])}
