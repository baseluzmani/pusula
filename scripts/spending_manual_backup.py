#!/usr/bin/env python3
"""
Back up how you categorised your spending lines, and put it back on lines
that lost it.

    python scripts/spending_manual_backup.py                    # back up
    python scripts/spending_manual_backup.py --restore FILE     # dry run
    python scripts/spending_manual_backup.py --restore FILE --apply

Back up (run it before reloading from statements)
    Writes ~/data/backups/spending_manual_<time>.csv: every line you have
    placed - its card, date, description, amount, category, sub-category,
    note, and split parts if you split it - with by_hand = 1 where the
    current rules would not have placed it that way (the work worth
    keeping). Also copies funds.db next to it.

Restore
    A reload from statements keeps every line it matches, so nothing is
    lost there. A line it could not match is replaced: the old one is
    deleted (not on the statement) and the statement's line comes in new,
    pending if no rule places it. Restore finds those: for each backed-up
    line that is no longer in the database, a pending line of the same card
    within three days and 5p (the 8052 history rounded amounts to 10p) -
    the same shop first, then the closest date and amount - takes its
    category, note and split. The pending line keeps its own, exact,
    amount. One backed-up line fills at most one line. The old description
    goes into the note when it said more than the new one.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd                                  # noqa: E402

from core import config, spending as sp              # noqa: E402
from core.repo import spending as repo               # noqa: E402

FIELDS = ["id", "card", "date", "description", "amount", "amount_orig",
          "category", "subcategory", "note", "status", "by_hand", "splits"]
DAYS = 3


def backup_db(target: Path, stamp: str) -> Path:
    dest = target.parent / "backups" / f"funds_pre_statement_reload_{stamp}.db"
    dest.parent.mkdir(parents=True, exist_ok=True)
    src, out = sqlite3.connect(target), sqlite3.connect(dest)
    with out:
        src.backup(out)
    out.close()
    src.close()
    return dest


def placed_lines() -> pd.DataFrame:
    """Every confirmed line with a category or a split, with by_hand and
    its parts as JSON."""
    df = repo.transactions(status="confirmed")
    parts = repo.splits_for(df["id"])
    split_ids = set(parts["transaction_id"])
    df = df[df["category"].notna() | df["id"].isin(split_ids)].copy()
    rules = repo.rule_tuples()
    df["by_hand"] = [
        int(i in split_ids or sp.classify(d, rules, a).category != c
            or sp.classify(d, rules, a).subcategory != s)
        for i, d, a, c, s in zip(df["id"], df["description"], df["amount"],
                                 df["category"], df["subcategory"])]
    by_line = {int(t): g[["amount", "category", "subcategory", "note"]]
               .to_dict("records") for t, g in parts.groupby("transaction_id")}
    df["splits"] = [json.dumps(by_line[i], default=str) if i in by_line
                    else "" for i in df["id"]]
    return df


def do_backup() -> int:
    target = Path(config.DB_PATH)
    if not target.exists():
        print(f"Database not found: {target}")
        return 1
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    df = placed_lines()
    out = target.parent / "backups" / f"spending_manual_{stamp}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    df[FIELDS].to_csv(out, index=False, quoting=csv.QUOTE_NONNUMERIC)
    print(f"Backup  {out}")
    print(f"  {len(df):,} placed lines, {int(df['by_hand'].sum()):,} of them "
          "by hand (the rules would not place them that way), "
          f"{int((df['splits'] != '').sum()):,} split")
    print(f"Database copy  {backup_db(target, stamp)}")
    return 0


def plan_restore(backup: pd.DataFrame, current: pd.DataFrame,
                 pending: pd.DataFrame) -> list[tuple[dict, dict]]:
    """(backed-up line, pending line it fills) pairs. Pure, so tested."""
    gone = backup[~backup["id"].isin(current["id"])]
    free = pending.to_dict("records")
    out = []
    for b in gone.sort_values("date").to_dict("records"):
        bdate = pd.Timestamp(b["date"])
        options = [p for p in free if p["card"] == b["card"]
                   and (float(p["amount"]) < 0) == (float(b["amount"]) < 0)
                   and abs(float(p["amount"]) - float(b["amount"]))
                   <= sp.AMOUNT_SLACK + 1e-9
                   and abs((pd.Timestamp(p["date"]) - bdate).days) <= DAYS]
        if not options:
            continue
        options.sort(key=lambda p: (
            not sp.same_shop(p["description"], b["description"]),
            abs((pd.Timestamp(p["date"]) - bdate).days),
            abs(float(p["amount"]) - float(b["amount"]))))
        best = options[0]
        free.remove(best)
        out.append((b, best))
    return out


def do_restore(path: Path, apply: bool) -> int:
    if not path.exists():
        print(f"Backup not found: {path}")
        return 1
    backup = pd.read_csv(path, keep_default_na=False,
                         dtype={"note": str, "splits": str})
    backup["id"] = backup["id"].astype(int)
    current = repo.transactions()
    pending = repo.pending()
    pending = pending[~pending["id"].isin(repo.splits_for(
        pending["id"])["transaction_id"])]
    pairs = plan_restore(backup, current, pending)
    gone = int((~backup["id"].isin(current["id"])).sum())
    print(f"Backup  {path}: {len(backup):,} lines, {gone:,} no longer stored")
    print(f"  {len(pairs):,} of those match a pending line - card, amount, "
          f"date within {DAYS} days")
    for b, p in pairs[:15]:
        what = "split" if b["splits"] else f"{b['category']} / {b['subcategory']}"
        print(f"    {p['date']} {p['card']:<12} {p['description'][:38]:<38} "
              f"{float(p['amount']):>10,.2f}  <- {what}")
    if len(pairs) > 15:
        print(f"    ... and {len(pairs) - 15:,} more")
    if not apply:
        print("\nDry run: nothing written. Run again with --apply.")
        return 0
    done = 0
    for b, p in pairs:
        note = b["note"] or ""
        if sp.extra_wording(b["description"], p["description"]):
            note = " · ".join(x for x in [note, b["description"]] if x)
        if b["splits"]:
            parts = json.loads(b["splits"])
            # The parts were made for the old amount; the last one takes up
            # any pence of difference.
            diff = round(float(p["amount"]) - sum(float(x["amount"])
                                                  for x in parts), 2)
            parts[-1]["amount"] = round(float(parts[-1]["amount"]) + diff, 2)
            if repo.save_split(int(p["id"]), parts) is None:
                repo.set_note([int(p["id"])], note or None)
                done += 1
            continue
        repo.assign([int(p["id"])], b["category"], b["subcategory"],
                    note=note or None)
        done += 1
    print(f"\nRestored {done:,} line{'s' * (done != 1)}.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--restore", type=Path, metavar="FILE",
                    help="a backup CSV to restore from")
    ap.add_argument("--apply", action="store_true",
                    help="with --restore: write (default: dry run)")
    args = ap.parse_args()
    if args.restore:
        return do_restore(args.restore, args.apply)
    return do_backup()


if __name__ == "__main__":
    sys.exit(main())
