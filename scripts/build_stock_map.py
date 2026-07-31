"""
Build Stock Identifier Map
--------------------------
Creates stock_identifier_map table and populates it from:
1. Existing etf_holding_ticker_map (already reviewed/mapped)
2. OpenFIGI API for enrichment (FIGI, name, exchange code)
3. ETF holdings source data (SEDOL, ISIN where available)

Usage:
    python3 scripts/build_stock_map.py              # enrich unmapped only
    python3 scripts/build_stock_map.py --rebuild    # drop and rebuild from scratch
    python3 scripts/build_stock_map.py --new-only   # only process new holdings not yet in map
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import requests
import time
import argparse
import json

from core import config

DB_PATH    = str(config.DB_PATH)
FIGI_URL   = config.FIGI_URL
FIGI_KEY   = config.OPENFIGI_API_KEY
BATCH_SIZE = config.FIGI_BATCH_SIZE
RATE_SLEEP = config.FIGI_RATE_SLEEP


# OpenFIGI exchCode → Yahoo Finance suffix
EXCH_TO_YAHOO = {
    'US':  '',     'UQ':  '',     'UN':  '',     'UP':  '',
    'UA':  '',     'UR':  '',
    'LN':  '.L',   'LX':  '.L',
    'GY':  '.DE',  'GF':  '.F',   'GS':  '.SG',
    'FP':  '.PA',
    'IM':  '.MI',
    'NA':  '.AS',
    'SM':  '.MC',
    'SS':  '.ST',
    'FH':  '.HE',
    'DC':  '.CO',
    'BB':  '.BR',
    'PW':  '.WA',
    'KS':  '.KS',  'KQ':  '.KQ',
    'JT':  '.T',
    'TT':  '.TW',
    'HK':  '.HK',
    'AU':  '.AX',
    'SP':  '.SI',
    'SJ':  '.JO',
    'CN':  '.TO',  'CV':  '.V',
    'IT':  '.TA',
    'IN':  '.NS',  'IB':  '.BO',
    'MK':  '.KL',
    'TB':  '.BK',
    'ID':  '.JK',
}


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def create_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS stock_identifier_map (
            figi            TEXT PRIMARY KEY,
            name            TEXT,
            base_ticker     TEXT,
            exch_code       TEXT,
            bloomberg_code  TEXT,
            raw_ticker      TEXT,
            sedol           TEXT,
            isin            TEXT,
            yahoo_id        TEXT,
            security_type   TEXT,
            group_figi      TEXT,
            reviewed        INTEGER DEFAULT 0,
            notes           TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sim_yahoo_id
            ON stock_identifier_map(yahoo_id);
        CREATE INDEX IF NOT EXISTS idx_sim_bloomberg
            ON stock_identifier_map(bloomberg_code);
        CREATE INDEX IF NOT EXISTS idx_sim_raw_ticker
            ON stock_identifier_map(raw_ticker);
        CREATE INDEX IF NOT EXISTS idx_sim_sedol
            ON stock_identifier_map(sedol);
        CREATE INDEX IF NOT EXISTS idx_sim_isin
            ON stock_identifier_map(isin);
        CREATE INDEX IF NOT EXISTS idx_sim_base_ticker
            ON stock_identifier_map(base_ticker);
        CREATE INDEX IF NOT EXISTS idx_sim_group_figi
            ON stock_identifier_map(group_figi);
    """)
    # Add new columns to existing table if upgrading
    for col, defn in [('raw_ticker', 'TEXT'), ('group_figi', 'TEXT')]:
        try:
            conn.execute(f"ALTER TABLE stock_identifier_map ADD COLUMN {col} {defn}")
            conn.commit()
        except:
            pass  # column already exists
    # Set group_figi = figi for all records that don't have it set
    conn.execute("""
        UPDATE stock_identifier_map SET group_figi = figi
        WHERE group_figi IS NULL OR group_figi = ''
    """)
    conn.commit()
    print("  Table stock_identifier_map ready")


