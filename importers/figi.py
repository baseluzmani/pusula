"""
OpenFIGI resolution: ETF holdings tickers -> stock_identifier_map.

The exchange-code tables live here rather than in the callers because they
were duplicated between import_etf_holdings.py and build_stock_map.py with
different contents - one had NO, TI, IJ, CS and CH, the other did not - so
which script last ran changed how a holding resolved.

Resolution is best-effort by design. A ticker as it appears in a holdings
file is often ambiguous: '2330' means nothing without knowing it is Taiwanese,
and '028260 KS' carries its exchange in a Bloomberg suffix. normalise_ticker
therefore produces up to three candidate lookups, ordered most to least
reliable, and the first hit wins. Anything still unmatched is written as an
UNRESOLVED placeholder rather than dropped, so it shows up in the Ticker Map
tab for a human decision instead of silently disappearing.
"""

from __future__ import annotations

import re
import time

import requests

from core import config

# Ticker suffix -> OpenFIGI exchange code.
SUFFIX_MAP = {
    '.L': 'LN', '.DE': 'GY', '.PA': 'FP', '.MI': 'IM', '.AS': 'NA',
    '.MC': 'SM', '.ST': 'SS', '.HE': 'FH', '.KS': 'KS', '.KQ': 'KQ',
    '.T': 'JT', '.TW': 'TT', '.HK': 'HK', '.AX': 'AU', '.OL': 'NO',
    '.IS': 'TI', '.E': 'TI', '.JK': 'IJ', '.SZ': 'CS', '.SS': 'CH',
}

# OpenFIGI exchange code -> Yahoo suffix.
EXCH_TO_YAHOO = {
    'US': '', 'UQ': '', 'UN': '', 'UP': '', 'UA': '', 'UR': '',
    'LN': '.L', 'LX': '.L', 'GY': '.DE', 'GF': '.F', 'GS': '.SG',
    'FP': '.PA', 'IM': '.MI', 'NA': '.AS', 'SM': '.MC', 'SS': '.ST',
    'FH': '.HE', 'KS': '.KS', 'KQ': '.KQ', 'JT': '.T', 'TT': '.TW',
    'HK': '.HK', 'AU': '.AX', 'NO': '.OL', 'TI': '.IS', 'IJ': '.JK',
    'CS': '.SZ', 'CH': '.SS', 'DC': '.CO', 'BB': '.BR', 'PW': '.WA',
    'CN': '.TO', 'CV': '.V', 'IT': '.TA', 'IN': '.NS', 'IB': '.BO',
    'MK': '.KL', 'TB': '.BK', 'SP': '.SI', 'SJ': '.JO',
}

# Exchanges preferred when OpenFIGI returns several listings of one company.
PRIMARY = {'US', 'UQ', 'UN', 'UP', 'UA', 'LN', 'GY', 'FP', 'IM', 'NA',
           'SM', 'SS', 'FH', 'KS', 'KQ', 'JT', 'TT', 'HK', 'AU', 'NO',
           'TI', 'IJ', 'CS', 'CH', 'DC', 'BB', 'PW', 'CN', 'IT', 'IN',
           'IB', 'MK'}

# iShares Location column -> exchange code. This is the most reliable signal
# for numeric tickers, which carry no suffix at all.
LOCATION_TO_EXCH = {
    'taiwan': 'TT', 'japan': 'JT', 'china': 'HK', 'hong kong': 'HK',
    'south korea': 'KS', 'korea': 'KS', 'germany': 'GY', 'france': 'FP',
    'italy': 'IM', 'netherlands': 'NA', 'spain': 'SM', 'sweden': 'SS',
    'finland': 'FH', 'norway': 'NO', 'turkey': 'TI', 'indonesia': 'IJ',
    'australia': 'AU', 'singapore': 'SP', 'israel': 'IT', 'india': 'IN',
    'malaysia': 'MK', 'thailand': 'TB', 'denmark': 'DC', 'belgium': 'BB',
    'poland': 'PW', 'canada': 'CN', 'switzerland': 'SW', 'brazil': 'BS',
    'mexico': 'MM', 'south africa': 'SJ', 'united kingdom': 'LN',
    'united states': 'US',
}

