"""
Live prices - latest available close for every tracked Yahoo ticker.

Ported from FTScrapper/scripts/live_prices.py. Same approach, now reading its
ticker list from the instruments table and writing through Pusula's db layer.

Why one batched download rather than per-ticker .info: .info hits Yahoo's
heaviest endpoint, and 120 of those back to back reliably trips rate limiting.
A single yf.download() pulls the lot in essentially one round trip.

Price source is the last available daily Close - during market hours that is
the delayed price, after close it is the official one.

Usage
-----
    python3 -m importers.live_prices              # every tracked ticker
    python3 -m importers.live_prices NATP.L       # one ticker
"""

from __future__ import annotations

import logging
import random
import sys
import time
from datetime import datetime

import pandas as pd
import yfinance as yf

from core import db
from core.repo import tickers as ticker_repo

# yfinance's own logging is misleading - "possibly delisted" nearly always
# means Yahoo returned nothing because it is throttling.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Yahoo quotes these in pence while the instrument is stored in pounds, so the
# fetched price needs dividing. Kept here rather than in the database because
# it describes Yahoo's behaviour, not the instrument's - though if the list
# grows it would be better as a column.
DIVIDE_BY_100 = {"WEAP.L", "NRGT.L"}

RETRIES = 4


def _download(symbols, tries=RETRIES):
    """One batched download with exponential backoff.

    Reports when a fetch only succeeded after retries - a sign Yahoo is still
    throttling even though the run looks clean.
    """
    for attempt in range(tries):
        try:
            data = yf.download(symbols, period="2d", interval="1d",
                               group_by="ticker",
                               threads=False,   # parallel threads trip limits
                               progress=False, auto_adjust=False)
            if data is not None and not data.empty:
                if attempt:
                    print(f"  succeeded only on attempt {attempt + 1}/{tries} "
                          f"- Yahoo still throttling")
                return data
            print(f"  attempt {attempt + 1}/{tries}: empty "
                  f"(usually rate limiting, not delisting)")
        except Exception as exc:                               # noqa: BLE001
            print(f"  attempt {attempt + 1}/{tries} error: {exc}")
        time.sleep((2 ** attempt) + random.uniform(0, 1))
    print(f"  gave up after {tries} attempts - Yahoo not responding")
    return None


def _close(data, ticker):
    """Latest non-null Close, handling single and multi-ticker frames."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            series = data[ticker]["Close"].dropna()
        else:
            series = data["Close"].dropna()
        return float(series.iloc[-1]) if len(series) else None
    except (KeyError, IndexError):
        return None


def _save(conn, fund_id, today, price):
    """Replace today's row for one fund. Open, high and low are set to the
    close because a live snapshot has no intraday range."""
    conn.execute("DELETE FROM prices WHERE fund_id = ? AND date = ?",
                 (fund_id, today))
    conn.execute("""
        INSERT INTO prices (fund_id, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (fund_id, today, price, price, price, price))


def run(ticker: str | None = None) -> dict:
    """
    Fetch and store prices. Returns a summary the importer registry can log.

    ticker: limit to one symbol. Unknown symbols are still attempted, so a
    ticker can be tested before its instrument row exists.
    """
    today = datetime.today().strftime("%Y-%m-%d")
    all_tickers = ticker_repo.yahoo_tickers()

    if ticker:
        wanted = ticker.upper()
        match = next((t for t in all_tickers if t[0].upper() == wanted), None)
        selected = [match] if match else [(ticker, ticker, None)]
    else:
        selected = all_tickers

    if not selected:
        return {"updated": 0, "failed": 0,
                "message": "No tracked tickers - check instruments.source"}

    print(f"Live prices - {today}")
    print(f"Fetching {len(selected)} ticker(s)\n")

    symbols = [t[0] for t in selected]
    # Indices sometimes misbehave in a mixed batch and can empty the whole
    # frame, so they go in their own call. Still only two requests.
    groups = [[s for s in symbols if s.startswith("^")],
              [s for s in symbols if not s.startswith("^")]]

    frames = []
    for group in groups:
        if not group:
            continue
        data = _download(group)
        if data is not None:
            frames.append(data)
        time.sleep(2)

    updated = failed = 0
    with db.get_conn() as conn:
        for item in selected:
            symbol, name = item[0], item[1]
            price = None
            for frame in frames:
                price = _close(frame, symbol)
                if price is not None:
                    break

            if price is None:
                print(f"  x {name:<45} no data")
                failed += 1
                continue

            if symbol in DIVIDE_BY_100:
                price = price / 100
            _save(conn, f"YF:{symbol}", today, price)
            print(f"  + {name:<45} {price:.4f}")
            updated += 1
        conn.commit()

    print(f"\nDone. Updated: {updated} | Failed: {failed}")
    return {"updated": updated, "failed": failed,
            "message": f"{updated} prices updated, {failed} failed"}


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)