def openfigi_lookup(jobs):
    """
    Submit a batch of jobs to OpenFIGI.
    Each job: {'idType': 'ID_SEDOL'|'TICKER'|'ID_ISIN', 'idValue': '...', 'exchCode': '...'}
    Returns list of results aligned with jobs (None if no match).
    """
    headers = {
        'Content-Type': 'application/json',
        'X-OPENFIGI-APIKEY': FIGI_KEY,
    }
    try:
        resp = requests.post(FIGI_URL, headers=headers, json=jobs, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        PRIMARY = {'US', 'UQ', 'UN', 'UP', 'UA', 'LN', 'GY', 'FP', 'IM',
                    'NA', 'SM', 'SS', 'FH', 'KS', 'KQ', 'JT', 'TT', 'HK',
                    'AU', 'SP', 'SJ', 'CN', 'IT', 'IN', 'IB', 'MK', 'NO',
                    'TI', 'IJ', 'CS', 'CH', 'DC', 'BB', 'PW'}
        results = []
        for item in data:
            if 'data' in item and item['data']:
                equities = [d for d in item['data']
                           if d.get('marketSector') == 'Equity'
                           and d.get('securityType2') in ('Common Stock', 'ETP', 'ETF', 'Depositary Receipt')]
                if equities:
                    # Prefer primary exchange listings
                    primary = [e for e in equities if e.get('exchCode') in PRIMARY]
                    results.append(primary[0] if primary else equities[0])
                elif item['data']:
                    results.append(item['data'][0])
                else:
                    results.append(None)
            else:
                results.append(None)
        return results
    except Exception as e:
        print(f"  OpenFIGI error: {e}")
        return [None] * len(jobs)


def derive_yahoo_id(ticker, exch_code):
    """Construct Yahoo Finance ticker from OpenFIGI base ticker and exchange code."""
    if not ticker or not exch_code:
        return None
    suffix = EXCH_TO_YAHOO.get(exch_code, '')
    return f"YF:{ticker}{suffix}"


def insert_or_update(conn, figi, name, base_ticker, exch_code,
                     sedol=None, isin=None, security_type=None, raw_ticker=None):
    """Insert or update a stock_identifier_map record."""
    bloomberg_code = f"{base_ticker} {exch_code}" if base_ticker and exch_code else base_ticker
    yahoo_id       = derive_yahoo_id(base_ticker, exch_code)

    conn.execute("""
        INSERT INTO stock_identifier_map
            (figi, name, base_ticker, exch_code, bloomberg_code,
             raw_ticker, sedol, isin, yahoo_id, security_type, group_figi)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(figi) DO UPDATE SET
            name           = COALESCE(excluded.name,           name),
            base_ticker    = COALESCE(excluded.base_ticker,    base_ticker),
            exch_code      = COALESCE(excluded.exch_code,      exch_code),
            bloomberg_code = COALESCE(excluded.bloomberg_code, bloomberg_code),
            raw_ticker     = COALESCE(excluded.raw_ticker,     raw_ticker),
            sedol          = COALESCE(excluded.sedol,          sedol),
            isin           = COALESCE(excluded.isin,           isin),
            yahoo_id       = COALESCE(excluded.yahoo_id,       yahoo_id),
            security_type  = COALESCE(excluded.security_type,  security_type),
            group_figi     = COALESCE(excluded.group_figi,     group_figi)
    """, (figi, name, base_ticker, exch_code, bloomberg_code,
          raw_ticker, sedol, isin, yahoo_id, security_type, figi))


def enrich_from_existing_map(conn):
    """
    Use existing etf_holding_ticker_map (reviewed entries with yahoo_fund_id)
    as the seed. Look up each yahoo_fund_id via OpenFIGI TICKER search.
    """
    print("\nStep 1: Enriching from existing etf_holding_ticker_map...")

    # Get all reviewed mappings with a yahoo_fund_id
    rows = conn.execute("""
        SELECT DISTINCT yahoo_fund_id
        FROM etf_holding_ticker_map
        WHERE yahoo_fund_id IS NOT NULL
        AND yahoo_fund_id != ''
        AND reviewed = 1
    """).fetchall()

    yahoo_ids = [r[0] for r in rows]
    print(f"  {len(yahoo_ids)} unique yahoo_fund_ids to process")

    # Check which ones are already in stock_identifier_map
    already = set(r[0] for r in conn.execute(
        "SELECT yahoo_id FROM stock_identifier_map WHERE yahoo_id IS NOT NULL"
    ).fetchall())
    yahoo_ids = [y for y in yahoo_ids if y not in already]
    print(f"  {len(yahoo_ids)} not yet in stock_identifier_map")

    if not yahoo_ids:
        print("  Nothing to do")
        return

    inserted = 0
    failed   = []

    for i in range(0, len(yahoo_ids), BATCH_SIZE):
        batch_ids = yahoo_ids[i:i + BATCH_SIZE]

        # Build OpenFIGI jobs from yahoo_id
        # Strip YF: prefix and .L/.KS etc suffix to get base ticker + exchange
        jobs = []
        for yid in batch_ids:
            raw = yid.replace('YF:', '')
            # Determine exchange from suffix
            job = {'idType': 'TICKER', 'idValue': raw, 'marketSecDes': 'Equity'}

            # Add exchange hint from suffix
            suffix_map = {
                '.L': 'LN', '.DE': 'GY', '.PA': 'FP', '.MI': 'IM',
                '.AS': 'NA', '.MC': 'SM', '.ST': 'SS', '.HE': 'FH',
                '.KS': 'KS', '.KQ': 'KQ', '.T': 'JT', '.TW': 'TT',
                '.HK': 'HK', '.AX': 'AU', '.SI': 'SP', '.JO': 'SJ',
                '.TO': 'CN', '.V': 'CV', '.TA': 'IT', '.NS': 'IN',
                '.BO': 'IB', '.KL': 'MK',
            }
            for suffix, exch in suffix_map.items():
                if raw.endswith(suffix):
                    job['idValue'] = raw[:-len(suffix)]
                    job['exchCode'] = exch
                    break

            jobs.append((yid, job))

        figi_jobs  = [j for _, j in jobs]
        results    = openfigi_lookup(figi_jobs)

        for (yid, _), result in zip(jobs, results):
            if result:
                figi         = result.get('figi')
                name         = result.get('name')
                base_ticker  = result.get('ticker')
                exch_code    = result.get('exchCode')
                sec_type     = result.get('securityType2')

                if figi:
                    insert_or_update(conn, figi, name, base_ticker, exch_code,
                                     security_type=sec_type)
                    # Also update yahoo_id to match our known value
                    conn.execute("""
                        UPDATE stock_identifier_map SET yahoo_id = ?
                        WHERE figi = ?
                    """, (yid, figi))
                    inserted += 1
            else:
                failed.append(yid)

        conn.commit()
        print(f"  Batch {i//BATCH_SIZE + 1}: {min(i+BATCH_SIZE, len(yahoo_ids))}/{len(yahoo_ids)} processed")
        time.sleep(RATE_SLEEP)

    print(f"\n  Inserted/updated: {inserted}")
    if failed:
        print(f"  Failed ({len(failed)}): {failed[:10]}{'...' if len(failed) > 10 else ''}")


def enrich_from_holdings_sedol(conn):
    """
    Find holdings with SEDOL-format tickers (7-char alphanumeric)
    and look them up via OpenFIGI ID_SEDOL.
    """
    print("\nStep 2: Enriching from SEDOL tickers in etf_holdings...")

    # SEDOL: 7 chars, alphanumeric — already in stock_identifier_map via sedol column?
    sedol_rows = conn.execute("""
        SELECT DISTINCT h.ticker, h.name
        FROM etf_holdings h
        WHERE LENGTH(h.ticker) = 7
        AND h.ticker GLOB '[A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9]'
        AND NOT EXISTS (
            SELECT 1 FROM stock_identifier_map
            WHERE sedol = h.ticker
        )
    """).fetchall()

    print(f"  {len(sedol_rows)} unmatched SEDOL tickers found")
    if not sedol_rows:
        return

    inserted = 0
    for i in range(0, len(sedol_rows), BATCH_SIZE):
        batch = sedol_rows[i:i + BATCH_SIZE]
        jobs  = [{'idType': 'ID_SEDOL', 'idValue': r[0]} for r in batch]
        results = openfigi_lookup(jobs)

        for (sedol, name), result in zip(batch, results):
            if result:
                figi        = result.get('figi')
                fname       = result.get('name')
                base_ticker = result.get('ticker')
                exch_code   = result.get('exchCode')
                sec_type    = result.get('securityType2')

                if figi:
                    insert_or_update(conn, figi, fname or name, base_ticker,
                                     exch_code, sedol=sedol, security_type=sec_type)
                    inserted += 1

        conn.commit()
        print(f"  Batch {i//BATCH_SIZE + 1}: {min(i+BATCH_SIZE, len(sedol_rows))}/{len(sedol_rows)} processed")
        time.sleep(RATE_SLEEP)

    print(f"  Inserted/updated: {inserted}")


def enrich_from_holdings_isin(conn):
    """
    Find holdings with ISIN-format tickers (12-char: 2 letters + 10 alphanumeric)
    and look them up via OpenFIGI ID_ISIN.
    """
    print("\nStep 3: Enriching from ISIN tickers in etf_holdings...")

    isin_rows = conn.execute("""
        SELECT DISTINCT h.ticker, h.name
        FROM etf_holdings h
        WHERE LENGTH(h.ticker) = 12
        AND h.ticker GLOB '[A-Z][A-Z][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9][A-Z0-9]'
        AND NOT EXISTS (
            SELECT 1 FROM stock_identifier_map
            WHERE isin = h.ticker
        )
    """).fetchall()

    print(f"  {len(isin_rows)} unmatched ISIN tickers found")
    if not isin_rows:
        return

    inserted = 0
    for i in range(0, len(isin_rows), BATCH_SIZE):
        batch   = isin_rows[i:i + BATCH_SIZE]
        jobs    = [{'idType': 'ID_ISIN', 'idValue': r[0], 'marketSecDes': 'Equity'} for r in batch]
        results = openfigi_lookup(jobs)

        for (isin, name), result in zip(batch, results):
            if result:
                figi        = result.get('figi')
                fname       = result.get('name')
                base_ticker = result.get('ticker')
                exch_code   = result.get('exchCode')
                sec_type    = result.get('securityType2')

                if figi:
                    insert_or_update(conn, figi, fname or name, base_ticker,
                                     exch_code, isin=isin, security_type=sec_type)
                    inserted += 1

        conn.commit()
        print(f"  Batch {i//BATCH_SIZE + 1}: {min(i+BATCH_SIZE, len(isin_rows))}/{len(isin_rows)} processed")
        time.sleep(RATE_SLEEP)

    print(f"  Inserted/updated: {inserted}")


def print_summary(conn):
    total    = conn.execute("SELECT COUNT(*) FROM stock_identifier_map").fetchone()[0]
    w_yahoo  = conn.execute("SELECT COUNT(*) FROM stock_identifier_map WHERE yahoo_id IS NOT NULL").fetchone()[0]
    w_sedol  = conn.execute("SELECT COUNT(*) FROM stock_identifier_map WHERE sedol IS NOT NULL").fetchone()[0]
    w_isin   = conn.execute("SELECT COUNT(*) FROM stock_identifier_map WHERE isin IS NOT NULL").fetchone()[0]
    reviewed = conn.execute("SELECT COUNT(*) FROM stock_identifier_map WHERE reviewed=1").fetchone()[0]

    print(f"\n=== stock_identifier_map Summary ===")
    print(f"  Total records:     {total:,}")
    print(f"  With Yahoo ID:     {w_yahoo:,}")
    print(f"  With SEDOL:        {w_sedol:,}")
    print(f"  With ISIN:         {w_isin:,}")
    print(f"  Reviewed:          {reviewed:,}")


def main():
    parser = argparse.ArgumentParser(description='Build stock_identifier_map from OpenFIGI')
    parser.add_argument('--rebuild',  action='store_true', help='Drop and rebuild table from scratch')
    parser.add_argument('--new-only', action='store_true', help='Only process holdings not yet mapped')
    args = parser.parse_args()

    conn = get_conn()

    if args.rebuild:
        print("Dropping stock_identifier_map...")
        conn.execute("DROP TABLE IF EXISTS stock_identifier_map")
        conn.commit()

    create_table(conn)

    enrich_from_holdings_sedol(conn)
    enrich_from_holdings_isin(conn)

    print_summary(conn)
    conn.close()


if __name__ == '__main__':
    main()