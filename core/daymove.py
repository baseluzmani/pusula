"""
Day-move map for the Heatmap tab: each holding a tile sized by its value in
GBP and coloured by its latest daily move.

Pure calculation - frames in, frames out. No Dash, no SQL.

Latest daily move
-----------------
The close-to-close change on the latest day the instrument actually moved.
For anything already priced today that is the same number as the Heatmap's
1D column. Funds priced once a day after the close (FT funds, the HSBC
pension composites) have not moved yet today - their latest row is
yesterday's, or today's repeating yesterday's close - so for those the tile
shows the last move they did make, and says which day it was ("stale").
A change older than STALE_DAYS is not used: the tile shows 0.

Day change in GBP
-----------------
The holding's value is at the latest price, i.e. after the move, so the
move's GBP effect is value * r / (1 + r). A group's (or the whole map's)
move is its GBP change over its value before the move - the value-weighted
move. Mixed dates when some tiles are stale: it is "the latest move of each
holding", not one calendar day.
"""

from __future__ import annotations

import re

import pandas as pd

STALE_DAYS = 10          # calendar days a move stays usable
SAME = 1e-9              # relative change below this counts as no move

# Holdings that are not market positions: no daily move to show.
SKIP_PREFIXES = ("CASH:", "ASSET:", "LIABILITY:")

_TICKER_IN_NAME = re.compile(r"\(([A-Z][A-Z0-9.\-]{0,7})\)\s*$")
_EXCHANGE_SUFFIX = re.compile(r"\.(L|IS|TO|AS|PA|DE|MI|SW|HK|AX)$")


def latest_moves(prices: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """
    One row per fund in ``prices`` (long frame: fund_id, date, close):
    move (percent), move_date (None if no move within STALE_DAYS),
    last_date, stale (True when the move is not from ``as_of``).

    ``as_of`` defaults to the latest date in the frame - the same "today"
    the Heatmap's 1D column measures from.
    """
    cols = ["fund_id", "move", "move_date", "last_date", "stale"]
    if prices is None or prices.empty:
        return pd.DataFrame(columns=cols)
    df = prices[["fund_id", "date", "close"]].dropna()
    df = df[df["close"] > 0]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df = (df.assign(date=pd.to_datetime(df["date"]))
            .sort_values(["fund_id", "date"])
            .drop_duplicates(["fund_id", "date"], keep="last"))
    as_of = pd.Timestamp(as_of) if as_of is not None else df["date"].max()
    oldest = as_of - pd.Timedelta(days=STALE_DAYS)

    rows = []
    for fid, g in df.groupby("fund_id", sort=False):
        dates = list(g["date"])
        closes = g["close"].to_numpy(dtype=float)
        move, when = 0.0, None
        for j in range(len(closes) - 1, 0, -1):
            if dates[j] < oldest:
                break
            change = closes[j] / closes[j - 1] - 1
            if abs(change) > SAME:
                move, when = change * 100, dates[j]
                break
        rows.append({"fund_id": fid, "move": float(move), "move_date": when,
                     "last_date": dates[-1],
                     "stale": when is None or when < as_of})
    return pd.DataFrame(rows, columns=cols)


def short_label(name, fund_id) -> str:
    """Tile label: the ticker in 'Cheniere Energy Inc (LNG)', a name that is
    already short, a Yahoo symbol without its exchange suffix ('YF:SEMI.L'
    -> 'SEMI'), otherwise the name cut to fit."""
    name = str(name or fund_id)
    m = _TICKER_IN_NAME.search(name)
    if m:
        return m.group(1)
    if len(name) <= 16:
        return name
    fid = str(fund_id)
    if fid.startswith("YF:") and not fid.startswith("YF:^"):
        return _EXCHANGE_SUFFIX.sub("", fid[3:])
    return name if len(name) <= 32 else name[:31].rstrip() + "\u2026"


def leaves(valued: pd.DataFrame, moves: pd.DataFrame) -> pd.DataFrame:
    """
    The tiles. ``valued`` has fund_id, name, asset_type, value (GBP, from
    core.valuation.value_holdings); ``moves`` is latest_moves(). Holdings
    with no positive value, and cash / house / mortgage rows, are left out.

    Columns: fund_id, label, name, asset_type, value, weight (percent of the
    tiles' total), move, change (GBP), move_date, stale. Largest first.
    """
    cols = ["fund_id", "label", "name", "asset_type", "value", "weight",
            "move", "change", "move_date", "stale"]
    if valued is None or valued.empty:
        return pd.DataFrame(columns=cols)
    v = valued[valued["value"].notna() & (valued["value"] > 0)
               & ~valued["fund_id"].str.startswith(SKIP_PREFIXES)].copy()
    if v.empty:
        return pd.DataFrame(columns=cols)
    m = moves.set_index("fund_id") if moves is not None and not moves.empty \
        else pd.DataFrame(columns=["move", "move_date", "stale"])
    v["move"] = [float(m.at[f, "move"]) if f in m.index else 0.0
                 for f in v["fund_id"]]
    v["move_date"] = [m.at[f, "move_date"] if f in m.index else None
                      for f in v["fund_id"]]
    v["stale"] = [bool(m.at[f, "stale"]) if f in m.index else True
                  for f in v["fund_id"]]
    r = v["move"] / 100
    v["change"] = v["value"] * r / (1 + r)
    v["weight"] = v["value"] / v["value"].sum() * 100
    v["label"] = [short_label(n, f) for n, f in zip(v["name"], v["fund_id"])]
    v["asset_type"] = v["asset_type"].fillna("Other")
    return v.sort_values("value", ascending=False)[cols].reset_index(drop=True)


def summary(tiles: pd.DataFrame) -> dict:
    """value, change (GBP) and move (percent, value-weighted) of a set of
    tiles - the whole map or one asset type."""
    if tiles is None or tiles.empty:
        return {"value": 0.0, "change": 0.0, "move": 0.0}
    value = float(tiles["value"].sum())
    change = float(tiles["change"].sum())
    before = value - change
    return {"value": value, "change": change,
            "move": change / before * 100 if before else 0.0}


def groups(tiles: pd.DataFrame) -> pd.DataFrame:
    """One row per asset type: asset_type, value, change, move. Largest
    first."""
    if tiles is None or tiles.empty:
        return pd.DataFrame(columns=["asset_type", "value", "change", "move"])
    out = [{"asset_type": t, **summary(g)}
           for t, g in tiles.groupby("asset_type", sort=False)]
    return (pd.DataFrame(out).sort_values("value", ascending=False)
            .reset_index(drop=True))
