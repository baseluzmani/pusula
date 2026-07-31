# setup_instruments.py
# Creates the instruments table and populates it from config.py
# Safe to run multiple times — only adds missing instruments, never deletes.
# Run: python3 setup_instruments.py

import sqlite3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def get_connection():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect("data/funds.db")
    conn.row_factory = sqlite3.Row
    return conn


def create_instruments_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            fund_id     TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            asset_type  TEXT,
            currency    TEXT,
            price_unit  TEXT
        )
    """)
    conn.commit()


def guess_currency_and_unit(fund_id, asset_type):
    """
    Educated guess for currency and price unit based on fund_id pattern.
    currency  : GBP, USD, EUR, TRY
    price_unit: pence, pound, dollar, point, ratio
    """
    fid = fund_id.upper()

    # Composite funds — always GBP pounds (calculated index)
    if fid.startswith('COMPOSITE:'):
        return 'GBP', 'pound'

    # FT funds — GB or LU ISINs are priced in pence GBP
    if not fid.startswith('YF:'):
        return 'GBP', 'pence'

    # Yahoo Finance — strip the YF: prefix
    ticker = fund_id[3:]  # remove 'YF:'
    t = ticker.upper()

    # FX / ratios — not useful for portfolio value
    if t.endswith('=X'):
        return 'GBP', 'ratio'

    # Gold spot
    if t == 'XAUUSD=X':
        return 'USD', 'dollar'

    # London listed (.L) — priced in pence GBP
    if t.endswith('.L'):
        return 'GBP', 'pence'

    # Istanbul listed (.IS) — Turkish lira points
    if t.endswith('.IS'):
        return 'TRY', 'point'

    # Crypto in GBP
    if t.endswith('-GBP'):
        return 'GBP', 'pound'

    # Futures — USD
    if t.endswith('=F'):
        return 'USD', 'dollar'

    # Indices
    if t.startswith('^'):
        return 'USD', 'point'

    # US listed stocks/ETFs — USD dollars
    return 'USD', 'dollar'


def upsert_instrument(conn, fund_id, name, asset_type):
    """Insert if not exists — never overwrites manual changes."""
    existing = conn.execute(
        "SELECT fund_id FROM instruments WHERE fund_id = ?", (fund_id,)
    ).fetchone()

    if existing:
        return False  # already there

    currency, price_unit = guess_currency_and_unit(fund_id, asset_type)
    conn.execute("""
        INSERT INTO instruments (fund_id, name, asset_type, currency, price_unit)
        VALUES (?, ?, ?, ?, ?)
    """, (fund_id, name, asset_type, currency, price_unit))
    return True


def main():
    conn = get_connection()
    create_instruments_table(conn)

    added = 0

    # ── FT funds from FUNDS list
    print("Processing FUNDS...")
    for fund in config.FUNDS:
        if upsert_instrument(conn, fund['id'], fund['name'], 'Fund'):
            print(f"  + {fund['id']} — {fund['name']}")
            added += 1

    # ── Yahoo Finance tickers
    print("\nProcessing YAHOO_TICKERS...")
    for item in config.YAHOO_TICKERS:
        ticker, name, asset_type = item
        fund_id = f"YF:{ticker}"
        if upsert_instrument(conn, fund_id, name, asset_type):
            print(f"  + {fund_id} — {name}")
            added += 1

    # ── Composite funds
    print("\nProcessing COMPOSITE_FUNDS...")
    for comp in getattr(config, 'COMPOSITE_FUNDS', []):
        if upsert_instrument(conn, comp['fund_id'], comp['display_name'], comp.get('asset_type', 'Fund')):
            print(f"  + {comp['fund_id']} — {comp['display_name']}")
            added += 1

    conn.commit()
    conn.close()

    print(f"\nDone. {added} new instruments added.")
    print("\nTo review: SELECT * FROM instruments ORDER BY asset_type, fund_id;")
    print("To fix a currency: UPDATE instruments SET currency='GBP', price_unit='pence' WHERE fund_id='YF:XXXX';")


if __name__ == "__main__":
    main()