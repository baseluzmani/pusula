"""
ETF holdings importer.

Reads provider holdings files - CSV, XLS or XLSX - and stores them in
etf_holdings, then resolves any new tickers to stock_identifier_map.

Files are matched to an ETF by their filename prefix: AINF_20260610.csv maps
through instruments.source_id to a fund_id, and instruments.provider chooses
the parser. The as-of date is always read from inside the file, never from the
name, because a file is often downloaded days after the date it describes.

This used to be a script under FTScrapper, run as a subprocess with its output
captured as text, which meant the Data page could report only whether the
process exited cleanly. It now runs in process and returns counts, so the
upload screen can show what happened to each file.

The historical Excel path is gone. It imported from a spreadsheet with sheets
like 'Ahmet Dashboard' and referenced NATP, which no longer exists; the data
it loaded has been in etf_holdings for months.

Usage
-----
    python3 -m importers.etf_holdings          # process the inbox folder
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from core import config, db
from core.repo import tickers as ticker_repo
from importers import etf_parsers as parsers
from importers import figi


def create_tables(conn):
    """Once per run, not once per file. isin and sedol are added here rather
    than mid-insert, which is where the ALTER used to live - retried and
    swallowed for every fund and date in the batch."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS etf_holdings (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            etf_fund_id     TEXT NOT NULL,
            scraped_date    TEXT NOT NULL,
            ticker          TEXT,
            name            TEXT NOT NULL,
            sector          TEXT,
            asset_class     TEXT,
            weight_pct      REAL,
            market_value    REAL,
            location        TEXT,
            currency        TEXT,
            UNIQUE(etf_fund_id, scraped_date, name)
        );
        CREATE INDEX IF NOT EXISTS idx_etf_holdings_fund_date
            ON etf_holdings(etf_fund_id, scraped_date);
        CREATE INDEX IF NOT EXISTS idx_etf_holdings_ticker
            ON etf_holdings(ticker);
    """)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(etf_holdings)")}
    for col in ("isin", "sedol"):
        if col not in existing:
            conn.execute(f"ALTER TABLE etf_holdings ADD COLUMN {col} TEXT")
    conn.commit()


def already_imported(conn, etf_fund_id, scraped_date) -> bool:
    return conn.execute("""
        SELECT 1 FROM etf_holdings
        WHERE etf_fund_id = ? AND scraped_date = ? LIMIT 1
    """, (etf_fund_id, scraped_date)).fetchone() is not None


def insert_holdings(conn, etf_fund_id, scraped_date, holdings):
    inserted, errors = 0, 0
    for h in holdings:
        ticker = str(h.get('ticker', '') or '').strip().upper()
        if ticker in parsers.SKIP_TICKERS:
            continue
        asset_class = str(h.get('asset_class', '') or '').lower().strip()
        if asset_class in parsers.SKIP_ASSET_CLASSES:
            continue
        try:
            conn.execute("""
                INSERT OR REPLACE INTO etf_holdings
                    (etf_fund_id, scraped_date, ticker, name, sector,
                     asset_class, weight_pct, market_value, location,
                     currency, isin, sedol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (etf_fund_id, scraped_date, h.get('ticker'), h['name'],
                  h.get('sector'), h.get('asset_class'), h.get('weight_pct'),
                  h.get('market_value'), h.get('location'), h.get('currency'),
                  h.get('isin'), h.get('sedol')))
            inserted += 1
        except Exception as exc:                               # noqa: BLE001
            errors += 1
            print(f"    row error ({h.get('name', '?')}): {exc}")
    conn.commit()
    return inserted, errors


