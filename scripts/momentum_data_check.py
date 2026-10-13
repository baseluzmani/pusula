#!/usr/bin/env python3
"""
Momentum chart - data sufficiency check (read-only).

Answers one question before anything is built: does every open position have
enough clean daily price history to compute a 6-month trend, a volatility
estimate, and 6 weekly trail points?

What it checks, per open position (Cash and House excluded):

    history    first price date - need at least NEED_DAYS of it
    coverage   price rows in the window vs. expected trading days
               (calendar days for Crypto, weekdays for everything else)
    gaps       longest stretch between two consecutive prices
    staleness  days since the last price
    steps      single-day moves too large to be real - e.g. a pence/pound
               mix-up, or the splice point left by a composite component swap.
               One of these inflates the volatility and fakes a return, so
               they matter more than a bit of missing history.

Nothing is written. Uses core.db.query, which opens the DB read-only.

Usage
-----
    cd ~/pusula
    .venv/bin/python scripts/momentum_data_check.py
    .venv/bin/python scripts/momentum_data_check.py --all     # every instrument
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import db                                   # noqa: E402
from core.repo.market import EXCLUDED_TYPES           # noqa: E402

TREND_DAYS = 182          # 6M trend window (calendar days)
TRAIL_WEEKS = 6           # weekly trail points behind the current dot
NEED_DAYS = TREND_DAYS + 7 * TRAIL_WEEKS   # oldest price the chart needs

MIN_COVERAGE = 0.80       # below this the vol estimate is unreliable
MAX_GAP_DAYS = 7          # a longer hole than this gets flagged
STALE_DAYS = 5            # last price older than this gets flagged

# A daily move is a "step" if it is both huge relative to the asset's normal
# moves AND big in absolute terms. The absolute floor stops a money market
# fund's 0.1% wiggle from being flagged against its tiny normal range.
STEP_SIGMAS = 8
STEP_FLOOR = 0.05


def load(all_instruments: bool) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Universe metadata and its price history."""
    if all_instruments:
        uni = db.query("""
            SELECT fund_id, name, asset_type FROM instruments
            WHERE fund_id IN (SELECT DISTINCT fund_id FROM prices)
        """)
    else:
        uni = db.query("""
            SELECT h.fund_id, i.name, i.asset_type
            FROM portfolio_holdings h
            JOIN instruments i ON i.fund_id = h.fund_id
            WHERE h.units > 0
        """)
    uni = uni[~uni["asset_type"].isin(EXCLUDED_TYPES)]
    if uni.empty:
        return uni, pd.DataFrame()

    marks = ",".join("?" * len(uni))
    px = db.query(f"""
        SELECT fund_id, date, close FROM prices
        WHERE fund_id IN ({marks}) AND close IS NOT NULL AND close > 0
        ORDER BY fund_id, date
    """, tuple(uni["fund_id"]))
    px["date"] = pd.to_datetime(px["date"])
    return uni, px


def check_one(g: pd.DataFrame, asset_type: str, today: pd.Timestamp) -> dict:
    """All the checks for one instrument's price rows (sorted by date)."""
    crypto = asset_type == "Crypto"
    start = today - pd.Timedelta(days=NEED_DAYS)

    first, last = g["date"].iloc[0], g["date"].iloc[-1]
    win = g[g["date"] >= start]

    # Expected rows: every calendar day for crypto, weekdays otherwise.
    # (UK bank holidays cost a few percent of coverage - that's fine.)
    if crypto:
        expected = (today - start).days
    else:
        expected = len(pd.bdate_range(start, today))
    coverage = len(win) / expected if expected else 0.0

    rets = win["close"].pct_change().dropna()
    gaps = win["date"].diff().dt.days.dropna()

    # Ordinary volatility, annualised on the asset's own trading calendar.
    periods = 365 if crypto else 252
    vol_ann = rets.std() * np.sqrt(periods) if len(rets) > 1 else np.nan

    # Robust sigma (median absolute deviation) for step detection: a single
    # bad day inflates the ordinary std, which would hide the very step
    # we are looking for.
    worst_ret, worst_day, steps = np.nan, None, 0
    if len(rets) > 10:
        mad = (rets - rets.median()).abs().median() * 1.4826
        big = rets[(rets.abs() > STEP_SIGMAS * mad) & (rets.abs() > STEP_FLOOR)]
        steps = len(big)
        i = rets.abs().idxmax()
        worst_ret = rets[i]
        worst_day = win.loc[i, "date"].date()

    problems = []
    if first > start:
        problems.append(f"SHORT ({(today - first).days}d of {NEED_DAYS}d)")
    if coverage < MIN_COVERAGE:
        problems.append("SPARSE")
    if len(gaps) and gaps.max() > MAX_GAP_DAYS:
        problems.append("GAP")
    if (today - last).days > STALE_DAYS:
        problems.append("STALE")
    if steps:
        problems.append(f"STEP x{steps}")

    return {
        "first": first.date(),
        "last": last.date(),
        "rows": len(win),
        "cover": f"{coverage:.0%}",
        "vol_ann": f"{vol_ann:.0%}" if pd.notna(vol_ann) else "-",
        "max_gap": int(gaps.max()) if len(gaps) else 0,
        "worst_day": f"{worst_ret:+.1%} {worst_day}" if worst_day else "-",
        "status": ", ".join(problems) or "OK",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--all", action="store_true",
                    help="check every instrument with prices, not just holdings")
    args = ap.parse_args()

    uni, px = load(args.all)
    if uni.empty:
        print("No instruments to check.")
        return

    today = pd.Timestamp.today().normalize()
    rows = []
    for r in uni.itertuples():
        g = px[px["fund_id"] == r.fund_id]
        name = (r.name or r.fund_id)[:28]
        if g.empty:
            rows.append({"fund_id": r.fund_id, "name": name,
                         "type": r.asset_type, "status": "NO PRICES"})
            continue
        rows.append({"fund_id": r.fund_id, "name": name, "type": r.asset_type,
                     **check_one(g, r.asset_type, today)})

    out = pd.DataFrame(rows)
    # Problems first, then by type and name.
    out["_ok"] = out["status"].eq("OK")
    out = out.sort_values(["_ok", "type", "name"]).drop(columns="_ok")

    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.max_rows", 500)
    print(f"Window needed: {NEED_DAYS} days "
          f"({TREND_DAYS}d trend + {TRAIL_WEEKS} weekly trail points)\n")
    print(out.to_string(index=False))

    bad = (out["status"] != "OK").sum()
    print(f"\n{len(out)} checked, {len(out) - bad} OK, {bad} flagged.")


if __name__ == "__main__":
    main()
