"""
Nightly database backup.

Uses SQLite's own backup API rather than copying the file. With WAL enabled a
plain copy can catch the database mid-transaction and produce a backup that
looks fine but will not open; the backup API takes a consistent snapshot of a
live database.

Usage
-----
    python3 -m importers.backup
"""

from __future__ import annotations

import glob
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from core import config

KEEP_DAYS = 7


def backup_dir() -> Path:
    return Path(config.DB_PATH).parent / "backups"


def run() -> dict:
    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        return {"ok": False, "message": f"Database not found at {db_path}"}

    target_dir = backup_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"funds_{datetime.today():%Y-%m-%d}.db"

    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()

    size_mb = target.stat().st_size / 1024 / 1024
    print(f"Backup created: {target} ({size_mb:.1f} MB)")

    # Sorted by filename, which is date-ordered, so the oldest fall off first.
    existing = sorted(glob.glob(str(target_dir / "funds_*.db")))
    removed = 0
    for old in existing[:-KEEP_DAYS]:
        os.remove(old)
        print(f"Deleted old backup: {Path(old).name}")
        removed += 1

    kept = sorted(glob.glob(str(target_dir / "funds_*.db")))
    print(f"Backups kept: {len(kept)}")
    return {"ok": True, "kept": len(kept), "removed": removed,
            "message": f"{size_mb:.1f} MB, {len(kept)} kept"}


if __name__ == "__main__":
    run()
