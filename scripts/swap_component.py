#!/usr/bin/env python3
"""
Swap one component of a composite without moving its GBP value.

Background
----------
A composite's price is sum(weight x component GBP price) on every date all its
components share (importers/composites.py). Swapping a component changes that
price, and with units fixed the holding's value would jump by
units x (new price - old price).

So the composite is re-denominated instead, the way a share split works:

    k = old composite price / new composite price      on the latest shared date

    transactions   quantity x k, price / k    (BUY and SELL only)
    prices         composite rows older than the new series, / k

quantity x price is untouched on every transaction, so cost, realised P&L and
ROCE capital are the same figures before and after. Units x price on the swap
date is also the same - that is the verification.

A single adjusting transaction was the alternative and is worse here: if fewer
units are needed it has to be a SELL at price 0, which books a realised loss of
those units at average cost and an equal unrealised gain. The total is right
but the split is not.

What changes
------------
- The rebuild rewrites every date the new components share, so the composite's
  recent history follows the new fund rather than the old one.
- Rows before that window are never touched by the rebuild, so they are divided
  by k here to stay on the same scale as the restated units. Where the two meet
  there is a small step: the old and new fund's relative performance over the
  rewritten window.
- Stored snapshots are not touched and keep the values they were taken with.

Order of work
-------------
Run this BEFORE changing the weights on screen - it reads the current
definition as the "old" one.

    1. preview (no --apply)       nothing written
    2. --apply                    backs up funds.db, then restates the ledger
    3. Data -> Composites         swap the component, Save weights
    4. rebuild composites         Data -> Importers -> Run now, or
                                  python -m importers.composites
    5. run it again               compares the value with the backup

Do 2 to 4 back to back. Between them the units are restated but the price is
not, so the holding shows the wrong value until the rebuild runs.

Usage
-----
    cd ~/pusula
    .venv/bin/python scripts/swap_component.py \\
        --composite COMPOSITE:HSBC_EM \\
        --out GB00BL0DTP33:GBP --in GB00BNGGYW21:GBX --weight 0.5 [--apply]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # ~/pusula
sys.path.insert(0, str(ROOT))

import pandas as pd                                            # noqa: E402

from core import config, db                                    # noqa: E402
from core.repo import composites as comp_repo                  # noqa: E402
from core.repo.entry import recalc_holding                     # noqa: E402
from importers import composites as builder                    # noqa: E402

BACKUP_DIR = Path(config.DB_PATH).parent / "backups"


# --- Reading the current state ---------------------------------------------

def _definition(composite_id: str) -> dict:
    for d in comp_repo.definitions():
        if d["fund_id"] == composite_id:
            return d
    sys.exit(f"{composite_id} not found, or it has no components.")


def _series(defn: dict, prices: pd.DataFrame, meta: dict) -> pd.Series:
    """The composite series exactly as the importer would build it.

    Calls the importer's own _build, so the GBP conversion (pence, FX, common
    dates) cannot drift from what the rebuild will actually write.
    """
    built = builder._build(prices, [defn], meta)
    if built.empty:
        return pd.Series(dtype=float)
    # Indexed by "YYYY-MM-DD" strings, the same form prices.date is stored in.
    return built.set_index("date")["close"].sort_index()


def _units(conn, fund_id: str) -> float:
    row = conn.execute("""
        SELECT SUM(CASE WHEN type = 'BUY'  THEN quantity ELSE 0 END)
             - SUM(CASE WHEN type = 'SELL' THEN quantity ELSE 0 END)
        FROM transactions WHERE fund_id = ?
    """, (fund_id,)).fetchone()
    return float(row[0] or 0.0)


def _latest_stored(conn, fund_id: str):
    """(date, close) of the newest stored row - what every screen values at."""
    row = conn.execute("""
        SELECT date, close FROM prices WHERE fund_id = ?
        ORDER BY date DESC LIMIT 1
    """, (fund_id,)).fetchone()
    return (row[0], float(row[1])) if row else (None, None)


def _gbp_on(prices, meta, fund_id, on_date):
    """(raw close, GBP close) for one component on one date."""
    sub = prices[prices["fund_id"] == fund_id][["date", "close"]]
    sub = sub.set_index("date")["close"].sort_index()
    usd = builder._fx_series(prices, builder.GBPUSD)
    try_ = builder._fx_series(prices, builder.GBPTRY)
    gbp = builder._to_gbp_series(sub, meta.get(fund_id, {}), usd, try_)
    when = pd.Timestamp(on_date)
    raw = sub.get(when)
    return raw, (gbp.get(when) if gbp is not None else None)


def _backup_glob(composite_id: str, out_id: str) -> str:
    return f"pre_swap_{_slug(composite_id)}__{_slug(out_id)}_*.db"


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text)


# --- Preview ---------------------------------------------------------------

def preview(args) -> dict:
    defn = _definition(args.composite)
    ids = [c["fund_id"] for c in defn["components"]]

    if args.out not in ids:
        if args.inn in ids:
            verify(args)
            sys.exit(0)
        sys.exit(f"{args.out} is not a component of {args.composite}.")
    if args.inn in ids:
        sys.exit(f"{args.inn} is already a component - nothing to swap.")

    out_weight = next(c["weight"] for c in defn["components"]
                      if c["fund_id"] == args.out)
    new_weight = args.weight if args.weight is not None else out_weight
    new_defn = {**defn, "components": [
        {"fund_id": args.inn, "weight": new_weight} if c["fund_id"] == args.out
        else c for c in defn["components"]]}

    with db.get_conn(readonly=True) as conn:
        prices = builder._prices_frame(conn)
        meta = builder._instrument_meta(conn)
        units = _units(conn, args.composite)
        stored_date, stored_close = _latest_stored(conn, args.composite)
        inst = conn.execute("""
            SELECT source, COALESCE(active, 1) FROM instruments WHERE fund_id = ?
        """, (args.inn,)).fetchone()
        txns = conn.execute("""
            SELECT trade_date, account, type, quantity, price
            FROM transactions
            WHERE fund_id = ? AND type IN ('BUY', 'SELL')
            ORDER BY trade_date
        """, (args.composite,)).fetchall()

    print("Building both versions with the importer's own logic...")
    old = _series(defn, prices, meta)
    new = _series(new_defn, prices, meta)
    if old.empty or new.empty:
        sys.exit("Could not build one of the series - see messages above.")

    common = old.index.intersection(new.index)
    if common.empty:
        sys.exit("Old and new series share no dates.")
    on = on_str = common.max()
    k = old[on] / new[on]
    window_start = new.index.min()

    with db.get_conn(readonly=True) as conn:
        pre_window = conn.execute("""
            SELECT COUNT(*), MIN(date), MAX(date) FROM prices
            WHERE fund_id = ? AND date < ?
        """, (args.composite, window_start)).fetchone()

    print(f"\n{args.composite} on {on_str}")
    print("-" * 72)
    print(f"  {'component':24s} {'weight':>7s} {'raw close':>11s} "
          f"{'unit':>6s} {'GBP':>10s} {'share':>7s}")
    for c in new_defn["components"] + [{"fund_id": args.out,
                                        "weight": None}]:
        cid = c["fund_id"]
        raw, gbp = _gbp_on(prices, meta, cid, on)
        unit = meta.get(cid, {}).get("price_unit", "pound")
        share = (f"{c['weight'] * gbp / new[on]:6.1%}"
                 if c["weight"] is not None and gbp else "   out")
        weight = f"{c['weight']:.4g}" if c["weight"] is not None else "-"
        print(f"  {cid[:24]:24s} {weight:>7s} "
              f"{raw if raw is not None else float('nan'):>11.4f} "
              f"{unit[:6]:>6s} "
              f"{gbp if gbp is not None else float('nan'):>10.4f} {share:>7s}")
    print("  (share = each component's part of the new blend's value - weights")
    print("   multiply GBP prices, so they are not value percentages)")

    print(f"\n  old composite price   {old[on]:12.6f}")
    print(f"  new composite price   {new[on]:12.6f}")
    print(f"  k = old / new         {k:12.8f}")

    value = units * old[on]
    print(f"\n  units now             {units:16,.6f}")
    print(f"  units after           {units * k:16,.6f}")
    print(f"  value now             £{value:15,.2f}")
    print(f"  value if swapped without restating  £{units * new[on]:,.2f}")
    print(f"  value after restating               "
          f"£{units * k * new[on]:,.2f}")

    print(f"\nTransactions restated ({len(txns)}, BUY/SELL only)")
    print("-" * 72)
    for d, acct, ttype, qty, price in txns:
        print(f"  {d} {acct or '-':12s} {ttype:4s} "
              f"{qty:>16,.6f} @ {price:<10.6f} -> "
              f"{qty * k:>16,.6f} @ {price / k:.6f}")

    print(f"\nHistory")
    print("-" * 72)
    print(f"  rebuild will rewrite {args.composite} from {window_start} "
          f"onwards (new series start)")
    if pre_window[0]:
        print(f"  {pre_window[0]} older rows ({pre_window[1]} to "
              f"{pre_window[2]}) will be divided by k")
    else:
        print("  no older rows to rescale")

    # --- Checks that should stop an apply ---
    problems = []
    if stored_date != on_str or abs(stored_close - old[on]) > 1e-9 * old[on]:
        problems.append(
            f"stored {args.composite} latest is {stored_date} @ "
            f"{stored_close}, not {on_str} @ {old[on]:.6f}. Run the composites "
            f"rebuild first so the screen value and this script agree, then "
            f"preview again.")
    if not inst:
        problems.append(f"{args.inn} has no instruments row.")
    elif inst[0] not in ("ft", "yahoo") or not inst[1]:
        problems.append(
            f"{args.inn} has source '{inst[0]}' / active {inst[1]}. The daily "
            f"importers will not update it, and the composite only builds on "
            f"dates every component has - it would freeze at {on_str}.")

    print("\nChecks")
    print("-" * 72)
    if problems:
        for p in problems:
            print(f"  STOP: {p}")
    else:
        print("  OK - stored price matches the old blend; new component is "
              "kept up to date by its importer.")

    return {"k": k, "window_start": window_start, "ok": not problems,
            "value": value}


# --- Apply -----------------------------------------------------------------

def _backup(args) -> Path:
    """Consistent snapshot via SQLite's backup API (safe with WAL).

    Named pre_swap_* so the nightly job's funds_* pruning never removes it,
    and so a second --apply can see the first already happened.
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    target = BACKUP_DIR / _backup_glob(args.composite, args.out).replace(
        "*", datetime.now().strftime("%Y%m%d_%H%M%S"))
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def apply(args, plan: dict) -> None:
    done = sorted(BACKUP_DIR.glob(_backup_glob(args.composite, args.out)))
    if done:
        sys.exit(f"Already applied - backup {done[-1].name} exists. Applying "
                 f"twice would restate the units twice. Restore from that "
                 f"backup first if you really mean to redo it.")
    if not plan["ok"]:
        sys.exit("Not applied - fix the STOP items above first.")

    k, start = plan["k"], plan["window_start"]
    target = _backup(args)
    print(f"\nBackup: {target}")

    with db.get_conn() as conn:
        n_txn = conn.execute("""
            UPDATE transactions SET quantity = quantity * ?, price = price / ?
            WHERE fund_id = ? AND type IN ('BUY', 'SELL')
        """, (k, k, args.composite)).rowcount
        n_px = conn.execute("""
            UPDATE prices
            SET open = open / ?, high = high / ?, low = low / ?, close = close / ?
            WHERE fund_id = ? AND date < ?
        """, (k, k, k, k, args.composite, start)).rowcount

    units = recalc_holding(args.composite)
    print(f"Restated {n_txn} transactions and {n_px} older price rows; "
          f"holding now {units:,.6f} units.")
    print("\nNext, back to back:")
    print(f"  1. Data -> Composites: replace {args.out} with {args.inn}, "
          f"Save weights")
    print("  2. Rebuild: Data -> Importers -> Run now, or "
          ".venv/bin/python -m importers.composites")
    print("  3. Run this script again (same arguments, no --apply) to verify")


