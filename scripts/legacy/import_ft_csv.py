# import_ft_csv.py
# Imports FT historical price data from tab-separated text files
# Place your FT data file in data/imports/ft/ folder
# File should be named with fund_id e.g. GB00B6Y7NF43:GBX.txt
#
# Usage: python3 scripts/import_ft_csv.py

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import glob
from datetime import datetime

DB_PATH    = 'data/funds.db'
IMPORT_DIR = 'data/import'


def parse_date(date_str):
    """Parse dates like 'Wednesday, May 06, 2026'"""
    date_str = date_str.strip()
    # Remove day name
    if ',' in date_str:
        parts = date_str.split(',', 1)
        date_str = parts[1].strip()
    try:
        return datetime.strptime(date_str, '%B %d, %Y').strftime('%Y-%m-%d')
    except ValueError:
        try:
            return datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            return None


def parse_number(val):
    """Parse numbers like '2,766.00'"""
    try:
        return float(str(val).strip().replace(',', ''))
    except (ValueError, AttributeError):
        return None


def import_file(filepath, conn):
    fund_id  = os.path.splitext(os.path.basename(filepath))[0]
    inserted = 0
    skipped  = 0

    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # Skip fund name and fund id lines if present
    # Find header line (contains 'Date')
    data_lines = []
    header_found = False
    for line in lines:
        if line.strip().startswith('Date'):
            header_found = True
            continue
        if header_found and line.strip():
            data_lines.append(line.strip())

    if not data_lines:
        print(f"  No data found in {filepath}")
        return

    for line in data_lines:
        parts = line.split('\t')
        if len(parts) < 5:
            continue

        date = parse_date(parts[0])
        if not date:
            continue

        open_  = parse_number(parts[1])
        high   = parse_number(parts[2])
        low    = parse_number(parts[3])
        close  = parse_number(parts[4])
        volume = parse_number(parts[5]) if len(parts) > 5 else 0

        try:
            conn.execute("""
                INSERT OR REPLACE INTO prices
                    (fund_id, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (fund_id, date, open_, high, low, close, int(volume or 0)))
            inserted += 1
        except Exception as e:
            skipped += 1

    conn.commit()
    print(f"  {fund_id}: {inserted} rows inserted, {skipped} skipped")


def main():
    os.makedirs(IMPORT_DIR, exist_ok=True)
    files = glob.glob(os.path.join(IMPORT_DIR, '*.txt'))

    if not files:
        print(f"No .txt files found in {IMPORT_DIR}/")
        print("Place FT data files there named as: GB00B6Y7NF43:GBX.txt")
        return

    conn = sqlite3.connect(DB_PATH)
    print(f"Found {len(files)} file(s):\n")
    for f in files:
        import_file(f, conn)
    conn.close()
    print("\nDone.")


if __name__ == '__main__':
    main()