#!/usr/bin/env python3
"""
Close the gaps that stop the ledger describing 22 April onward.

Three of them, found by comparing each position's first snapshot against its
first transaction:

  Burcu's four pension funds have no transactions at all. They were held from
  22 April and cashed out on 6 May, and the proceeds are the cash jump that
  day. Without them the rebuilt history is short by about 260k for a fortnight
  and then gains it back as cash, which looks like a deposit rather than a
  transfer.

  The three composites open on 24 April but appear in snapshots from the 22nd,
  so those two days show no pension at all - a 430k hole.

  CASH:TOTAL has no transactions either, which is correct and left alone: cash
  is a current balance, not a position, and the rebuild preserves it from
  snapshot_cash.

Quantities come from the snapshot's own value divided by that day's price,
because the earliest snapshots recorded value with no units. That keeps the
opening position worth exactly what the snapshot said it was worth.

    python3 backfill_openings.py            # dry run
    python3 backfill_openings.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db                                            # noqa: E402

OPEN_DATE = "2026-04-22"
EXIT_DATE = "2026-05-06"

# Held from the start of the snapshot series, cashed out on 6 May.
BURCU_FUNDS = ["GB0031728438:GBP", "GB00B3VNFD68:GBP",
               "GB00B61JR401:GBP", "LU1033664027:GBP"]

# Opened on 24 April by the statement rebuild, but held from the 22nd.
COMPOSITES = ["COMPOSITE:HSBC_ASIA_PAC", "COMPOSITE:HSBC_EM",
              "COMPOSITE:HSBC_SHARIA"]

ACCOUNT_BURCU = "BB Pension"
ACCOUNT_PENSION = "AB Pension"


def snapshot_value(conn, fund_id, on_date):
    row = conn.execute("""
        SELECT h.value_gbp FROM snapshot_holdings h
        JOIN portfolio_snapshots s ON s.id = h.snapshot_id
        WHERE h.fund_id = ? AND s.snap_date = ?
    """, (fund_id, on_date)).fetchone()
    return float(row[0]) if row and row[0] else None


def price_at(conn, fund_id, on_date):
    row = conn.execute("""
        SELECT date, close FROM prices
        WHERE fund_id = ? AND date <= ? AND close > 0
        ORDER BY date DESC LIMIT 1
    """, (fund_id, on_date)).fetchone()
    return (row[0], float(row[1])) if row else (None, None)


def instrument(conn, fund_id):
    row = conn.execute("""
        SELECT price_unit, currency FROM instruments WHERE fund_id = ?
    """, (fund_id,)).fetchone()
    return (row[0] or "pound", row[1] or "GBP") if row else ("pound", "GBP")


def to_native(value_gbp, price, price_unit):
    """Units from a GBP value and a native price.

    Only the pence case needs handling here: these are all GBP instruments, so
    there is no FX to apply.
    """
    if price_unit == "pence":
        return value_gbp / (price / 100.0)
    return value_gbp / price


def plan(conn):
    out = []

    for fund_id in BURCU_FUNDS:
        value = snapshot_value(conn, fund_id, OPEN_DATE)
        pdate, price = price_at(conn, fund_id, OPEN_DATE)
        if value is None or price is None:
            print(f"  ! {fund_id}: no value or price at {OPEN_DATE} - skipped")
            continue
        punit, _curr = instrument(conn, fund_id)
        qty = to_native(value, price, punit)

        exit_pdate, exit_price = price_at(conn, fund_id, EXIT_DATE)
        out.append({"fund_id": fund_id, "date": OPEN_DATE, "type": "BUY",
                    "qty": qty, "price": price, "account": ACCOUNT_BURCU,
                    "value": value, "note": "opening position"})
        out.append({"fund_id": fund_id, "date": EXIT_DATE, "type": "SELL",
                    "qty": qty, "price": exit_price or price,
                    "account": ACCOUNT_BURCU,
                    "value": qty * ((exit_price or price)
                                    / (100.0 if punit == "pence" else 1.0)),
                    "note": "cashed out"})

    for fund_id in COMPOSITES:
        value = snapshot_value(conn, fund_id, OPEN_DATE)
        pdate, price = price_at(conn, fund_id, OPEN_DATE)
        if value is None or price is None:
            print(f"  ! {fund_id}: no value or price at {OPEN_DATE} - skipped")
            continue
        punit, _curr = instrument(conn, fund_id)
        qty = to_native(value, price, punit)
        out.append({"fund_id": fund_id, "date": OPEN_DATE, "type": "MOVE",
                    "qty": qty, "price": price, "account": ACCOUNT_PENSION,
                    "value": value,
                    "note": "opening moved from 2026-04-24"})
    return out


def main(args):
    with db.get_conn() as conn:
        rows = plan(conn)
        if not rows:
            sys.exit("Nothing to do.")

        print(f"{'date':<12}{'fund_id':<28}{'type':<6}{'quantity':>18}"
              f"{'price':>12}{'value':>12}  note")
        print("-" * 104)
        for r in rows:
            print(f"{r['date']:<12}{r['fund_id']:<28}{r['type']:<6}"
                  f"{r['qty']:>18,.4f}{r['price']:>12,.4f}"
                  f"{r['value']:>12,.0f}  {r['note']}")

        moves = [r for r in rows if r["type"] == "MOVE"]
        if moves:
            print(f"\n{len(moves)} composite opening BUYs will be re-dated "
                  f"from 2026-04-24 to {OPEN_DATE} and re-priced, so the "
                  f"opening value matches that day's snapshot.")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return

        for r in rows:
            if r["type"] == "MOVE":
                # The existing opening BUY is replaced rather than added to,
                # or the position would double.
                conn.execute("""
                    DELETE FROM transactions
                    WHERE fund_id = ? AND trade_date = '2026-04-24'
                      AND type = 'BUY'
                      AND id = (SELECT MIN(id) FROM transactions
                                 WHERE fund_id = ? AND trade_date = '2026-04-24'
                                   AND type = 'BUY')
                """, (r["fund_id"], r["fund_id"]))
                ttype = "BUY"
            else:
                ttype = r["type"]

            conn.execute("""
                INSERT INTO transactions
                    (fund_id, account, trade_date, type, quantity, price,
                     currency, fx_rate, commission)
                VALUES (?, ?, ?, ?, ?, ?, 'GBP', 1.0, 0)
            """, (r["fund_id"], r["account"], r["date"], ttype,
                  r["qty"], r["price"]))

        print(f"\nWritten. {len(rows)} transactions.")

        print("\nLedger against holdings:")
        for fund_id in BURCU_FUNDS + COMPOSITES:
            net = conn.execute("""
                SELECT COALESCE(SUM(CASE WHEN type='BUY' THEN quantity
                                         WHEN type='SELL' THEN -quantity
                                         ELSE 0 END), 0)
                FROM transactions WHERE fund_id = ? AND type != 'DIVIDEND'
            """, (fund_id,)).fetchone()[0]
            held = conn.execute("""
                SELECT units FROM portfolio_holdings WHERE fund_id = ?
            """, (fund_id,)).fetchone()
            held_v = float(held[0]) if held else 0.0
            flag = "" if abs(net - held_v) < max(1.0, abs(held_v) * 0.02) \
                else "  <-- check"
            print(f"  {fund_id:<30}{net:>18,.2f}{held_v:>18,.2f}{flag}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    main(ap.parse_args())