def import_file(conn, filepath: Path, fund_id_map=None,
                provider_map=None) -> dict:
    """
    Import one file. Returns a result dict rather than raising, so a bad file
    in a batch reports itself and the rest still run.

    status is one of: imported, skipped, error.
    """
    # Maps are read per call, not at module import: an ETF added in the
    # Instruments tab must be usable without restarting the app.
    if fund_id_map is None:
        fund_id_map = ticker_repo.fund_id_map()
    if provider_map is None:
        provider_map = ticker_repo.etf_provider_map()

    name = filepath.name
    prefix = filepath.stem.split('_')[0].upper()
    etf_fund_id = fund_id_map.get(prefix)

    if not etf_fund_id:
        return {"file": name, "status": "error", "rows": 0,
                "message": f"Unknown prefix '{prefix}'. Add an instrument "
                           f"with this source_id and a provider under "
                           f"Data → Instruments."}

    provider = provider_map.get(prefix, 'ishares')
    parser = parsers.parser_for(provider, filepath.suffix)
    if parser is None:
        return {"file": name, "status": "error", "rows": 0,
                "message": f"Unsupported file type {filepath.suffix}"}

    try:
        holdings, scraped_date = parser(str(filepath))
    except Exception as exc:                                   # noqa: BLE001
        return {"file": name, "status": "error", "rows": 0,
                "message": f"Parse failed ({provider}): {exc}"}

    if not holdings:
        return {"file": name, "status": "error", "rows": 0,
                "message": f"No holdings found - is this a {provider} file?"}

    dated_from_file = scraped_date is not None
    if not scraped_date:
        scraped_date = datetime.now().strftime('%Y-%m-%d')

    if already_imported(conn, etf_fund_id, scraped_date):
        return {"file": name, "status": "skipped", "rows": 0,
                "fund_id": etf_fund_id, "date": scraped_date,
                "message": f"{etf_fund_id} {scraped_date} already imported"}

    inserted, errors = insert_holdings(conn, etf_fund_id, scraped_date,
                                       holdings)
    message = f"{inserted} rows"
    if errors:
        message += f", {errors} row errors"
    if not dated_from_file:
        message += " - no date in file, used today"
    return {"file": name, "status": "imported", "rows": inserted,
            "fund_id": etf_fund_id, "date": scraped_date, "message": message}


def archive(filepath: Path):
    ARCHIVE = Path(config.ARCHIVE_DIR)
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    filepath.rename(ARCHIVE / filepath.name)


def import_paths(paths, resolve_tickers=True) -> dict:
    """
    Import a list of files, archive the ones that were read, resolve new
    tickers once at the end.

    Shared by the folder run and the upload screen, so both behave the same.
    """
    paths = [Path(p) for p in paths]
    if not paths:
        return {"saved": 0, "files": [], "message": "No files to import"}

    results = []
    with db.get_conn() as conn:
        create_tables(conn)
        fund_id_map = ticker_repo.fund_id_map()
        provider_map = ticker_repo.etf_provider_map()

        for path in paths:
            result = import_file(conn, path, fund_id_map, provider_map)
            results.append(result)
            print(f"  {result['status']:<8} {result['file']}: "
                  f"{result['message']}")
            # An unreadable file stays put: archiving it would hide the
            # problem and lose the only copy.
            if result["status"] in ("imported", "skipped"):
                try:
                    archive(path)
                except OSError as exc:
                    result["message"] += f" (archive failed: {exc})"

        figi_result = {}
        if resolve_tickers and any(r["status"] == "imported" for r in results):
            print("\nResolving new tickers...")
            figi_result = figi.resolve(conn)
            print(f"  resolved {figi_result.get('resolved', 0)}, "
                  f"unresolved {figi_result.get('unresolved', 0)}")

    saved = sum(r["rows"] for r in results)
    imported = sum(1 for r in results if r["status"] == "imported")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "error")

    message = f"{saved:,} rows from {imported} file(s)"
    if skipped:
        message += f", {skipped} already imported"
    if failed:
        message += f", {failed} failed"

    return {"saved": saved, "files": results, "figi": figi_result,
            "imported": imported, "skipped": skipped, "failed": failed,
            "message": message}


def run(resolve_tickers: bool = True) -> dict:
    """Import everything sitting in the configured input folder."""
    import_dir = Path(config.IMPORT_DIR)
    import_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(list(import_dir.glob('*.csv'))
                   + list(import_dir.glob('*.xls'))
                   + list(import_dir.glob('*.xlsx')))
    if not files:
        return {"saved": 0, "files": [],
                "message": f"No files in {import_dir}"}

    print(f"ETF holdings importer\n{len(files)} file(s) in {import_dir}\n")
    result = import_paths(files, resolve_tickers=resolve_tickers)
    print(f"\nDone. {result['message']}")
    return result


if __name__ == "__main__":
    run()
    sys.exit(0)
