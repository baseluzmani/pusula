#!/usr/bin/env python3
"""
When could a golden (or death) cross happen? Read-only.

A cross is a consequence of future prices, so there is no date without a
price path. This answers the useful version of the question: for each
horizon k (trading days from now), what price - held flat from tomorrow -
puts the 50-day average exactly on the 200-day on day k?

    k < 50          P = (50 * B - 200 * A) / (150 * k)
    50 <= k < 200   P = B / (200 - k)

where A is the sum of the latest (50 - k) closes and B the sum of the latest
(200 - k) closes - the ones still inside each average on day k. (Shown for
50/200; the script uses whatever lengths you pass.) Above that price the
fast average is on top by day k; below it, it is not.

It also says when the cross comes if the price simply stays where it is.
The sums live in core/monitor.py (cross_breakeven, project_cross), shared
with the Monitor tab's Next cross column.

Usage
-----
    cd ~/pusula
    .venv/bin/python scripts/cross_breakeven.py YF:SPGI
    .venv/bin/python scripts/cross_breakeven.py YF:SHEL.L --fast 50 --slow 200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.monitor import cross_breakeven as breakeven  # noqa: E402
from core.monitor import project_cross                  # noqa: E402
from core.repo import market as repo                  # noqa: E402

HORIZONS = [5, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 130]


def days_if_flat(closes: np.ndarray, fast: int, slow: int,
                 limit: int = 250) -> int | None:
    """Trading days until the averages cross if every future close equals
    today's. None if not within the limit."""
    k, _ = project_cross(closes, np.full(limit, closes[-1]), fast, slow)
    return k


def main() -> None:
    ap = argparse.ArgumentParser(description="Price needed for a 50/200 cross")
    ap.add_argument("fund_id")
    ap.add_argument("--fast", type=int, default=50)
    ap.add_argument("--slow", type=int, default=200)
    args = ap.parse_args()

    px = repo.prices([args.fund_id])
    if px.empty:
        sys.exit(f"No prices for {args.fund_id}")
    closes = px.sort_values("date")["close"].to_numpy(dtype=float)
    if len(closes) < args.slow:
        sys.exit(f"Need {args.slow} closes, have {len(closes)}")

    f, s = args.fast, args.slow
    ma_f, ma_s = closes[-f:].mean(), closes[-s:].mean()
    golden_now = ma_f > ma_s
    want = "death" if golden_now else "golden"
    name = px["fund_name"].iloc[-1] or args.fund_id
    print(f"{name}  ({px['date'].iloc[-1]:%d %b %Y})")
    print(f"  last {closes[-1]:,.2f}   {f}-day {ma_f:,.2f}   {s}-day {ma_s:,.2f}"
          f"   -> {f}-day {'above' if golden_now else 'below'}\n")

    k_flat = days_if_flat(closes, f, s)
    if k_flat:
        print(f"  If the price stays at {closes[-1]:,.2f}: {want} cross in about "
              f"{k_flat} trading days (~{k_flat / 5:.0f} weeks)\n")
    else:
        print(f"  If the price stays at {closes[-1]:,.2f}: no {want} cross within "
              f"a year\n")

    side = "above" if want == "golden" else "below"
    print(f"  For a {want} cross by day k, the price needs to hold {side}:")
    for k in HORIZONS:
        p = breakeven(closes, k, f, s)
        if p is None or p <= 0:
            continue
        move = (p / closes[-1] - 1) * 100
        print(f"    {k:>4} trading days (~{k / 5:>4.0f} wk)   {p:>10,.2f}"
              f"   ({move:+.1f}% from today)")


if __name__ == "__main__":
    main()
