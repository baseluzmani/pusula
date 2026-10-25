#!/usr/bin/env python3
"""
One-off: bring the 8052 spending app's data into funds.db.

    python scripts/migrate_spending.py            # dry run - reports, writes nothing
    python scripts/migrate_spending.py --apply    # backs up funds.db, copies, verifies

Source: ~/spending_dashboard/spending.db (or --source PATH), opened read-only.
Target: the spend_* tables in funds.db (core/repo/spending.py).

What changes on the way
-----------------------
- Amounts become signed, as in the bank file: 8052 stored every amount as a
  positive number plus an is_credit flag; here spending is negative.
- "source" becomes "card", and "Burcu Card" (Sep 2021 - Dec 2025) is merged
  into "Burcu Debit" (from Jan 2026) - the same card under a later name.
- Each 8052 import batch becomes a row in spend_imports.
- Rules keep their ids, so rules with equal priority are still checked in
  the order 8052 checked them.

Everything is copied in one transaction and verified before it commits: row
counts, signed totals and pending counts per card, rules and categories.
Any mismatch rolls the lot back. Refuses to run if spend_transactions already
has rows, unless --replace (which empties the spend_* tables first).
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

DEFAULT_SOURCE = Path.home() / "spending_dashboard" / "spending.db"
MERGE = {"Burcu Card": "Burcu Debit"}
CARD_ORDER = ["Debit Card", "Credit Card", "Burcu Debit", "Burcu Credit",
              "Turkey Card"]
TABLES = ["spend_transactions", "spend_imports", "spend_rules",
          "spend_subcategories", "spend_categories", "spend_cards"]


def card(source: str) -> str:
    return MERGE.get(source, source)


def batch_filename(source: str, batch: str) -> str:
    """'Credit Card_credit_1.csv' -> 'credit_1.csv'."""
    if batch == "legacy_import":
        return "Harcamalar history (8052 legacy import)"
    prefix = f"{source}_"
    return batch[len(prefix):] if batch.startswith(prefix) else batch


def load_source(path: Path) -> dict:
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    q = lambda sql: [dict(r) for r in src.execute(sql)]       # noqa: E731
    data = {
        "transactions": q("SELECT * FROM transactions ORDER BY id"),
        "rules": q("SELECT * FROM mapping_rules ORDER BY priority, id"),
        "categories": q("SELECT * FROM categories ORDER BY sort_order, id"),
        "subcategories": q("SELECT * FROM subcategories ORDER BY id"),
        "overrides": q("SELECT * FROM manual_overrides"),
    }
    src.close()
    return data


def expected(data: dict) -> dict:
    """Per-card figures computed from the source, after the merge."""
    out: dict = {}
    for t in data["transactions"]:
        c = card(t["source"])
        e = out.setdefault(c, {"rows": 0, "total": 0.0, "pending": 0})
        e["rows"] += 1
        e["total"] += t["amount"] if t["is_credit"] else -t["amount"]
        e["pending"] += t["status"] == "pending"
    return out


def backup(target: Path) -> Path:
    dest_dir = target.parent / "backups"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"funds_pre_spending_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(target)
    out = sqlite3.connect(dest)
    with out:
        src.backup(out)
    out.close()
    src.close()
    return dest


def copy(conn: sqlite3.Connection, data: dict) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Cards: the known five first, anything else after.
    sources = {card(t["source"]) for t in data["transactions"]}
    names = [c for c in CARD_ORDER if c in sources] + sorted(
        sources - set(CARD_ORDER))
    for i, name in enumerate(names):
        conn.execute("INSERT INTO spend_cards (name, sort_order, active, "
                     "created_at) VALUES (?, ?, 1, ?)", (name, i, now))

    for c in data["categories"]:
        conn.execute("INSERT INTO spend_categories (name, sort_order) "
                     "VALUES (?, ?)", (c["name"], c["sort_order"]))
    for s in data["subcategories"]:
        conn.execute("INSERT OR IGNORE INTO spend_subcategories "
                     "(category, name) VALUES (?, ?)",
                     (s["category"], s["name"]))
    for r in data["rules"]:
        conn.execute("INSERT INTO spend_rules (id, keyword, category, "
                     "subcategory, priority, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (r["id"], r["keyword"], r["category"], r["subcategory"],
                      r["priority"], now))

    # One spend_imports row per 8052 batch, oldest first.
    batches: dict = {}
    for t in data["transactions"]:
        k = (t["source"], t["import_batch"] or "")
        b = batches.setdefault(k, {"rows": 0, "first": t["date"],
                                   "last": t["date"],
                                   "created": t["created_at"] or now})
        b["rows"] += 1
        b["first"] = min(b["first"], t["date"])
        b["last"] = max(b["last"], t["date"])
        b["created"] = min(b["created"], t["created_at"] or now)
    import_id = {}
    for (source, batch), b in sorted(batches.items(),
                                     key=lambda kv: kv[1]["created"]):
        cur = conn.execute("""
            INSERT INTO spend_imports (card, filename, imported_at,
                rows_in_file, inserted, skipped, first_date, last_date)
            VALUES (?, ?, ?, ?, ?, 0, ?, ?)
        """, (card(source), batch_filename(source, batch), b["created"],
              b["rows"], b["rows"], b["first"], b["last"]))
        import_id[(source, batch)] = cur.lastrowid

    conn.executemany("""
        INSERT INTO spend_transactions (id, date, card, description, amount,
            category, subcategory, status, import_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [(t["id"], t["date"], card(t["source"]), t["description"],
           round(t["amount"] if t["is_credit"] else -t["amount"], 2),
           t["category"], t["subcategory"], t["status"] or "confirmed",
           import_id[(t["source"], t["import_batch"] or "")],
           t["created_at"] or now, t["created_at"] or now)
          for t in data["transactions"]])


