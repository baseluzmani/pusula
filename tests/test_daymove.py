"""Tests for core.daymove (the Heatmap tab's day-move map) - synthetic data
only.

    python -m pytest tests/test_daymove.py -q
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import daymove as dm, theme

TODAY = pd.Timestamp("2026-10-30")          # a Friday


def px(fund_id, closes, end=TODAY):
    dates = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({"fund_id": fund_id, "date": dates, "close": closes})


def one(frame, fund_id, as_of=None):
    m = dm.latest_moves(frame, as_of)
    return m.set_index("fund_id").loc[fund_id]


# --- latest move --------------------------------------------------------------

def test_priced_today_is_the_close_to_close_move():
    r = one(px("A", [100, 101, 99.0]), "A")
    assert r["move"] == pytest.approx((99 / 101 - 1) * 100)
    assert r["move_date"] == TODAY and not r["stale"]


def test_not_priced_today_keeps_its_last_move_and_is_stale():
    frame = pd.concat([px("A", [100, 101, 99.0]),
                       px("F", [10, 10.5, 10.2], end=TODAY - pd.Timedelta(days=1))])
    r = one(frame, "F")
    assert r["move"] == pytest.approx((10.2 / 10.5 - 1) * 100)
    assert r["move_date"] == TODAY - pd.Timedelta(days=1)
    assert r["stale"]


def test_a_repeated_close_today_is_skipped():
    # A composite rebuilt today from components that have not updated.
    r = one(px("C", [5.0, 5.2, 5.1, 5.1]), "C")
    assert r["move"] == pytest.approx((5.1 / 5.2 - 1) * 100)
    assert r["move_date"] == TODAY - pd.Timedelta(days=1) and r["stale"]


def test_float_noise_is_not_a_move():
    r = one(px("C", [5.0, 5.2, 5.2 * (1 + 1e-12)]), "C")
    assert r["move_date"] == TODAY - pd.Timedelta(days=1)


def test_nothing_within_the_window_shows_zero():
    closes = [1.0, 1.1] + [1.1] * 12             # last move ~2.5 weeks ago
    r = one(px("Z", closes), "Z")
    assert r["move"] == 0.0 and r["move_date"] is None and r["stale"]


def test_empty_frame():
    assert dm.latest_moves(pd.DataFrame()).empty


# --- labels -------------------------------------------------------------------

@pytest.mark.parametrize("name, fid, label", [
    ("Cheniere Energy Inc (LNG)", "YF:LNG", "LNG"),
    ("S&P Global Inc. (SPGI)", "YF:SPGI", "SPGI"),
    ("Shell plc", "YF:SHEL.L", "Shell plc"),
    ("iShares MSCI Global Semiconductors UCITS ETF", "YF:SEMI.L", "SEMI"),
    ("Türkiye Petrol Rafinerileri A.S.", "YF:TUPRS.IS", "TUPRS"),
    ("HSBC Pension Emerging Markets", "COMPOSITE:HSBC_EM",
     "HSBC Pension Emerging Markets"),
    ("Royal London Short Term Money Market Y Acc", "GB00B8XYYQ86:GBP",
     "Royal London Short Term Money M…"),
    ("Fidelity Asia Fund W Acc", "GB00B4WT1Y33:GBP", "Fidelity Asia Fund W Acc"),
    (None, "YF:BTC-GBP", "YF:BTC-GBP"),
])
def test_short_label(name, fid, label):
    assert dm.short_label(name, fid) == label


# --- tiles --------------------------------------------------------------------

def _valued():
    return pd.DataFrame([
        {"fund_id": "YF:LNG", "name": "Cheniere Energy Inc (LNG)",
         "asset_type": "Commodity", "value": 40_000.0},
        {"fund_id": "YF:SHEL.L", "name": "Shell plc",
         "asset_type": "Commodity", "value": 60_000.0},
        {"fund_id": "YF:MSFT", "name": "Microsoft Corporation (MSFT)",
         "asset_type": "Tech/AI", "value": 10_000.0},
        {"fund_id": "ASSET:HOUSE", "name": "House", "asset_type": "House",
         "value": 500_000.0},
        {"fund_id": "LIABILITY:MORTGAGE", "name": "Mortgage",
         "asset_type": "House", "value": -200_000.0},
        {"fund_id": "YF:X", "name": "Unpriced", "asset_type": "Tech/AI",
         "value": None},
    ])


def _moves():
    return pd.DataFrame([
        {"fund_id": "YF:LNG", "move": 3.0, "move_date": TODAY, "stale": False},
        {"fund_id": "YF:SHEL.L", "move": -1.0, "move_date": TODAY,
         "stale": False},
        {"fund_id": "YF:MSFT", "move": 0.5,
         "move_date": TODAY - pd.Timedelta(days=1), "stale": True},
    ])


def test_leaves_keep_market_positions_only_largest_first():
    t = dm.leaves(_valued(), _moves())
    assert list(t["fund_id"]) == ["YF:SHEL.L", "YF:LNG", "YF:MSFT"]
    assert t["weight"].sum() == pytest.approx(100)
    assert list(t["label"]) == ["Shell plc", "LNG", "MSFT"]


def test_day_change_is_the_moves_effect_on_the_value_before_it():
    t = dm.leaves(_valued(), _moves()).set_index("fund_id")
    assert t.at["YF:LNG", "change"] == pytest.approx(40_000 * 0.03 / 1.03)
    assert t.at["YF:SHEL.L", "change"] == pytest.approx(60_000 * -0.01 / 0.99)
    assert bool(t.at["YF:MSFT", "stale"])


def test_a_holding_without_a_move_shows_zero():
    t = dm.leaves(_valued(), None).set_index("fund_id")
    assert (t["move"] == 0).all() and (t["change"] == 0).all()


def test_summary_and_groups_are_value_weighted():
    t = dm.leaves(_valued(), _moves())
    s = dm.summary(t)
    before = (t["value"] - t["change"]).sum()
    assert s["move"] == pytest.approx(t["change"].sum() / before * 100)
    g = dm.groups(t).set_index("asset_type")
    assert list(g.index) == ["Commodity", "Tech/AI"]
    comm = t[t["asset_type"] == "Commodity"]
    assert g.at["Commodity", "move"] == pytest.approx(
        comm["change"].sum() / (comm["value"] - comm["change"]).sum() * 100)
    assert dm.summary(pd.DataFrame())["move"] == 0.0


# --- colours and figure -------------------------------------------------------

def test_tile_colours_run_through_the_neutral_midpoint():
    assert theme.tile_rgb(0) == theme.TILE_MID
    assert theme.tile_rgb(3).upper() == theme.POSITIVE.upper()
    assert theme.tile_rgb(-9).upper() == theme.NEGATIVE.upper()
    assert theme.tile_rgb(None) == theme.TILE_MID


def test_figure_flat_and_grouped():
    from pages.heatmap import _tree_figure
    t = dm.leaves(_valued(), _moves())
    flat = _tree_figure(t, grouped=False).data[0]
    assert len(flat.ids) == 3 and set(flat.parents) == {""}
    nested = _tree_figure(t, grouped=True).data[0]
    assert len(nested.ids) == 5                      # 2 types + 3 holdings
    assert "type:Commodity" in nested.parents
    msft = list(nested.ids).index("YF:MSFT")
    assert "29 Oct" in nested.text[msft]             # stale: dated on the tile
    lng = list(nested.ids).index("YF:LNG")
    assert "<i>" not in nested.text[lng]