# Bloomberg exchange code -> OpenFIGI exchange code, for tickers written as
# 'ASELS TI' by WisdomTree and VanEck.
BLOOMBERG_TO_OPENFIGI = {
    'US': 'US', 'UQ': 'UQ', 'UN': 'UN', 'UP': 'UP', 'UA': 'UA',
    'LN': 'LN', 'GY': 'GY', 'GR': 'GY', 'GF': 'GF', 'GS': 'GS',
    'FP': 'FP', 'IM': 'IM', 'NA': 'NA', 'SM': 'SM', 'SS': 'SS',
    'FH': 'FH', 'KS': 'KS', 'KQ': 'KQ', 'JT': 'JT', 'TT': 'TT',
    'HK': 'HK', 'AU': 'AU', 'NO': 'NO', 'TI': 'TI', 'IJ': 'IJ',
    'CS': 'CS', 'CH': 'CH', 'DC': 'DC', 'BB': 'BB', 'PW': 'PW',
    'CN': 'CN', 'CV': 'CV', 'IT': 'IT', 'IN': 'IN', 'IB': 'IB',
    'MK': 'MK', 'TB': 'TB', 'SP': 'SP', 'SJ': 'SJ',
    'MT': 'NA', 'SW': 'SW', 'VX': 'SW', 'SE': 'SS', 'SF': 'FH',
    'LI': 'LN', 'E': 'TI', 'EI': 'ID', 'ID': 'ID', 'PL': 'PW',
    'BS': 'BS', 'MM': 'MM', 'CI': 'CI', 'NZ': 'NZ', 'JP': 'JT',
}


def normalise_ticker(raw, location=None) -> list:
    """Up to three OpenFIGI lookups for one holding, best guess first.

    Order matters: location beats a suffix, because a suffix can be absent or
    wrong, and both beat the bare-ticker-on-US fallback.
    """
    t = str(raw).strip().upper()
    base = t.split()[0]
    attempts, seen = [], set()

    def add(ticker_val, exch):
        if exch and exch not in seen:
            attempts.append({'idType': 'TICKER', 'idValue': ticker_val,
                             'exchCode': exch, 'marketSecDes': 'Equity'})
            seen.add(exch)

    exch_from_suffix, clean_base = None, base
    for suffix, code in SUFFIX_MAP.items():
        if clean_base.endswith(suffix.upper()):
            clean_base = clean_base[:-len(suffix)]
            exch_from_suffix = code
            break

    exch_from_bloomberg = None
    if ' ' in t:
        parts = t.split()
        if len(parts) == 2:
            candidate = BLOOMBERG_TO_OPENFIGI.get(parts[1])
            if candidate:
                clean_base, exch_from_bloomberg = parts[0], candidate
            elif parts[1] in PRIMARY:
                clean_base, exch_from_bloomberg = parts[0], parts[1]

    # OpenFIGI wants slashes where providers use dots and hyphens.
    clean_base = re.sub(r'-([A-Z])$', r'/\1', clean_base)   # MOG-A -> MOG/A
    clean_base = re.sub(r'-', '/', clean_base)
    if '.' in clean_base and not clean_base.endswith('/'):
        clean_base = clean_base.replace('.', '/')           # BA. -> BA/

    if location:
        loc_exch = LOCATION_TO_EXCH.get(location.lower().strip())
        if loc_exch:
            add(clean_base, loc_exch)

    if exch_from_bloomberg:
        add(clean_base, exch_from_bloomberg)
    elif exch_from_suffix:
        add(clean_base, exch_from_suffix)

    add(clean_base, 'US')
    return attempts[:3]


