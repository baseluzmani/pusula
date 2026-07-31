"""
Backfill Yahoo prices for a date range.

For filling gaps - a ticker that stopped updating, a newly added instrument
that needs history, or a stretch Yahoo missed and later published.

Different from yahoo_prices in one way that matters: that importer decides its
own start date per ticker and only moves forward. This one takes the range you
give it and fetches exactly that, so it can go back over ground already
covered. Existing dates are skipped rather than overwritten, so re-running a
range is safe.

Batched: one download for every ticker in the range rather than one per
ticker. The original made 121 separate requests for a full backfill, which
Yahoo rate-limits long before it finishes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from core import db
from core.repo import tickers as ticker_repo

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

BATCH = 40   # tickers per request, small enough to stay under Yahoo's limits


def _rows(data, ticker) -> list:
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
            v = row.get(name)
            return float(v) if v is not None and not pd.isna(v) else float(close)

        volume = row.get("Volume")
        out.append({"date": date.strftime("%Y-%m-%d"),
                    "open": value("Open"), "high": value("High"),
                    "low": value("Low"), "close": float(close),
                    "volume": int(volume) if volume is not None
                              and not pd.isna(volume) else 0})
    return out

def _history_locked() -> set:
    """Tickers whose history must not be refetched.

    Their Yahoo history is wrong and has been replaced from another source,
    so a backfill would overwrite good data with bad. Daily updates are
    unaffected: yahoo_prices only moves forward from the last stored date,
    and it is only reaching back into the past that is blocked.
    """
    df = db.query("""
        SELECT source_id FROM instruments
        WHERE source = 'yahoo' AND COALESCE(latest_only, 0) = 1
          AND source_id IS NOT NULL AND source_id != ''
    """)
    return {s.upper() for s in df["source_id"]} if not df.empty else set()

def run(ticker: str | None = None, start: str | None = None,
        end: str | None = None) -> dict:
    """
    Fetch a date range and store anything not already held.

    ticker: one symbol, or None for every tracked Yahoo instrument.
    start, end: YYYY-MM-DD. end defaults to today.
    """
    if not start:
        return {"saved": 0, "message": "A start date is required"}
    end = end or datetime.today().strftime("%Y-%m-%d")
    if start > end:
        return {"saved": 0, "message": "Start date is after the end date"}

    all_tickers = ticker_repo.yahoo_tickers()
    if ticker:
        wanted = ticker.upper()
        match = next((t for t in all_tickers if t[0].upper() == wanted), None)
        selected = [match] if match else [(ticker, ticker, None)]
    else:
        selected = all_tickers

    if not selected:
        return {"saved": 0, "message": "No tracked Yahoo instruments"}
    
    locked = _history_locked()
    blocked = [t for t in selected if t[0].upper() in locked]
    selected = [t for t in selected if t[0].upper() not in locked]
    if blocked:
        print("History-locked, skipped: "
              + ", ".join(t[0] for t in blocked))
    if not selected:
        return {"saved": 0,
                "message": "Nothing to fetch — every selected ticker is "
                           "history-locked"}

    # Yahoo treats end as exclusive, so push it out a day to include it.
    fetch_end = (datetime.strptime(end, "%Y-%m-%d")
                 + timedelta(days=1)).strftime("%Y-%m-%d")

    print(f"Backfill {start} to {end} for {len(selected)} ticker(s)")

    symbols = [t[0] for t in selected]
    frames = []
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        print(f"  fetching {len(chunk)} ticker(s)...")
        try:
            data = yf.download(chunk, start=start, end=fetch_end,
                               group_by="ticker", threads=False,
                               progress=False, auto_adjust=True)
            if data is not None and not data.empty:
                frames.append((set(chunk), data))
            else:
                print("    empty response - usually rate limiting")
        except Exception as exc:                               # noqa: BLE001
            print(f"    error: {exc}")

    total = filled = skipped = complete = 0
    with db.get_conn() as conn:
        for item in selected:
            symbol = item[0]
            fund_id = f"YF:{symbol}"
            rows = []
            for chunk, data in frames:
                if symbol in chunk:
                    rows = _rows(data, symbol)
                    break
            if not rows:
                skipped += 1
                continue

            # An ad-hoc backfill of an untracked ticker still gets a proper
            # instrument row, so it behaves like any other from then on.
            conn.execute("""
                INSERT OR IGNORE INTO instruments (fund_id, name, asset_type,
                                                   source, source_id)
                VALUES (?, ?, ?, 'yahoo', ?)
            """, (fund_id, f"{item[1]} ({symbol})",
                  item[2] if len(item) > 2 else None, symbol))

            saved = 0
            for r in rows:
                saved += conn.execute("""
                    INSERT OR IGNORE INTO prices
                        (fund_id, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (fund_id, r["date"], r["open"], r["high"], r["low"],
                      r["close"], r["volume"])).rowcount
            if saved:
                filled += 1
                print(f"  + {symbol:<14} {len(rows):4d} fetched, {saved:4d} new")
            else:
                complete += 1
            total += saved
        conn.commit()

    message = (f"{total} rows added across {filled} ticker(s)"
               + (f", {complete} already complete" if complete else "")
               + (f", {skipped} returned nothing" if skipped else ""))
    print(f"\nDone. {message}")
    return {"saved": total, "filled": filled, "skipped": skipped,
            "message": message}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Backfill Yahoo prices")
    p.add_argument("--ticker", help="single symbol; omit for all")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end")
    a = p.parse_args()
    run(ticker=a.ticker, start=a.start, end=a.end)
