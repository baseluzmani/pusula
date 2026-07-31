"""
ETF Holdings Ticker Mapper v2
------------------------------
Second pass for remaining unmatched holdings.
Strategies:
1. SEDOL codes → search by company name
2. Numeric codes → try with exchange suffixes (.T, .HK, .KS, .SS etc.)
3. Share class tickers (X B, X A) → try X-B.ST, X-A.{suffix}
4. Name-based yfinance search as fallback

Usage:
    python3 scripts/map_etf_holdings_v2.py
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import time
import logging
import re
import yfinance as yf

# Suppress yfinance noise
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger('peewee').setLevel(logging.CRITICAL)

DB_PATH = 'data/funds.db'

SKIP_NAMES = {
    'gbp/eur','gbp/usd','eur/usd','jpy/usd','cad/usd','chf/usd','aud/usd',
    'hkd/usd','krw/usd','mxn/usd','twd/usd','cnh/usd','pln/usd','idr/usd',
    'sek/eur','sek/usd','nok/eur','try/eur','czk/eur','cash','cash w-o',
    'euro income a/c','japanese yen','us dollar','canadian dollar',
}
SKIP_TICKERS = {
    'gbp','eur','jpy','cad','chf','aud','hkd','krw','mxn','twd',
    'cnh','pln','idr','sek','nok','try','czk','usd','nan',
    '$usd','$cad','$jpy','cash','icseagd','icsuagd','-',
}

# Numeric code → exchange suffix mapping by typical digit count/origin
NUMERIC_EXCHANGES = {
    4: ['.T', '.HK', '.KS', '.TW'],       # Japan 4-digit, HK, Korea, Taiwan
    5: ['.KS', '.KQ'],                     # Korea 5-6 digit
    6: ['.SS', '.SZ', '.T', '.KS'],        # China A-shares, Japan, Korea
    7: ['.HK'],                            # HK 7-digit
}

def is_sedol(ticker):
    """SEDOL: 7 alphanumeric characters."""
    return bool(re.match(r'^[A-Z0-9]{7}$', ticker.strip()))

def is_numeric_code(ticker):
    """Pure numeric ticker like Japanese/Korean/Chinese codes."""
    return bool(re.match(r'^\d{3,7}$', ticker.strip()))

def is_share_class(ticker):
    """Ticker with share class like 'EMBRAC B', 'SAAB B', 'GMEXICOB'."""
    return ' ' in ticker.strip()

def try_ticker(ticker_str):
    """Try a single Yahoo Finance ticker. Returns True if valid."""
    try:
        t = yf.Ticker(ticker_str)
        fi = t.fast_info
        if hasattr(fi, 'last_price') and fi.last_price and fi.last_price > 0:
            return True
        info = t.info
        if info and (info.get('regularMarketPrice') or info.get('currentPrice')):
            return True
    except:
        pass
    return False

def search_by_name(name):
    """Use yfinance Search to find ticker by company name."""
    try:
        results = yf.Search(name, max_results=3)
        quotes = results.quotes
        if quotes:
            # Return best match
            best = quotes[0]
            ticker = best.get('symbol', '')
            if ticker and try_ticker(ticker):
                return f'YF:{ticker}', 0.70
    except:
        pass
    return None, 0.0

def find_ticker_v2(source_ticker, source_name):
    """
    Enhanced ticker finding with multiple strategies.
    Returns (yahoo_fund_id, confidence) or (None, 0).
    """
    ticker = source_ticker.strip()
    ticker_upper = ticker.upper()

    # Strategy 1: Share class tickers (e.g. "EMBRAC B" → "EMBRAC-B.ST")
    if is_share_class(ticker):
        parts = ticker_upper.split()
        base, cls = parts[0], parts[1]
        candidates = [
            f'{base}-{cls}.ST',   # Stockholm (most common for A/B shares)
            f'{base}-{cls}.HE',   # Helsinki
            f'{base}-{cls}.CO',   # Copenhagen
            f'{base}-{cls}.OL',   # Oslo
            f'{base}-{cls}',      # No suffix
            f'{base}{cls}',       # Concatenated
        ]
        for c in candidates:
            time.sleep(0.05)
            if try_ticker(c):
                return f'YF:{c}', 0.85

    # Strategy 2: Numeric codes with exchange suffixes
    if is_numeric_code(ticker):
        # Build variants including zero-padded versions (Excel strips leading zeros)
        numeric_variants = [ticker]
        for pad_len in [4, 5, 6, 7]:
            padded = ticker.zfill(pad_len)
            if padded != ticker:
                numeric_variants.append(padded)

        length = len(ticker)
        suffixes = NUMERIC_EXCHANGES.get(length, ['.T', '.HK', '.KS', '.SS', '.SZ', '.TW'])

        for variant in numeric_variants:
            for suffix in suffixes:
                candidate = variant + suffix
                time.sleep(0.05)
                if try_ticker(candidate):
                    return f'YF:{candidate}', 0.80

    # Strategy 3: SEDOL or other codes → search by name
    if is_sedol(ticker) or ticker.startswith('B') and len(ticker) == 7:
        result, conf = search_by_name(source_name)
        if result:
            return result, conf

    # Strategy 4: Try common variants with exchange suffixes
    base = ticker_upper.split()[0].rstrip('.')
    suffixes = ['', '.L', '.PA', '.DE', '.AS', '.MI', '.ST', '.OL',
                '.HE', '.IS', '.TO', '.AX', '.KS', '.T', '.TW', '.HK',
                '.SS', '.SZ', '.MC', '.BR', '.LS', '.SW', '.VI']
    for suffix in suffixes:
        candidate = base + suffix
        if candidate == ticker_upper:
            continue  # already tried in v1
        time.sleep(0.05)
        if try_ticker(candidate):
            return f'YF:{candidate}', 0.75

    # Strategy 5: Name search as last resort
    result, conf = search_by_name(source_name)
    if result:
        return result, conf

    return None, 0.0


def main():
    conn = sqlite3.connect(DB_PATH)

    rows = conn.execute("""
        SELECT id, source_ticker, source_name
        FROM etf_holding_ticker_map
        WHERE yahoo_fund_id IS NULL AND reviewed = 0
        ORDER BY source_name
    """).fetchall()

    print(f"Found {len(rows)} unmatched holdings\n")

    matched = unmatched = skipped = 0

    for i, (row_id, source_ticker, source_name) in enumerate(rows):
        # Skip currencies/cash
        if source_name.lower() in SKIP_NAMES or source_ticker.lower() in SKIP_TICKERS:
            conn.execute("UPDATE etf_holding_ticker_map SET reviewed=2 WHERE id=?", (row_id,))
            skipped += 1
            continue

        print(f"[{i+1}/{len(rows)}] {source_ticker} | {source_name}", end=' ... ', flush=True)

        yahoo_fund_id, confidence = find_ticker_v2(source_ticker, source_name)

        if yahoo_fund_id:
            conn.execute("""
                UPDATE etf_holding_ticker_map
                SET yahoo_fund_id=?, confidence=?, reviewed=0
                WHERE id=?
            """, (yahoo_fund_id, confidence, row_id))
            print(f"✓ {yahoo_fund_id} ({confidence:.0%})")
            matched += 1
        else:
            print("✗ not found")
            unmatched += 1

        if (i + 1) % 50 == 0:
            conn.commit()
            print(f"\n  Progress: {matched} matched, {unmatched} unmatched, {skipped} skipped\n")

    conn.commit()

    print(f"\n=== Complete ===")
    print(f"  Matched:   {matched}")
    print(f"  Unmatched: {unmatched}")
    print(f"  Skipped:   {skipped}")

    # Final DB summary
    total = conn.execute("SELECT COUNT(*) FROM etf_holding_ticker_map").fetchone()[0]
    total_matched = conn.execute("SELECT COUNT(*) FROM etf_holding_ticker_map WHERE yahoo_fund_id IS NOT NULL").fetchone()[0]
    print(f"\n  Total in map: {total}")
    print(f"  Total matched: {total_matched} ({total_matched/total*100:.0f}%)")

    conn.close()


if __name__ == '__main__':
    main()