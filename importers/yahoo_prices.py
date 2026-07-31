"""
Yahoo prices - daily history for every tracked ticker.

Ported from FTScrapper/scripts/yahoofinanceimporter.py. Reads its ticker list
from the instruments table and writes through Pusula's db layer.

Why it batches: the original called yf.download() once per ticker, which meant
120 requests per run and reliable rate limiting. Tickers are grouped by the
start date they need - almost all share one in steady state - and fetched in
batched calls, typically two requests rather than 120.

The last few days are always reimported, because Yahoo revises recent figures
after the fact and a one-way "only fetch what is newer" import would keep the
first, wrong version forever.

Usage
-----
    python3 -m importers.yahoo_prices             # every tracked ticker
    python3 -m importers.yahoo_prices ^GSPC       # one ticker
"""

from __future__ import annotations

import logging
import random
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from core import config, db
from core.repo import tickers as ticker_repo

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

FIRST_RUN_DAYS = 730   # history fetched the first time a ticker is seen
REIMPORT_DAYS = 3      # recent days always refetched, to pick up revisions
RETRIES = 4


def _download(symbols, start_date, end_date, tries=RETRIES):
    """Batched download with exponential backoff.

    A clean first attempt is silent; one that needed retries says so, because
    that means Yahoo is still throttling even though the run looks fine.
    """
    for attempt in range(tries):
        try:
            data = yf.download(symbols, start=start_date, end=end_date,
                               group_by="ticker", threads=False,
                               progress=False, auto_adjust=True)
            if data is not None and not data.empty:
                if attempt:
                    print(f"    succeeded only on attempt {attempt + 1}/{tries}"
                          f" - Yahoo still throttling")
                return data
            print(f"    attempt {attempt + 1}/{tries}: empty "
                  f"(nearly always rate limiting, not delisting)")
        except Exception as exc:                               # noqa: BLE001
            print(f"    attempt {attempt + 1}/{tries} error: {exc}")
        time.sleep((2 ** attempt) + random.uniform(0, 1))
    print(f"    gave up after {tries} attempts - Yahoo not responding")
    return None


def _rows(data, ticker) -> list:
    """OHLCV rows for one ticker out of a possibly multi-ticker frame.

    Missing open, high or low fall back to the close - some instruments only
    ever report a close, and a null there would fail the not-null columns.
    """
    multi = isinstance(data.columns, pd.MultiIndex)
    try:
        sub = data[ticker] if multi else data
    except KeyError:
        return []

    out = []
    for date, row in sub.dropna(subset=["Close"]).iterrows():
        close = row["Close"]
        if pd.isna(close):
            continue

        def value(name):
            v = row[name]
            return float(v) if not pd.isna(v) else float(close)

        volume = row["Volume"]
        out.append({"date": date.strftime("%Y-%m-%d"),
                    "open": value("Open"), "high": value("High"),
                    "low": value("Low"), "close": float(close),
                    "volume": int(volume) if not pd.isna(volume) else 0})
    return out

def _history_locked() -> set:
    """Tickers yahoo_prices must not touch.

    Their Yahoo history is wrong and has been replaced from another source,
    so the reimport window would overwrite good data with bad. live_prices
    still updates them daily - it only ever rewrites today's row - so they
    stay current without their history being revised.
    """
    df = db.query("""
        SELECT source_id FROM instruments
        WHERE source = 'yahoo' AND COALESCE(latest_only, 0) = 1
          AND source_id IS NOT NULL AND source_id != ''
    """)
    return {s.upper() for s in df["source_id"]} if not df.empty else set()


def _plan(conn, ticker, name, asset_type) -> dict:
    """
    Decide where this ticker needs fetching from, and clear rows to be redone.

    Local work only - no Yahoo calls - so every ticker can be planned before
    a single request goes out, which is what allows the batching.

    The reimport window does two jobs. It picks up Yahoo's revisions to recent
    figures, and - the one that is easy to miss - it replaces the provisional
    rows live_prices writes during the day. Those carry open=high=low=close
    and no volume, because a live snapshot has no intraday range; refetching
    the last few days turns them into proper bars.

    Tickers returned by _history_locked() never reach here: they are filtered
    out in run(), because for them this window would overwrite good data with
    bad.
    """
    fund_id = f"YF:{ticker}"
    fund_name = f"{name} ({ticker})"
    reimport_from = (datetime.today()
                     - timedelta(days=REIMPORT_DAYS)).strftime("%Y-%m-%d")

    row = conn.execute("SELECT MAX(date) FROM prices WHERE fund_id = ?",
                       (fund_id,)).fetchone()
    latest = row[0] if row and row[0] else None

    if latest:
        cleared = conn.execute(
            "DELETE FROM prices WHERE fund_id = ? AND date >= ?",
            (fund_id, reimport_from)).rowcount
        if cleared:
            print(f"  {fund_name:<45} | cleared {cleared} rows for reimport")
        day_after = (datetime.strptime(latest, "%Y-%m-%d")
                     + timedelta(days=1)).strftime("%Y-%m-%d")
        start_date = min(day_after, reimport_from)
    else:
        start_date = (datetime.today()
                      - timedelta(days=FIRST_RUN_DAYS)).strftime("%Y-%m-%d")
        print(f"  {fund_name:<45} | first import | {FIRST_RUN_DAYS} days")

    return {"ticker": ticker, "fund_id": fund_id, "fund_name": fund_name,
            "asset_type": asset_type, "start_date": start_date}

