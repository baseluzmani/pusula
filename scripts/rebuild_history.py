#!/usr/bin/env python3
"""
Rebuild snapshot history from the transaction ledger.

The stored history was computed by whatever pricing logic was current on the
day each snapshot ran. That logic has changed twice since - composites moved
from a rebased index to a real GBP price, and the house split into an asset
and a liability - so the series is not comparable end to end. Rather than try
to reconcile to those figures, this recomputes them.

For each date:

    units  = ledger position as at that date (BUY - SELL, dividends excluded)
    price  = the close on or before that date
    value  = units * price, converted to GBP

Cash is not touched. Cash accounts hold a current balance with no transaction
history, so there is nothing to derive; snapshot_cash keeps its stored values
and is added to the recomputed holdings total.

Positions with units but no price on a date are reported rather than silently
counted as zero - an understated total that looks plausible is worse than a
visible gap.

    python3 rebuild_history.py                  # dry run over existing dates
    python3 rebuild_history.py --from 2024-06-15
    python3 rebuild_history.py --apply
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db, finance as fin, valuation as val          # noqa: E402
from core.repo import portfolio as repo                        # noqa: E402


def ledger_positions(conn):
    """{fund_id: [(date, cumulative_units), ...]} ascending."""
    rows = conn.execute("""
        SELECT fund_id, trade_date, type, quantity
        FROM transactions WHERE type != 'DIVIDEND'
        ORDER BY fund_id, trade_date, id
    """).fetchall()

    running = defaultdict(float)
    out = defaultdict(list)
    for fund_id, trade_date, ttype, qty in rows:
        if ttype == "BUY":
            running[fund_id] += float(qty)
        elif ttype == "SELL":
            running[fund_id] -= float(qty)
        out[fund_id].append((trade_date, running[fund_id]))
    return out


def units_at(series, on_date):
    """Cumulative units as at a date, or 0 before the first trade.

    Not floored at zero: LIABILITY:MORTGAGE is held at -1, and flooring would
    erase it - the same trap recalc_holding sets.
    """
    held = 0.0
    for trade_date, cum in series:
        if trade_date <= on_date:
            held = cum
        else:
            break
    return held


def price_frame(conn):
    """{fund_id: [(date, close)]} ascending, for as-at lookups."""
    rows = conn.execute("""
        SELECT fund_id, date, close FROM prices
        WHERE close IS NOT NULL ORDER BY fund_id, date
    """).fetchall()
    out = defaultdict(list)
    for fund_id, d, close in rows:
        out[fund_id].append((d, float(close)))
    return out


def price_at(series, on_date):
    """Close on or before a date. None if the series starts later."""
    found = None
    for d, close in series:
        if d <= on_date:
            found = close
        else:
            break
    return found


def fx_at(prices, on_date):
    """USD and TRY crosses as at a date, falling back to the module default."""
    rates = {}
    for code, fund_id in (("USD", "YF:GBPUSD=X"), ("TRY", "YF:GBPTRY=X")):
        p = price_at(prices.get(fund_id, []), on_date)
        rates[code] = p if p else fin.FX_FALLBACK.get(code, 1.0)
    return rates


def snapshot_dates(conn, since=None):
    rows = conn.execute("""
        SELECT id, snap_date FROM portfolio_snapshots ORDER BY snap_date
    """).fetchall()
    return [(i, d) for i, d in rows if not since or d >= since]


def month_ends(start, end):
    """Month-end dates between two dates, for extending history backwards."""
    out = []
    d = datetime.strptime(start, "%Y-%m-%d").date().replace(day=1)
    last = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= last:
        nxt = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
        eom = nxt - timedelta(days=1)
        if eom <= last:
            out.append(eom.isoformat())
        d = nxt
    return out


def main(args):
    with db.get_conn() as conn:
        instruments = repo.instruments()
        ledger = ledger_positions(conn)
        prices = price_frame(conn)

        existing = snapshot_dates(conn, args.since)
        by_date = {d: i for i, d in existing}

        dates = sorted(by_date)
        if args.since and (not dates or args.since < dates[0]):
            extra = [d for d in month_ends(args.since,
                                           dates[0] if dates else
                                           date.today().isoformat())
                     if d not in by_date]
            dates = sorted(set(dates) | set(extra))

        if not dates:
            sys.exit("No dates to rebuild.")

        print(f"{len(dates)} dates, {dates[0]} to {dates[-1]}\n")
        print(f"{'date':<12}{'pos':>5}{'holdings':>14}{'cash':>12}"
              f"{'total':>14}{'stored':>14}{'diff':>12}  unpriced")
        print("-" * 100)

        results = []
        for d in dates:
            rates = fx_at(prices, d)
            gbpusd = rates["USD"]

            total, unpriced = 0.0, []
            rows = []
            for fund_id, series in ledger.items():
                units = units_at(series, d)
                if abs(units) < 1e-9:
                    continue
                inst = instruments.get(fund_id, {})

                if fund_id.startswith("CASH:"):
                    continue          # cash is preserved, not recomputed

                raw = price_at(prices.get(fund_id, []), d)
                if raw is None:
                    unpriced.append(fund_id)
                    continue
                gbp = fin.to_gbp(raw, inst.get("price_unit", "pound"),
                                 inst.get("currency", "GBP"), gbpusd, rates)
                if gbp is None:
                    unpriced.append(fund_id)
                    continue
                value = gbp * units
                rows.append((fund_id, units, round(value, 2)))
                total += value

            snap_id = by_date.get(d)
            cash_total = 0.0
            if snap_id:
                r = conn.execute("""
                    SELECT COALESCE(SUM(value_gbp), 0) FROM snapshot_cash
                    WHERE snapshot_id = ?
                """, (snap_id,)).fetchone()
                cash_total = float(r[0]) if r else 0.0

            stored = conn.execute("""
                SELECT total_gbp FROM networth_history WHERE date = ?
            """, (d,)).fetchone()
            stored_v = float(stored[0]) if stored else None

            grand = total + cash_total
            diff = (grand - stored_v) if stored_v is not None else None
            # Position count matters more than the unpriced count: a date
            # before the ledger covers everything shows every price it needs
            # and is still short, because the holdings simply are not there.
            print(f"{d:<12}{len(rows):>5}{total:>14,.0f}{cash_total:>12,.0f}"
                  f"{grand:>14,.0f}"
                  f"{stored_v if stored_v is not None else 0:>14,.0f}"
                  f"{diff if diff is not None else 0:>12,.0f}"
                  f"  {len(unpriced)}"
                  + (f" {unpriced[:3]}" if unpriced else ""))
            results.append((d, snap_id, rows, grand, cash_total))

        counts = [len(r[2]) for r in results]
        if counts and max(counts) - min(counts) > 2:
            lo = [r[0] for r in results if len(r[2]) == min(counts)]
            print(f"\nPosition count ranges {min(counts)} to {max(counts)}. "
                  f"The thinnest dates ({', '.join(lo[:3])}) are missing "
                  f"holdings the ledger does not know about yet - run "
                  f"backfill_openings.py before trusting them.")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return

        for d, snap_id, rows, grand, _cash in results:
            if snap_id is None:
                conn.execute("""
                    INSERT INTO portfolio_snapshots (snap_date, gbpusd,
                                                     gbptry, created_at)
                    VALUES (?, ?, ?, datetime('now'))
                """, (d, fx_at(prices, d)["USD"], fx_at(prices, d)["TRY"]))
                snap_id = conn.execute(
                    "SELECT id FROM portfolio_snapshots WHERE snap_date = ?",
                    (d,)).fetchone()[0]

            conn.execute("DELETE FROM snapshot_holdings WHERE snapshot_id = ?",
                         (snap_id,))
            for fund_id, units, value in rows:
                conn.execute("""
                    INSERT INTO snapshot_holdings
                        (snapshot_id, fund_id, units, value_gbp)
                    VALUES (?, ?, ?, ?)
                """, (snap_id, fund_id, units, value))

            conn.execute("""
                INSERT INTO networth_history (date, total_gbp, source)
                VALUES (?, ?, 'snapshot')
                ON CONFLICT(date) DO UPDATE SET
                    total_gbp = excluded.total_gbp
            """, (d, round(grand, 2)))

        print(f"\nWritten. {len(results)} dates rebuilt.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Rebuild snapshot history")
    ap.add_argument("--from", dest="since",
                    help="extend back to this date (YYYY-MM-DD)")
    ap.add_argument("--apply", action="store_true")
    main(ap.parse_args())
