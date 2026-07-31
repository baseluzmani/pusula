"""
FT fund prices and holdings.

Walks every instrument with source 'ft', scrapes its price history and top
holdings from FT Markets, and stores both. Composites are rebuilt afterwards
because several of them are blends of these funds.

Ported from FTScrapper/scripts/runner.py.

Usage
-----
    python3 -m importers.ft_funds                  # prices and holdings
    python3 -m importers.ft_funds --no-holdings    # prices only, quicker
    python3 -m importers.ft_funds --holdings-only  # holdings only
"""

from __future__ import annotations

import argparse

from core import db
from core.repo import tickers as ticker_repo
from importers import composites, ft_scrape


def _latest_date(conn, fund_id):
    row = conn.execute("SELECT MAX(date) FROM prices WHERE fund_id = ?",
                       (fund_id,)).fetchone()
    return row[0] if row and row[0] else None


def _save_prices(conn, fund_id, fund_name, rows) -> int:
    conn.execute("""
        INSERT OR IGNORE INTO instruments (fund_id, name, asset_type, source,
                                           source_id)
        VALUES (?, ?, 'Fund', 'ft', ?)
    """, (fund_id, fund_name, fund_id))
    saved = 0
    for r in rows:
        saved += conn.execute("""
            INSERT OR IGNORE INTO prices
                (fund_id, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fund_id, r["date"], r["open"], r["high"], r["low"], r["close"],
              r["volume"])).rowcount
    return saved


def _save_holdings(conn, fund_id, fund_name, holdings) -> int:
    """Replace today's holdings for this fund - a rescrape should supersede
    an earlier one rather than sit alongside it."""
    if not holdings:
        return 0
    scraped = holdings[0]["scraped_date"]
    conn.execute("DELETE FROM pension_holdings WHERE fund_id = ? "
                 "AND scraped_date = ?", (fund_id, scraped))
    for h in holdings:
        conn.execute("""
            INSERT INTO pension_holdings
                (fund_id, fund_name, scraped_date, rank, name, ticker,
                 weight_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (fund_id, fund_name, scraped, h.get("rank"), h.get("name"),
              h.get("ticker"), h.get("weight_pct")))
    return len(holdings)


def _process(conn, fund, do_prices, do_holdings):
    fund_id = fund["id"]
    holdings_id = fund.get("holdings_id") or fund_id
    name = fund["name"]

    print(f"\n{'=' * 50}\n{name}\n{fund_id}")
    if holdings_id != fund_id:
        print(f"holdings via {holdings_id}")

    if do_prices:
        latest = _latest_date(conn, fund_id)
        print(f"  latest in database: {latest or 'none - first run'}")
        rows, fetched_name = ft_scrape.scrape_prices(fund_id, name, latest)
        if rows:
            print(f"  saved {_save_prices(conn, fund_id, fetched_name, rows)} "
                  f"new price rows")
        else:
            print("  no new price data")
        # FT's own name is authoritative - funds get renamed and the stored
        # name should follow.
        conn.execute("UPDATE instruments SET name = ? WHERE fund_id = ?",
                     (fetched_name, fund_id))

    if do_holdings:
        holdings = ft_scrape.scrape_holdings(holdings_id, name)
        saved = _save_holdings(conn, fund_id, name, holdings)
        print(f"  saved {saved} holding rows" if saved
              else "  no holdings retrieved")


def run(do_prices=True, do_holdings=True) -> dict:
    funds = ticker_repo.ft_funds()
    if not funds:
        return {"funds": 0, "message": "No instruments with source 'ft'"}

    print(f"FT fund scraper - {len(funds)} fund(s)")
    processed = failed = 0

    with db.get_conn() as conn:
        for fund in funds:
            try:
                _process(conn, fund, do_prices, do_holdings)
                processed += 1
            except Exception as exc:                           # noqa: BLE001
                # One fund failing should not lose the rest of the run.
                print(f"  ERROR processing {fund['name']}: {exc}")
                failed += 1
        conn.commit()

    if do_prices:
        print("\nRebuilding composites...")
        composites.run()

    print(f"\nDone. {processed} processed, {failed} failed.")
    return {"funds": processed, "failed": failed,
            "message": f"{processed} funds scraped, {failed} failed"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FT fund scraper")
    parser.add_argument("--no-holdings", action="store_true")
    parser.add_argument("--holdings-only", action="store_true")
    args = parser.parse_args()
    run(do_prices=not args.holdings_only, do_holdings=not args.no_holdings)