def verify(conn: sqlite3.Connection, data: dict) -> list[str]:
    problems = []
    want = expected(data)
    got = {r[0]: {"rows": r[1], "total": r[2], "pending": r[3]}
           for r in conn.execute("""
               SELECT card, COUNT(*), SUM(amount), SUM(status = 'pending')
               FROM spend_transactions GROUP BY card""")}
    print(f"\n  {'Card':<14}{'Rows':>8}{'Pending':>9}{'Net total':>16}")
    for c in sorted(set(want) | set(got)):
        w, g = want.get(c, {}), got.get(c, {})
        ok = (w.get("rows") == g.get("rows")
              and w.get("pending") == g.get("pending")
              and abs((w.get("total") or 0) - (g.get("total") or 0)) < 0.005)
        print(f"  {c:<14}{g.get('rows', 0):>8,}{g.get('pending', 0):>9,}"
              f"{g.get('total', 0):>16,.2f}  {'ok' if ok else 'MISMATCH'}")
        if not ok:
            problems.append(f"{c}: expected {w}, got {g}")
    for table, n in [("spend_rules", len(data["rules"])),
                     ("spend_categories", len(data["categories"]))]:
        have = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {have}")
        if have != n:
            problems.append(f"{table}: expected {n}, got {have}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    ap.add_argument("--apply", action="store_true",
                    help="write to funds.db (default: dry run)")
    ap.add_argument("--replace", action="store_true",
                    help="empty the spend_* tables first")
    args = ap.parse_args()

    if not args.source.exists():
        print(f"Source not found: {args.source}")
        return 1
    target = Path(config.DB_PATH)
    data = load_source(args.source)
    if data["overrides"]:
        print(f"{len(data['overrides'])} manual overrides in the source - "
              "this script does not carry those over. Stopping.")
        return 1

    print(f"Source  {args.source}")
    print(f"Target  {target}")
    print(f"  {len(data['transactions']):,} transactions, "
          f"{len(data['rules'])} rules, {len(data['categories'])} "
          f"categories, {len(data['subcategories'])} sub-categories")
    for old, new in MERGE.items():
        n = sum(t["source"] == old for t in data["transactions"])
        if n:
            print(f"  {n:,} rows of '{old}' will be booked to '{new}'")

    conn = sqlite3.connect(target, isolation_level=None)
    try:
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM spend_transactions").fetchone()[0]
        except sqlite3.OperationalError:          # table not created yet
            existing = 0
        if existing and not args.replace:
            print(f"\nspend_transactions already has {existing:,} rows. "
                  "Nothing done. Use --replace to start again.")
            return 1
        if args.apply:
            print(f"\nBackup  {backup(target)}")
        # Tables are created inside the transaction, so a dry run (or a
        # failed check) leaves funds.db exactly as it was.
        conn.execute("BEGIN")
        for stmt in repo.SCHEMA.split(";"):
            if stmt.strip():
                conn.execute(stmt)
        if existing:
            for t in TABLES:
                conn.execute(f"DELETE FROM {t}")
        copy(conn, data)
        problems = verify(conn, data)
        if problems:
            conn.execute("ROLLBACK")
            print("\nVerification failed - nothing written:")
            for p in problems:
                print(f"  {p}")
            return 1
        if not args.apply:
            conn.execute("ROLLBACK")
            print("\nDry run: all checks pass. Nothing written. "
                  "Run again with --apply.")
            return 0
        conn.execute("COMMIT")
        print("\nDone. Stop the old app with:\n"
              "  sudo systemctl disable --now spending")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