def _save(conn, plan, rows) -> int:
    """Insert rows, skipping any date already held. Returns rows actually
    written, so the count reflects new data rather than rows fetched."""
    # An ad-hoc import of a ticker with no instrument row creates one, marked
    # as a yahoo source so it behaves like any other from then on.
    conn.execute("""
        INSERT OR IGNORE INTO instruments (fund_id, name, asset_type,
                                           source, source_id)
        VALUES (?, ?, ?, 'yahoo', ?)
    """, (plan["fund_id"], plan["fund_name"], plan["asset_type"],
          plan["ticker"]))

    saved = 0
    for r in rows:
        saved += conn.execute("""
            INSERT OR IGNORE INTO prices
                (fund_id, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (plan["fund_id"], r["date"], r["open"], r["high"], r["low"],
              r["close"], r["volume"])).rowcount
    return saved


def _rebuild_composites():
    """Rebuild composite series from the freshly imported components.

    In-process now. This used to shell out to FTScrapper's
    build_composite_prices.py, which was deleted — and the exists() check
    meant it printed "skipped" and carried on, so composites quietly stopped
    being rebuilt after each price import.
    """
    try:
        from importers import composites
        result = composites.run()
        print(f"  composites: {result.get('message', 'rebuilt')}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"  ERROR building composites: {exc}")

def run(ticker: str | None = None) -> dict:
    """Fetch and store price history. Returns a summary for the registry."""
    all_tickers = ticker_repo.yahoo_tickers()

    if ticker:
        wanted = ticker.upper()
        match = next((t for t in all_tickers if t[0].upper() == wanted), None)
        if match:
            selected = [match]
        else:
            print(f"  {wanted} not tracked - importing under its own name")
            selected = [(ticker, ticker, None)]
    else:
        selected = all_tickers

    if not selected:
        return {"saved": 0, "message": "No tracked tickers"}

    # History-locked tickers never reach _plan: the reimport window would
    # delete good rows and refetch them from Yahoo, which is the source the
    # lock exists to keep out. live_prices still updates them daily.
    locked = _history_locked()
    blocked = [t for t in selected if t[0].upper() in locked]
    selected = [t for t in selected if t[0].upper() not in locked]
    if blocked:
        print("History-locked, left to live_prices: "
              + ", ".join(t[0] for t in blocked))
    if not selected:
        return {"saved": 0, "message": "Every selected ticker is history-locked"}

    print("Yahoo price importer")
    print(f"Processing {len(selected)} ticker(s)\n")

    with db.get_conn() as conn:
        plans = [_plan(conn, t[0], t[1], t[2] if len(t) > 2 else None)
                 for t in selected]
        conn.commit()

        end_date = datetime.today().strftime("%Y-%m-%d")
        by_start = defaultdict(list)
        for p in plans:
            by_start[p["start_date"]].append(p["ticker"])

        print("\nFetching in batched calls...")
        frames = []
        for start_date, symbols in by_start.items():
            # Indices can misbehave in a mixed batch and empty the whole
            # frame, so they are fetched separately.
            for group in ([s for s in symbols if s.startswith("^")],
                          [s for s in symbols if not s.startswith("^")]):
                if not group:
                    continue
                print(f"  {len(group)} ticker(s) from {start_date} to {end_date}")
                data = _download(group, start_date, end_date)
                if data is not None:
                    frames.append((set(group), data))
                time.sleep(2)

        total = 0
        for p in plans:
            rows = []
            for symbols, data in frames:
                if p["ticker"] in symbols:
                    rows = _rows(data, p["ticker"])
                    break
            if not rows:
                print(f"  x {p['fund_name']:<45} no data")
                continue
            saved = _save(conn, p, rows)
            total += saved
            dates = [r["date"] for r in rows]
            print(f"  + {p['fund_name']:<45} {len(rows)} rows | {saved} new "
                  f"| {min(dates)} to {max(dates)}")
        conn.commit()

    print("\nBuilding composite prices...")
    _rebuild_composites()

    print(f"\nDone. {total} new rows saved.")
    return {"saved": total, "message": f"{total} new price rows"}

if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
