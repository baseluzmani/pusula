"""
End-of-day portfolio snapshot.

Freezes what everything was worth today into portfolio_snapshots and its two
detail tables, and records the total in networth_history.

Ported from FTScrapper/scripts/snapshot.py with three changes:

  - valuation goes through core.valuation, which the Portfolio tabs already
    use, rather than this script keeping its own copy of to_gbp and the
    composite pricing. One definition of what a holding is worth.
  - the CALC:XAUGBP branch is gone. Physical gold moved to YF:GC=F and is now
    priced like any other USD instrument.
  - snapshot_categories is no longer written. Category totals are derived from
    snapshot_holdings joined to instruments, so they follow a rename instead
    of freezing the old label.

Usage
-----
    python3 -m importers.snapshot
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from core import db, finance as fin, valuation as val
from core.repo import portfolio as repo


def _first_working_day(today: date) -> date:
    """First weekday of the month - the date a permanent net-worth entry is
    kept, so month-ends survive while daily figures are transient."""
    first = date(today.year, today.month, 1)
    offset = 0
    while (first + timedelta(days=offset)).weekday() >= 5:
        offset += 1
    return first + timedelta(days=offset)


def _write_networth(conn, today: date, total: float):
    """
    One permanent row per month, plus a rolling row for today.

    Month starts are kept forever; intra-month figures are replaced as they go,
    so the table stays a monthly series rather than accumulating a row a day.
    """
    stamp = today.strftime("%Y-%m-%d")
    if today == _first_working_day(today):
        conn.execute("""
            INSERT OR REPLACE INTO networth_history (date, total_gbp, source)
            VALUES (?, ?, 'permanent')
        """, (stamp, round(total, 2)))
        print(f"Month start saved permanently: GBP {total:,.0f} ({stamp})")
        return

    conn.execute("DELETE FROM networth_history "
                 "WHERE source = 'snapshot' AND date != ?", (stamp,))
    conn.execute("""
        INSERT OR REPLACE INTO networth_history (date, total_gbp, source)
        VALUES (?, ?, 'snapshot')
    """, (stamp, round(total, 2)))
    print(f"Today's networth saved: GBP {total:,.0f} ({stamp})")


def run() -> dict:
    today = date.today()
    snap_date = today.strftime("%Y-%m-%d")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    instruments = repo.instruments()
    prices = repo.prices()
    rates = fin.fx_rates(prices)
    price_map = fin.latest_price_map(prices)
    composites = val.composite_definitions()

    holdings = repo.holdings()
    cash = repo.cash_accounts()

    with db.get_conn() as conn:
        # Update in place rather than INSERT OR REPLACE. REPLACE deletes the
        # row and inserts a new one, which takes a new AUTOINCREMENT id - so a
        # rerun would leave the previous run's detail rows pointing at an id
        # that no longer exists, and they would never be cleared.
        existing = conn.execute(
            "SELECT id FROM portfolio_snapshots WHERE snap_date = ?",
            (snap_date,)).fetchone()
        if existing:
            snap_id = existing[0]
            conn.execute("""
                UPDATE portfolio_snapshots
                SET gbpusd = ?, gbptry = ?, created_at = ? WHERE id = ?
            """, (rates["USD"], rates["TRY"], now, snap_id))
        else:
            conn.execute("""
                INSERT INTO portfolio_snapshots
                    (snap_date, gbpusd, gbptry, created_at)
                VALUES (?, ?, ?, ?)
            """, (snap_date, rates["USD"], rates["TRY"], now))
            snap_id = conn.execute(
                "SELECT id FROM portfolio_snapshots WHERE snap_date = ?",
                (snap_date,)).fetchone()[0]

        # Rerunning on the same day replaces that day rather than duplicating.
        conn.execute("DELETE FROM snapshot_holdings WHERE snapshot_id = ?",
                     (snap_id,))
        conn.execute("DELETE FROM snapshot_cash WHERE snapshot_id = ?",
                     (snap_id,))

        total = 0.0
        captured = 0
        for h in holdings.to_dict("records"):
            fund_id = h["fund_id"]
            units = float(h.get("units") or 0)
            price = val.holding_price_gbp(fund_id, instruments, price_map,
                                          rates["USD"], rates, composites)
            value = val.clean(price * units) if price else None
            if value is None:
                print(f"  unpriced: {fund_id}")
                continue
            conn.execute("""
                INSERT INTO snapshot_holdings
                    (snapshot_id, fund_id, units, value_gbp)
                VALUES (?, ?, ?, ?)
            """, (snap_id, fund_id, units, round(value, 2)))
            total += value
            captured += 1

        cash_total = 0.0
        for acc in cash.to_dict("records"):
            amount = float(acc.get("amount") or 0)
            currency = acc.get("currency") or "GBP"
            value_gbp = val.cash_to_gbp(amount, currency, rates)
            cash_total += value_gbp
            conn.execute("""
                INSERT INTO snapshot_cash
                    (snapshot_id, name, currency, amount, value_gbp)
                VALUES (?, ?, ?, ?, ?)
            """, (snap_id, acc.get("name"), currency, amount,
                  round(value_gbp, 2)))

        # Cash also goes in as a single rollup row so snapshot_holdings alone
        # sums to the portfolio total - which is what the comparison views read.
        if cash_total:
            conn.execute("""
                INSERT INTO snapshot_holdings
                    (snapshot_id, fund_id, units, value_gbp)
                VALUES (?, 'CASH:TOTAL', NULL, ?)
            """, (snap_id, round(cash_total, 2)))
            total += cash_total

        conn.execute("UPDATE portfolio_snapshots SET total_gbp = ? WHERE id = ?",
                     (round(total, 2), snap_id))
        _write_networth(conn, today, total)
        conn.commit()

    print(f"\nSnapshot saved: {snap_date} (id={snap_id})")
    print(f"Total portfolio value: GBP {total:,.2f}")
    print(f"Holdings captured: {captured} | Cash lines: {len(cash)} "
          f"| Cash total: GBP {cash_total:,.2f}")
    return {"total": round(total, 2), "holdings": captured,
            "message": f"GBP {total:,.0f} across {captured} holdings"}


if __name__ == "__main__":
    run()
