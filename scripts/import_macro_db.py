#!/usr/bin/env python3
"""
Copy the existing macro.db into funds.db.

223,000 observations across 30 series, some back to 1854, collected before the
consolidation. Fetching them again from FRED would work but would take a while
and pointlessly re-download decades of settled history.

Series metadata is not copied: the migration already seeded macro_series with
the same ids plus the dashboard flag and the new series. Only observations
whose series exists there are brought over, so a series dropped from the list
does not silently reappear.

    python3 import_macro_db.py            # dry run
    python3 import_macro_db.py --apply
"""

import sqlite3
import sys
from pathlib import Path

SOURCE = Path.home() / "macro-data" / "data" / "macro.db"
TARGET = Path.home() / "data" / "funds.db"


def main(apply_changes):
    if not SOURCE.exists():
        sys.exit(f"Source not found: {SOURCE}")
    if not TARGET.exists():
        sys.exit(f"Target not found: {TARGET}")

    src = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    dst = sqlite3.connect(TARGET, timeout=30)

    known = {r[0] for r in dst.execute("SELECT id FROM macro_series")}
    if not known:
        sys.exit("macro_series is empty - run the migration first.")

    rows = src.execute("""
        SELECT series_id, date, value FROM observations ORDER BY series_id, date
    """).fetchall()

    wanted = [r for r in rows if r[0] in known]
    skipped = {r[0] for r in rows if r[0] not in known}

    by_series = {}
    for sid, d, _v in wanted:
        e = by_series.setdefault(sid, [d, d, 0])
        e[0] = min(e[0], d)
        e[1] = max(e[1], d)
        e[2] += 1

    print(f"{len(rows):,} observations in source, {len(wanted):,} to copy\n")
    print(f"{'series':<16}{'rows':>9}  {'from':<12}{'to':<12}")
    print("-" * 52)
    for sid in sorted(by_series):
        lo, hi, n = by_series[sid]
        print(f"{sid:<16}{n:>9,}  {lo:<12}{hi:<12}")

    if skipped:
        print(f"\nNot in macro_series, skipped: {', '.join(sorted(skipped))}")

    existing = dst.execute("SELECT COUNT(*) FROM macro_observations").fetchone()[0]
    if existing:
        print(f"\nmacro_observations already holds {existing:,} rows. "
              f"INSERT OR IGNORE, so nothing already there is overwritten.")

    if not apply_changes:
        print("\nDry run. Re-run with --apply to write.")
        return

    dst.executemany("""
        INSERT OR IGNORE INTO macro_observations (series_id, date, value)
        VALUES (?, ?, ?)
    """, wanted)
    dst.commit()

    total = dst.execute("SELECT COUNT(*) FROM macro_observations").fetchone()[0]
    print(f"\nWritten. macro_observations now holds {total:,} rows.")
    src.close()
    dst.close()


if __name__ == "__main__":
    main("--apply" in sys.argv)
