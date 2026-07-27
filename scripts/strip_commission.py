#!/usr/bin/env python3
"""
Strip embedded commission out of historical transaction prices.

Background
----------
Historically the trade price was recorded as (total cost / quantity), so the
broker's flat commission was baked into the per-unit price. This migration
recovers the true trade price and records the commission in its own column:

    BUY   recorded = true + fee/qty   ->  true = recorded - fee/qty
    SELL  recorded = true - fee/qty   ->  true = recorded + fee/qty

Flat fees: GBP GBP4.00, USD USD5.50. Note that for pence-quoted instruments the
GBP4 fee is 400 pence, which is why the fee is scaled by price_unit.

The migration is P&L neutral by construction: cost was
    qty * (true + fee/qty) = qty*true + fee
and afterwards the engine computes qty*true + commission, the same figure.
Total P&L must therefore be unchanged - that is the verification.

Scope
-----
- Accounts: AB ISA, AB SIPP, AB Trading, ALB ISA, BB ISA, BB SIPP, BB Trading
- Types: BUY and SELL only
- Currencies: GBP and USD only (TRY and blank left at 0)
- Excluded: YF:GC=F (physical gold - bought without broker commission)

Usage
-----
    python3 strip_commission.py --db path/to/funds.db            # preview
    python3 strip_commission.py --db path/to/funds.db --apply     # write
"""

import argparse
import sqlite3
import sys

ACCOUNTS = ("AB ISA", "AB SIPP", "AB Trading", "ALB ISA",
            "BB ISA", "BB SIPP", "BB Trading")

# Funds deliberately left at commission 0:
#   YF:GC=F     physical gold - bought without broker commission
#   YF:MBGL-WI  spin-off distribution, received free (nominal ~0 prices, so a
#               fee strip would push the price negative)
#   YF:GRG.L    dummy rows used to check the transactions chart, not real trades
#   YF:DFNG.L   sold quantity exceeds bought (consolidated in from another
#               holding), which would break exact P&L neutrality
EXCLUDE_FUNDS = ("YF:GC=F", "YF:MBGL-WI", "YF:GRG.L", "YF:DFNG.L")
FEE_GBP = 4.0
FEE_USD = 5.5

_ACC = ",".join("?" for _ in ACCOUNTS)
_EXC = ",".join("?" for _ in EXCLUDE_FUNDS)

# Fee expressed in the same units as the stored price.
_FEE_IN_PRICE_UNITS = f"""
    CASE
      WHEN t.currency = 'USD' THEN {FEE_USD}
      WHEN t.currency = 'GBP' AND i.price_unit = 'pence' THEN {FEE_GBP * 100}
      WHEN t.currency = 'GBP' THEN {FEE_GBP}
      ELSE 0
    END
"""

# Fee in the transaction's own currency, for the commission column.
_FEE_IN_CURRENCY = f"""
    CASE WHEN t.currency = 'USD' THEN {FEE_USD}
         WHEN t.currency = 'GBP' THEN {FEE_GBP}
         ELSE 0 END
"""

_SCOPE = f"""
    t.account IN ({_ACC})
    AND t.fund_id NOT IN ({_EXC})
    AND t.type IN ('BUY', 'SELL')
    AND t.currency IN ('GBP', 'USD')
    AND t.quantity > 0
"""

_PARAMS = ACCOUNTS + EXCLUDE_FUNDS


def has_commission_column(conn) -> bool:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(transactions)")]
    return "commission" in cols


def preview(conn):
    print("Scope check")
    print("-" * 72)
    rows = conn.execute(f"""
        SELECT i.price_unit, t.currency, t.type, COUNT(*) AS n,
               ROUND(SUM({_FEE_IN_CURRENCY}), 2) AS total_fee
        FROM transactions t LEFT JOIN instruments i ON i.fund_id = t.fund_id
        WHERE {_SCOPE}
        GROUP BY i.price_unit, t.currency, t.type
        ORDER BY i.price_unit, t.currency, t.type
    """, _PARAMS).fetchall()
    total_rows = 0
    for unit, curr, ttype, n, fee in rows:
        print(f"  {str(unit):6s} {curr:4s} {ttype:4s}  {n:4d} rows   "
              f"fee total {curr} {fee:,.2f}")
        total_rows += n
    print(f"  {'':6s} {'':4s} {'':4s}  {total_rows:4d} rows in scope")

    # NULL currency makes "NOT (... currency IN (...))" evaluate to NULL rather
    # than true, so count by excluding the in-scope ids explicitly instead.
    skipped = conn.execute(f"""
        SELECT COUNT(*) FROM transactions t
        WHERE t.account IN ({_ACC})
          AND t.id NOT IN (SELECT t2.id FROM transactions t2
                           LEFT JOIN instruments i ON i.fund_id = t2.fund_id
                           WHERE {_SCOPE.replace('t.', 't2.')})
    """, ACCOUNTS + _PARAMS).fetchone()[0]
    print(f"\n  {skipped} rows in these accounts left untouched "
          f"(gold, blank currency, non-BUY/SELL)")

    print("\nSample: recorded price -> recovered true price")
    print("-" * 72)
    sample = conn.execute(f"""
        SELECT t.trade_date, t.fund_id, t.type, t.quantity, t.price,
               i.price_unit, t.currency,
               ROUND(t.price + (CASE WHEN t.type='SELL' THEN 1 ELSE -1 END)
                     * ({_FEE_IN_PRICE_UNITS}) / t.quantity, 6) AS new_price,
               {_FEE_IN_CURRENCY} AS commission
        FROM transactions t LEFT JOIN instruments i ON i.fund_id = t.fund_id
        WHERE {_SCOPE}
        ORDER BY t.trade_date DESC LIMIT 12
    """, _PARAMS).fetchall()
    for d, fid, ttype, qty, old, unit, curr, new, comm in sample:
        print(f"  {d} {fid[:16]:16s} {ttype:4s} qty={qty:>10.4g} "
              f"{old:>12.4f} -> {new:>12.4f}  {unit or '-':5s} "
              f"comm {curr} {comm}")

    print("\nBiggest per-unit adjustments (small quantities move the price most)")
    print("-" * 72)
    big = conn.execute(f"""
        SELECT t.trade_date, t.fund_id, t.type, t.quantity, t.price,
               ROUND(({_FEE_IN_PRICE_UNITS}) / t.quantity, 4) AS adj,
               ROUND(100.0 * ({_FEE_IN_PRICE_UNITS}) / t.quantity / t.price, 3) AS pct
        FROM transactions t LEFT JOIN instruments i ON i.fund_id = t.fund_id
        WHERE {_SCOPE}
        ORDER BY pct DESC LIMIT 8
    """, _PARAMS).fetchall()
    for d, fid, ttype, qty, price, adj, pct in big:
        flag = "  <-- check" if pct and pct > 5 else ""
        print(f"  {d} {fid[:16]:16s} {ttype:4s} qty={qty:>10.4g} "
              f"price={price:>11.4f} adj={adj:>9.4f} ({pct:>6.3f}%){flag}")

    _oversell_check(conn)
    _negative_price_check(conn)
    print("\nNothing written. Re-run with --apply to migrate.")