def pick_best(data):
    """Best equity match, preferring a primary listing."""
    equities = [d for d in data
                if d.get('marketSector') == 'Equity'
                and d.get('securityType2') in ('Common Stock', 'ETP', 'ETF',
                                               'Depositary Receipt')]
    primary = [e for e in equities if e.get('exchCode') in PRIMARY]
    return primary[0] if primary else (equities[0] if equities else None)


def yahoo_id_for(hit):
    ticker = hit.get('ticker')
    if not ticker:
        return None
    return f"YF:{ticker}{EXCH_TO_YAHOO.get(hit.get('exchCode'), '')}"


def _headers():
    return {'Content-Type': 'application/json',
            'X-OPENFIGI-APIKEY': config.OPENFIGI_API_KEY}


def _post(jobs):
    try:
        resp = requests.post(config.FIGI_URL, headers=_headers(), json=jobs,
                             timeout=30)
        return resp.json()
    except Exception as exc:                                   # noqa: BLE001
        print(f"  OpenFIGI error: {exc}")
        return [None] * len(jobs)


def _insert_hit(conn, hit, original_ticker, original_name):
    figi = hit['figi']
    base_ticker = hit.get('ticker', '')
    exch_code = hit.get('exchCode', '')
    conn.execute("""
        INSERT INTO stock_identifier_map
            (figi, name, base_ticker, exch_code, bloomberg_code,
             raw_ticker, yahoo_id, security_type, group_figi, reviewed)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(figi) DO UPDATE SET
            bloomberg_code = COALESCE(excluded.bloomberg_code, bloomberg_code),
            raw_ticker     = COALESCE(excluded.raw_ticker,     raw_ticker),
            yahoo_id       = COALESCE(excluded.yahoo_id,       yahoo_id),
            group_figi     = COALESCE(group_figi, excluded.group_figi)
    """, (figi, hit.get('name', original_name), base_ticker, exch_code,
          f"{base_ticker} {exch_code}".strip(),
          str(original_ticker).strip().upper(), yahoo_id_for(hit),
          hit.get('securityType2', ''), figi))


