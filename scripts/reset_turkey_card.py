#!/usr/bin/env python3
"""
One-off: empty the Turkey Card so its history can be re-imported from the
HSBC Türkiye PDF statements, which Import now reads in lira.

    python scripts/reset_turkey_card.py            # dry run - reports, writes nothing
    python scripts/reset_turkey_card.py --apply    # backs up funds.db, then resets

What it does, in one transaction:
- deletes every Turkey Card transaction and import record (the card stays);
- makes Turkey Card a TRY card and puts it back in use, so Import offers it
  for lira statements and converts them to pounds;
- adds three starter rules for lines the statements carry - Trendyol Go and
  Uber Eats orders (before the general UBER taxi rule) and the card payment
  from the bank account - unless a rule with that keyword already exists.

Other cards, rules and categories are not touched. Run it once, before
uploading the statements.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import config                          # noqa: E402
from core.repo import spending as repo           # noqa: E402

CARD = "Turkey Card"
CURRENCY = "TRY"
RATE_ID = f"YF:GBP{CURRENCY}=X"

# keyword, category, sub-category, where: "before UBER" or "end"
STARTER_RULES = [
    ("UBERTRENDYOLGO", "Food & Drink", "Restaurant", "before UBER"),
    ("UBER EATS/", "Food & Drink", "Restaurant", "before UBER"),
    ("HESAPTAN ÖDEME", "Finance", "Transfer", "end"),
]


def backup(target: Path) -> Path:
    dest_dir = target.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"funds_pre_turkey_reset_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(target)
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    out.close()
    src.close()
    return dest


def ensure_schema(conn: sqlite3.Connection) -> None:
    """The spend_* tables with the currency columns, inside the caller's
    transaction - so a dry run leaves funds.db as it was."""
    for stmt in repo.SCHEMA.split(";"):
        if stmt.strip():
            conn.execute(stmt)
    for table, cols in repo.LATER_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def report(conn: sqlite3.Connection) -> dict:
    rows, lo, hi, total = conn.execute(
        "SELECT COUNT(*), MIN(date), MAX(date), ROUND(SUM(amount), 2) "
        "FROM spend_transactions WHERE card = ?", (CARD,)).fetchone()
    imports = conn.execute("SELECT COUNT(*) FROM spend_imports WHERE card = ?",
                           (CARD,)).fetchone()[0]
    return {"rows": rows, "from": lo, "to": hi, "total": total or 0.0,
            "imports": imports}


def rule_priority(conn: sqlite3.Connection, where: str) -> int:
    if where == "before UBER":
        hit = conn.execute("SELECT priority FROM spend_rules "
                           "WHERE keyword = 'UBER'").fetchone()
        if hit:
            return max(int(hit[0]) - 1, 0)
    top = conn.execute("SELECT COALESCE(MAX(priority), 0) FROM spend_rules"
                       ).fetchone()[0]
    return int(top) + 1


def add_rules(conn: sqlite3.Connection) -> list[str]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    done = []
    for kw, cat, sub, where in STARTER_RULES:
        if conn.execute("SELECT 1 FROM spend_rules WHERE keyword = ?",
                        (kw,)).fetchone():
            done.append(f"  rule '{kw}' already there - left as it is")
            continue
        if not conn.execute("SELECT 1 FROM spend_subcategories "
                            "WHERE category = ? AND name = ?",
                            (cat, sub)).fetchone():
            done.append(f"  rule '{kw}' skipped - {cat} / {sub} is not a "
                        "sub-category")
            continue
        pri = rule_priority(conn, where)
        conn.execute("INSERT INTO spend_rules (keyword, category, subcategory, "
                     "priority, created_at) VALUES (?, ?, ?, ?, ?)",
                     (kw, cat, sub, pri, now))
        done.append(f"  rule '{kw}' -> {cat} / {sub}, priority {pri}")
    return done


def reset_card(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM spend_transactions WHERE card = ?", (CARD,))
    conn.execute("DELETE FROM spend_imports WHERE card = ?", (CARD,))
    conn.execute("""
        INSERT OR IGNORE INTO spend_cards (name, sort_order, active,
                                           created_at, currency)
        VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM spend_cards),
                1, ?, ?)
    """, (CARD, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), CURRENCY))
    conn.execute("UPDATE spend_cards SET currency = ?, active = 1 "
                 "WHERE name = ?", (CURRENCY, CARD))


def verify(conn: sqlite3.Connection) -> list[str]:
    problems = []
    after = report(conn)
    if after["rows"] or after["imports"]:
        problems.append(f"{CARD} still has {after['rows']} rows and "
                        f"{after['imports']} imports")
    card = conn.execute("SELECT currency, active FROM spend_cards "
                        "WHERE name = ?", (CARD,)).fetchone()
    if card != (CURRENCY, 1):
        problems.append(f"{CARD} is {card}, expected ({CURRENCY!r}, 1)")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--apply", action="store_true",
                    help="write to funds.db (default: dry run)")
    args = ap.parse_args()

    target = Path(config.DB_PATH)
    if not target.exists():
        print(f"Database not found: {target}")
        return 1
    print(f"Target  {target}")
    conn = sqlite3.connect(target, isolation_level=None)
    try:
        try:
            rates = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) "
                                 "FROM prices WHERE fund_id = ?",
                                 (RATE_ID,)).fetchone()
        except sqlite3.OperationalError:          # no prices table
            rates = (0, None, None)
        if rates[0]:
            print(f"  {RATE_ID}: {rates[0]:,} daily rates, {rates[1]} to "
                  f"{rates[2]} - used to convert lira to pounds")
        else:
            print(f"  {RATE_ID}: no rates stored - Import will refuse lira "
                  "statements until prices are refreshed")
        if args.apply:
            print(f"Backup  {backup(target)}")
        conn.execute("BEGIN")
        ensure_schema(conn)
        before = report(conn)
        if before["rows"]:
            print(f"\n{CARD}: {before['rows']:,} transactions, "
                  f"{before['from']} to {before['to']}, total "
                  f"{before['total']:,.2f}, from {before['imports']} "
                  "imports - all to be deleted")
        else:
            print(f"\n{CARD}: no transactions stored")
        reset_card(conn)
        print(f"{CARD}: currency {CURRENCY}, in use")
        print("Starter rules:")
        for line in add_rules(conn):
            print(line)
        problems = verify(conn)
        if problems:
            conn.execute("ROLLBACK")
            print("\nVerification failed - nothing written:")
            for p in problems:
                print(f"  {p}")
            return 1
        if not args.apply:
            conn.execute("ROLLBACK")
            print("\nDry run: nothing written. Run again with --apply.")
            return 0
        conn.execute("COMMIT")
        print("\nDone. Restart Pusula, then upload the statements under "
              "Spending -> Import.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