def _negative_price_check(conn) -> int:
    """
    Any row where stripping the fee would drive the price to zero or below.
    That means the fee is larger than the trade itself, so the row is not a
    normal commissioned trade (free receipts, nominal placeholder prices) and
    must be excluded rather than migrated.
    """
    rows = conn.execute(f"""
        SELECT t.fund_id, t.trade_date, t.type, t.quantity, t.price,
               ROUND(t.price - ({_FEE_IN_PRICE_UNITS}) / t.quantity, 6) AS new_price
        FROM transactions t LEFT JOIN instruments i ON i.fund_id = t.fund_id
        WHERE {_SCOPE} AND t.type = 'BUY'
          AND t.price - ({_FEE_IN_PRICE_UNITS}) / t.quantity <= 0
        ORDER BY new_price
    """, _PARAMS).fetchall()
    print("\nNon-positive price check")
    print("-" * 72)
    if not rows:
        print("  None - every stripped price stays positive.")
        return 0
    for fid, d, ttype, qty, old, new in rows:
        print(f"  {fid[:20]:20s} {d} {ttype} qty={qty:g} "
              f"{old:g} -> {new:g}   MUST EXCLUDE")
    return len(rows)


def _oversell_check(conn):
    """
    Flag funds where cumulative SELL quantity exceeds cumulative BUY at some
    point. The strip is exactly P&L neutral only when every sell is covered:
    position_pnl caps the realised quantity at what is held, but deducts the
    whole commission, so an oversell shifts realised P&L by a few pence per
    affected trade. Not an error - just worth knowing before you compare
    totals and wonder why they moved slightly.
    """
    print("\nOversell check (sells exceeding holdings break exact neutrality)")
    print("-" * 72)
    rows = conn.execute(f"""
        SELECT t.fund_id,
               SUM(CASE WHEN t.type='BUY'  THEN t.quantity ELSE 0 END) AS bought,
               SUM(CASE WHEN t.type='SELL' THEN t.quantity ELSE 0 END) AS sold
        FROM transactions t LEFT JOIN instruments i ON i.fund_id = t.fund_id
        WHERE {_SCOPE}
        GROUP BY t.fund_id
        HAVING sold > bought + 0.000001
    """, _PARAMS).fetchall()
    if not rows:
        print("  None - every sell is covered, so the strip is exactly "
              "P&L neutral.")
        return
    for fid, bought, sold in rows:
        print(f"  {fid[:24]:24s} bought {bought:>12.4g}  sold {sold:>12.4g}"
              f"  over by {sold - bought:.4g}")
    print("  These funds may show a few pence of realised-P&L movement.")


def apply(conn):
    if not has_commission_column(conn):
        print("Adding commission column ...")
        conn.execute("ALTER TABLE transactions ADD COLUMN commission REAL DEFAULT 0")
    else:
        print("commission column already present")

    already = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE commission IS NOT NULL "
        "AND commission != 0").fetchone()[0]
    if already:
        print(f"\nABORT: {already} transactions already carry a non-zero "
              f"commission.\nRunning again would strip the fee twice. "
              f"Investigate before proceeding.")
        return False

    bad = _negative_price_check(conn)
    if bad:
        print(f"\nABORT: {bad} rows would end up with a price of zero or less.\n"
              f"Add their fund_ids to EXCLUDE_FUNDS and re-run.")
        return False

    # Correlated subquery form: SQLite UPDATE cannot join directly.
    fee_units = _FEE_IN_PRICE_UNITS.replace("i.price_unit",
        "(SELECT price_unit FROM instruments WHERE fund_id = t.fund_id)")
    n = conn.execute(f"""
        UPDATE transactions AS t
        SET price = ROUND(price + (CASE WHEN type='SELL' THEN 1 ELSE -1 END)
                          * ({fee_units}) / quantity, 6),
            commission = ({_FEE_IN_CURRENCY})
        WHERE {_SCOPE}
    """, _PARAMS).rowcount
    conn.commit()
    print(f"\nMigrated {n} transactions.")
    print("Verify: total P&L must be UNCHANGED (the strip is neutral by "
          "construction). Re-run tests/parity.py and compare against 8050.")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        if args.apply:
            ok = apply(conn)
            sys.exit(0 if ok else 1)
        preview(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