def resolve(conn) -> dict:
    """Resolve every unmatched holding, by ticker then by ISIN.

    Returns counts rather than printing them, so the caller can put them in a
    result the UI can show.
    """
    batch = config.FIGI_BATCH_SIZE
    sleep = config.FIGI_RATE_SLEEP

    rows = conn.execute("""
        SELECT DISTINCT h.ticker, h.name, h.location
        FROM etf_holdings h
        WHERE h.ticker IS NOT NULL AND h.ticker != ''
    """).fetchall()

    unresolved = []
    for ticker, name, location in rows:
        t = str(ticker).strip().upper()
        tb = t.split()[0]
        exists = conn.execute("""
            SELECT 1 FROM stock_identifier_map
            WHERE figi NOT LIKE 'UNRESOLVED:%'
              AND (bloomberg_code IN (?, ?) OR base_ticker IN (?, ?)
                   OR raw_ticker IN (?, ?) OR sedol = ? OR isin = ?)
            LIMIT 1
        """, (t, tb, t, tb, t, tb, t, t)).fetchone()
        if not exists:
            unresolved.append((ticker, name, location))

    resolved_count, failed = 0, []

    if unresolved:
        job_list, job_meta = [], []
        for ticker, name, location in unresolved:
            for idx, job in enumerate(normalise_ticker(ticker, location)):
                job_list.append(job)
                job_meta.append((ticker, name, location, idx))

        done = set()
        for i in range(0, len(job_list), batch):
            results = _post(job_list[i:i + batch])
            for (ticker, name, _loc, _idx), result in zip(
                    job_meta[i:i + batch], results):
                if ticker in done:
                    continue
                hit = pick_best(result['data']) \
                    if isinstance(result, dict) and result.get('data') else None
                if hit and hit.get('figi'):
                    try:
                        _insert_hit(conn, hit, ticker, name)
                        done.add(ticker)
                        resolved_count += 1
                    except Exception as exc:                   # noqa: BLE001
                        print(f"  Insert error {ticker}: {exc}")
            conn.commit()
            time.sleep(sleep)

        for ticker, name, _loc in unresolved:
            if ticker not in done:
                failed.append(ticker)
                raw = str(ticker).strip().upper()
                conn.execute("""
                    INSERT OR IGNORE INTO stock_identifier_map
                        (figi, name, bloomberg_code, raw_ticker, group_figi,
                         reviewed)
                    VALUES (?, ?, ?, ?, ?, 0)
                """, (f"UNRESOLVED:{ticker}|{name}", name, raw, raw,
                      f"UNRESOLVED:{ticker}|{name}"))
        conn.commit()

    # Providers like Xtrackers give an ISIN and no ticker at all.
    isin_rows = conn.execute("""
        SELECT DISTINCT h.isin, h.name FROM etf_holdings h
        WHERE (h.ticker IS NULL OR h.ticker = '')
          AND h.isin IS NOT NULL AND h.isin != ''
    """).fetchall()

    isin_unresolved = []
    for isin_val, name in isin_rows:
        iv = str(isin_val).strip().upper()
        exists = conn.execute("""
            SELECT 1 FROM stock_identifier_map
            WHERE figi NOT LIKE 'UNRESOLVED:%' AND isin = ? LIMIT 1
        """, (iv,)).fetchone()
        if not exists:
            isin_unresolved.append((iv, name))

    isin_resolved, isin_failed = 0, []
    if isin_unresolved:
        jobs = [{'idType': 'ID_ISIN', 'idValue': iv, 'marketSecDes': 'Equity'}
                for iv, _ in isin_unresolved]
        for i in range(0, len(jobs), batch):
            results = _post(jobs[i:i + batch])
            for (isin_val, name), result in zip(isin_unresolved[i:i + batch],
                                                results):
                hit = pick_best(result['data']) \
                    if isinstance(result, dict) and result.get('data') else None
                if hit and hit.get('figi'):
                    try:
                        figi = hit['figi']
                        conn.execute("""
                            INSERT INTO stock_identifier_map
                                (figi, name, base_ticker, exch_code,
                                 bloomberg_code, isin, yahoo_id,
                                 security_type, group_figi, reviewed)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                            ON CONFLICT(figi) DO UPDATE SET
                                isin       = COALESCE(excluded.isin, isin),
                                yahoo_id   = COALESCE(excluded.yahoo_id,
                                                      yahoo_id),
                                group_figi = COALESCE(group_figi,
                                                      excluded.group_figi)
                        """, (figi, hit.get('name') or name, hit.get('ticker'),
                              hit.get('exchCode'),
                              f"{hit.get('ticker','')} "
                              f"{hit.get('exchCode','')}".strip(),
                              isin_val, yahoo_id_for(hit),
                              hit.get('securityType')
                              or hit.get('marketSector'), figi))
                        isin_resolved += 1
                    except Exception as exc:                   # noqa: BLE001
                        print(f"  Insert error {isin_val}: {exc}")
                else:
                    isin_failed.append((isin_val, name))
            conn.commit()
            time.sleep(sleep)

        for isin_val, name in isin_failed:
            placeholder = f"UNRESOLVED:{isin_val}|{name}"
            conn.execute("""
                INSERT OR IGNORE INTO stock_identifier_map
                    (figi, name, isin, group_figi, reviewed)
                VALUES (?, ?, ?, ?, 0)
            """, (placeholder, name, isin_val, placeholder))
        conn.commit()

    return {"resolved": resolved_count, "unresolved": len(failed),
            "isin_resolved": isin_resolved,
            "isin_unresolved": len(isin_failed),
            "unresolved_sample": failed[:10]}
