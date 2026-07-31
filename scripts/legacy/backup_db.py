# backup_db.py
# Creates a daily backup of funds.db and keeps last 7 days only.
# Run via cron: 0 23 * * * cd ~/FTScrapper && venv/bin/python3 backup_db.py

import os
import shutil
import sqlite3
import glob
from datetime import datetime

DB_PATH     = 'data/funds.db'
BACKUP_DIR  = 'data/backups'
KEEP_DAYS   = 7

def main():
    if not os.path.exists(DB_PATH):
        print("ERROR: funds.db not found")
        return

    os.makedirs(BACKUP_DIR, exist_ok=True)

    # Create today's backup
    date_str    = datetime.today().strftime('%Y-%m-%d')
    backup_path = os.path.join(BACKUP_DIR, f'funds_{date_str}.db')
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(backup_path)
    with dst:
        src.backup(dst)
    dst.close()
    src.close()
    size_mb = os.path.getsize(backup_path) / 1024 / 1024
    print(f"Backup created: {backup_path} ({size_mb:.1f} MB)")

    # Delete backups older than KEEP_DAYS
    all_backups = sorted(glob.glob(os.path.join(BACKUP_DIR, 'funds_*.db')))
    to_delete   = all_backups[:-KEEP_DAYS]  # keep last 7, delete the rest

    for f in to_delete:
        os.remove(f)
        print(f"Deleted old backup: {f}")

    remaining = sorted(glob.glob(os.path.join(BACKUP_DIR, 'funds_*.db')))
    print(f"Backups kept: {len(remaining)}")
    for f in remaining:
        size = os.path.getsize(f) / 1024 / 1024
        print(f"  {os.path.basename(f)} ({size:.1f} MB)")

if __name__ == '__main__':
    main()