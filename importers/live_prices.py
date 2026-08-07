"""
Live prices - the last traded price for every tracked Yahoo ticker.

Reads regularMarketPrice from Yahoo's quote endpoint and stores it under the
date its own timestamp gives, not under today's.

That last part is the point. The previous version downloaded two days of daily
bars and took the last non-null close. Yahoo publishes a row for the current
day before it has a price in it, so dropna() silently fell back to a close
several days old - and writing that under today's date made a stale figure
look fresh. On 4 August every US holding read 31 July's close, and the error
only surfaced as a large jump when a real price finally arrived.

regularMarketTime says when the quote was struck, so a market that has not
opened leaves its last real close standing instead of being restamped.

The docstring this replaces warned that .info is Yahoo's heaviest endpoint and
that 120 sequential calls trip rate limiting. Measured on the full list it is
19 seconds for 123 tickers with no failures, so the warning no longer holds -
but the batched download is kept as a fallback for anything the quote endpoint
does not answer.

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
from datetime import datetime, timezone

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


def _quote(symbol):
    """
    (price, date) from the quote endpoint, or (None, None).

    The date comes from regularMarketTime rather than the clock: a US close
    struck at 20:00 UTC and a Shanghai close struck at 07:00 UTC both belong
    to their own trading day, and neither is necessarily today.
    """
    try:
        info = yf.Ticker(symbol).info
    except Exception:                                          # noqa: BLE001
        return None, None

    price = info.get("regularMarketPrice")
    if price is None:
        return None, None

    stamp = info.get("regularMarketTime")
    if stamp:
        when = datetime.fromtimestamp(stamp, timezone.utc).strftime("%Y-%m-%d")
    else:
        # No timestamp is unusual enough to be worth not guessing about, but
        # a price with no date is useless, so today is the least-bad answer.
        when = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return float(price), when


def _download(symbols, tries=RETRIES):
    """Batched daily bars, used only for tickers the quote endpoint missed."""
    for attempt in range(tries):
        try:
            data = yf.download(symbols, period="5d", interval="1d",
                               group_by="ticker",
                               threads=False,   # parallel threads trip limits
                               progress=False, auto_adjust=False)
            if data is not None and not data.empty:
                return data
        except Exception as exc:                               # noqa: BLE001
            print(f"  fallback attempt {attempt + 1}/{tries}: {exc}")
        time.sleep((2 ** attempt) + random.uniform(0, 1))
    return None


def _close(data, ticker):
    """Latest non-null close and its own date, from a daily-bar frame."""
    try:
        if isinstance(data.columns, pd.MultiIndex):
            series = data[ticker]["Close"].dropna()
        else:
            series = data["Close"].dropna()
        if not len(series):
            return None, None
        return float(series.iloc[-1]), series.index[-1].strftime("%Y-%m-%d")
    except (KeyError, IndexError):
        return None, None


def _save(conn, fund_id, on_date, price):
    """Replace one fund's row for one date.

    Open, high and low are set to the close because a quote has no intraday
    range. yahoo_prices overwrites these with real bars when it runs.
    """
    conn.execute("DELETE FROM prices WHERE fund_id = ? AND date = ?",
                 (fund_id, on_date))
    conn.execute("""
        INSERT INTO prices (fund_id, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, 0)
    """, (fund_id, on_date, price, price, price, price))


def _rebuild_composites():
    """Composites are priced from the prices table, so they must be rebuilt
    whenever their components move - not just after the nightly history
    import. Without this the pension lines would sit at their evening value
    all day while everything around them ticked."""
    try:
        from importers import composites
        result = composites.run()
        print(f"  composites: {result.get('message', 'rebuilt')}")
    except Exception as exc:                                   # noqa: BLE001
        print(f"  ERROR building composites: {exc}")


def run(ticker: str | None = None) -> dict:
    """
    Fetch and store prices. Returns a summary the importer registry can log.

    ticker: limit to one symbol. Unknown symbols are still attempted, so a
    ticker can be tested before its instrument row exists.
    """
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

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"Live prices - {today}")
    print(f"Fetching {len(selected)} ticker(s)\n")

    results = {}
    missing = []
    for item in selected:
        symbol = item[0]
        price, when = _quote(symbol)
        if price is None:
            missing.append(item)
            continue
        results[symbol] = (price, when)

    # Anything the quote endpoint did not answer gets one batched download.
    if missing:
        print(f"  {len(missing)} without a quote, trying daily bars: "
              f"{', '.join(t[0] for t in missing[:8])}"
              f"{'...' if len(missing) > 8 else ''}")
        data = _download([t[0] for t in missing])
        if data is not None:
            for item in missing:
                price, when = _close(data, item[0])
                if price is not None:
                    results[item[0]] = (price, when)

    updated = failed = stale = 0
    with db.get_conn() as conn:
        for item in selected:
            symbol, name = item[0], item[1]
            got = results.get(symbol)
            if got is None:
                print(f"  x {name:<45} no price")
                failed += 1
                continue

            price, when = got
            if symbol in DIVIDE_BY_100:
                price = price / 100

            _save(conn, f"YF:{symbol}", when, price)
            # A quote older than today is normal - a closed market, or a fund
            # that prices once a day - but worth showing, because a date that
            # stops moving is the first sign a ticker has gone stale.
            marker = "" if when == today else f"  [{when}]"
            print(f"  + {name:<45} {price:.4f}{marker}")
            if when != today:
                stale += 1
            updated += 1
        conn.commit()

    if updated:
        print("\nRebuilding composite prices...")
        _rebuild_composites()

    message = f"{updated} prices updated, {failed} failed"
    if stale:
        message += f", {stale} not from today"
    print(f"\nDone. {message}")
    return {"updated": updated, "failed": failed, "stale": stale,
            "message": message}


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else None)