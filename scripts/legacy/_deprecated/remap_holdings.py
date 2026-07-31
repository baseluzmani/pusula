"""
Re-runs ticker mapping with Yahoo Finance validation for unmatched rows.

Usage:
    python3 scripts/remap_holdings.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import yfinance as yf
import time

DB_PATH = 'data/funds.db'

EXCHANGE_SUFFIXES = ['.SZ', '.SS', '.L', '.IS', '.HK', '.T', '.PA', '.DE', '.MI', '.ST', '.AS', '.BR', '.SI', '.KS', '.KQ']

# Exchange suffix candidates to try when validating on Yahoo
# Order matters — try the most likely first
YAHOO_EXCHANGE_CANDIDATES = {
    'SZ': ['{t}.SZ', '{t}.SS'],          # Shenzhen → try SZ then SS
    'SS': ['{t}.SS', '{t}.SZ'],          # Shanghai
    'HK': ['{t}.HK'],
    'L':  ['{t}.L'],
    'IS': ['{t}.IS'],
    'PA': ['{t}.PA'],
    'DE': ['{t}.DE'],
    'MI': ['{t}.MI'],
    'ST': ['{t}.ST'],
    'AS': ['{t}.AS'],
    'KS': ['{t}.KS'],    # Korea Stock Exchange
    'KQ': ['{t}.KQ'],    # KOSDAQ
    'TW': ['{t}.TW'],    # Taiwan
    'T':  ['{t}.T'],     # Tokyo
}

# Bloomberg exchange code → Yahoo suffix candidates
BLOOMBERG_TO_YAHOO = {
    'US': [''],
    'UQ': [''],
    'UN': [''],
    'UW': [''],
    'LN': ['.L'],
    'GY': ['.DE'],
    'FH': ['.HE'],
    'IM': ['.MI'],
    'MT': ['.AS'],
    'NA': ['.AS'],
    'SM': ['.MC'],
    'FP': ['.PA'],
    'SS': ['.ST'],
    'TT': ['.TW'],
    'JT': ['.T'],
    'JP': ['.T'],        # ← add this
    'KS': ['.KS'],
    'KQ': ['.KQ'],
    'HK': ['.HK'],
    'AU': ['.AX'],
    'SJ': ['.JO'],
    'SP': ['.SI'],       # Singapore
    'IT': ['.TA'],       # Israel Tel Aviv
    'AX': ['.AX'],       # Australia (alt code)
}

def normalise_ticker(ticker):
    if not ticker:
        return '', None
    parts = ticker.upper().strip().split()
    t = parts[0]
    raw_suffix = parts[1] if len(parts) > 1 else None

    # Strip dot-separated exchange suffix if present
    for suffix in EXCHANGE_SUFFIXES:
        if t.endswith(suffix):
            return t[:-len(suffix)], suffix.lstrip('.')
    
    # raw_suffix is Bloomberg code (e.g. 'UQ', 'GY', 'TT')
    return t, raw_suffix

def names_match(source_name, yahoo_name, threshold=0.4):
    if not source_name or not yahoo_name:
        return False
    # If yahoo_name looks like a ticker (no spaces, short), skip name check
    if ' ' not in yahoo_name.strip():
        return True
    s_words = set(w for w in source_name.lower().split() if len(w) > 3)
    y_words = set(w for w in yahoo_name.lower().split() if len(w) > 3)
    if not s_words or not y_words:
        return True
    return len(s_words & y_words) >= 1

def yahoo_candidates(clean_ticker, raw_suffix):
    candidates = []

    # Bloomberg exchange code (e.g. "INTC UQ" → suffix="UQ")
    if raw_suffix and raw_suffix in BLOOMBERG_TO_YAHOO:
        for suffix in BLOOMBERG_TO_YAHOO[raw_suffix]:
            candidates.append(f"{clean_ticker}{suffix}")

    # Dot-separated exchange suffix (e.g. "000333.SZ")
    elif raw_suffix and raw_suffix in YAHOO_EXCHANGE_CANDIDATES:
        for pattern in YAHOO_EXCHANGE_CANDIDATES[raw_suffix]:
            candidates.append(pattern.replace('{t}', clean_ticker))

    # Bare numeric — infer exchange from digit count and leading digit
    if not candidates and clean_ticker.replace('A','').replace('B','').isdigit():
        n = len(clean_ticker)
        if n == 6:
            if clean_ticker.startswith(('0', '3')):
                candidates = [f"{clean_ticker}.SZ", f"{clean_ticker}.KS", f"{clean_ticker}.SS"]
            elif clean_ticker.startswith('6'):
                candidates = [f"{clean_ticker}.SS", f"{clean_ticker}.SZ"]
            else:
                candidates = [f"{clean_ticker}.KS", f"{clean_ticker}.KQ",
                              f"{clean_ticker}.SZ", f"{clean_ticker}.SS"]
        elif n == 4:
            if clean_ticker.startswith(('4', '5', '6', '7', '8', '9')):
                candidates = [f"{clean_ticker}.T", f"{clean_ticker}.TW", f"{clean_ticker}.HK"]
            else:
                candidates = [f"{clean_ticker}.TW", f"{clean_ticker}.HK", f"{clean_ticker}.T"]
        elif n == 3:
            candidates = [f"{clean_ticker}.HK", f"{clean_ticker}.T"]
            
    # Always try bare ticker as last resort
    if clean_ticker not in candidates:
        candidates.append(clean_ticker)

    return candidates


def validate_yahoo(ticker_str):
    try:
        tkr   = yf.Ticker(ticker_str)
        fast  = tkr.fast_info
        price = fast.get('lastPrice') or fast.get('regularMarketPrice')
        if price and float(price) > 0:
            info = tkr.info
            name = info.get('shortName') or info.get('longName') or ticker_str
            return f"YF:{ticker_str}", name
    except Exception:
        pass
    return None, None


def generate_mapping_suggestions(conn):
    print("Loading instruments from DB...")
    instruments = conn.execute(
        "SELECT fund_id, name FROM instruments WHERE fund_id LIKE 'YF:%'"
    ).fetchall()

    inst_by_name   = {}
    inst_by_ticker = {}
    for fid, iname in instruments:
        if iname:
            inst_by_name[iname.lower().strip()] = fid
        raw = fid.replace('YF:', '')
        for suffix in EXCHANGE_SUFFIXES:
            if raw.upper().endswith(suffix.lstrip('.')):
                raw = raw[:-(len(suffix)-1)]
                break
        inst_by_ticker[raw.upper()] = fid

    unmapped = conn.execute("""
        SELECT DISTINCT source_ticker, source_name
        FROM etf_holding_ticker_map
        WHERE reviewed = 0
    """).fetchall()

    print(f"  {len(unmapped)} unreviewed rows to process\n")

    updated_db   = 0
    updated_yf   = 0
    still_missing = 0

    for ticker, name in unmapped:
        yahoo_fund_id = None
        confidence    = 0.0
        clean, suffix = normalise_ticker(ticker)

        # ── Pass 1: instruments table (fast, no network) ──────────────────
        if clean in inst_by_ticker:
            yahoo_fund_id = inst_by_ticker[clean]
            confidence    = 0.95

        if not yahoo_fund_id:
            name_lower = name.lower().strip()
            if name_lower in inst_by_name:
                yahoo_fund_id = inst_by_name[name_lower]
                confidence    = 0.90

        if not yahoo_fund_id:
            name_lower = name.lower().strip()
            for inst_name, fid in inst_by_name.items():
                if len(inst_name) > 5 and inst_name in name_lower:
                    yahoo_fund_id = fid
                    confidence    = 0.60
                    break
                elif len(name_lower) > 5 and name_lower[:10] in inst_name:
                    yahoo_fund_id = fid
                    confidence    = 0.40
                    break

        # ── Pass 2: Yahoo Finance validation (network, only if still unmatched) ─
        if not yahoo_fund_id and clean:
            candidates = yahoo_candidates(clean, suffix)
            for candidate in candidates:
                yf_id, yf_name = validate_yahoo(candidate)
                if yf_id:
                    # For bare numeric tickers, validate name before accepting
                    if clean.isdigit() and suffix not in BLOOMBERG_TO_YAHOO and not names_match(name, yf_name):
                        print(f"  NAME MISMATCH: {candidate} → {yf_name} (source: {name})")
                        continue
                    yahoo_fund_id = yf_id
                    confidence    = 0.85
                    print(f"  YF match: {ticker:20s} → {yf_id} ({yf_name})")
                    time.sleep(0.3)
                    break

        # ── Write result ──────────────────────────────────────────────────
        if yahoo_fund_id:
            conn.execute("""
                UPDATE etf_holding_ticker_map
                SET yahoo_fund_id = ?, confidence = ?
                WHERE source_ticker = ? AND source_name = ? AND reviewed = 0
            """, (yahoo_fund_id, confidence, ticker, name))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                if confidence == 0.85:
                    updated_yf += 1
                else:
                    updated_db += 1
        else:
            still_missing += 1
            print(f"  NO MATCH:  {ticker:20s}  {name[:50]}")

    conn.commit()

    print(f"\n  Updated via DB instruments: {updated_db}")
    print(f"  Updated via Yahoo Finance:  {updated_yf}")
    print(f"  Still unmatched:            {still_missing}")

    stats = conn.execute("""
        SELECT
            COUNT(*)                                                    as total,
            SUM(CASE WHEN yahoo_fund_id IS NOT NULL THEN 1 ELSE 0 END) as matched,
            SUM(CASE WHEN yahoo_fund_id IS NULL     THEN 1 ELSE 0 END) as unmatched,
            SUM(CASE WHEN reviewed = 1              THEN 1 ELSE 0 END) as reviewed
        FROM etf_holding_ticker_map
    """).fetchone()
    print(f"\n=== DB Summary ===")
    print(f"  Total:     {stats[0]}")
    print(f"  Matched:   {stats[1]}")
    print(f"  Unmatched: {stats[2]}")
    print(f"  Reviewed:  {stats[3]}")


if __name__ == '__main__':
    conn = sqlite3.connect(DB_PATH, timeout=30)
    generate_mapping_suggestions(conn)
    conn.close()