# --- Verify ----------------------------------------------------------------

def verify(args) -> None:
    """After the swap: compare today's value with the pre-swap backup."""
    done = sorted(BACKUP_DIR.glob(_backup_glob(args.composite, args.out)))
    if not done:
        sys.exit(f"{args.inn} is already in {args.composite} but there is no "
                 f"pre-swap backup to compare with.")

    backup = sqlite3.connect(f"file:{done[-1]}?mode=ro", uri=True)
    try:
        before_units = _units(backup, args.composite)
        before_date, before_close = _latest_stored(backup, args.composite)
    finally:
        backup.close()

    with db.get_conn(readonly=True) as conn:
        units = _units(conn, args.composite)
        now_date, now_close = _latest_stored(conn, args.composite)

    before = before_units * before_close
    after = units * now_close
    print(f"Verify against {done[-1].name}")
    print("-" * 72)
    print(f"  before  {before_units:16,.6f} units x {before_close:.6f} "
          f"({before_date}) = £{before:,.2f}")
    print(f"  now     {units:16,.6f} units x {now_close:.6f} "
          f"({now_date}) = £{after:,.2f}")
    print(f"  difference  £{after - before:,.2f}")
    if now_date == before_date and abs(now_close - before_close) < 1e-12:
        print("\n  The composite price has not changed yet - save the new "
              "weights and run the composites rebuild, then check again.")
    elif now_date != before_date:
        print(f"\n  Prices have moved on since {before_date}, so some "
              f"difference is market movement, not the swap.")


# --- Main ------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Swap a composite component without moving its value")
    p.add_argument("--composite", required=True)
    p.add_argument("--out", required=True, help="component being removed")
    p.add_argument("--in", dest="inn", required=True,
                   help="component replacing it")
    p.add_argument("--weight", type=float,
                   help="weight for the new component (default: same as out)")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()

    plan = preview(args)
    if args.apply:
        apply(args, plan)
    else:
        print("\nNothing written. Re-run with --apply to restate.")


if __name__ == "__main__":
    main()
