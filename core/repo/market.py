"""
Market data access for the Markets section (and, later, Portfolio).

All price/return reads go through here so there is one definition of a
"period return" across every tab. Returns DataFrames; no Dash imports.

Design notes
------------
- ``period_returns`` is vectorised: one grouped pass over the price frame
  computes every period at once, rather than the fund x period loop the
  original build_returns_table used (which re-filtered the frame for each
  cell and made Market Overview / Market Heatmap slow).
- Period returns are calendar-anchored (price today vs price N calendar
  days ago), matching the original behaviour. No weekly resampling here -
  that was only needed for correlation, which this section does not cover.
"""

import os
import threading
from datetime import datetime, timedelta

import pandas as pd

from core import config, db


# Period definitions shared by the returns table and the heatmap.
# days=None means "year to date" (measured from last close of prior year).
PERIODS = [
    ("1D", 1),
    ("1W", 7),
    ("1M", 30),
    ("3M", 91),
    ("6M", 182),
    ("YTD", None),
    ("1Y", 365),
]

# Never meaningful to chart or rank on returns.
EXCLUDED_TYPES = ("Cash", "House")


# --- Raw reads -----------------------------------------------------------

def prices(fund_ids=None, min_date=None) -> pd.DataFrame:
    """
    Price history joined to instrument metadata.

    Columns: fund_id, fund_name, asset_type, category, date, close.
    date is a datetime. Ordered by fund then date so downstream groupby
    operations see sorted series.
    """
    sql = ["""
        SELECT p.fund_id, i.name AS fund_name, i.asset_type, i.category,
               p.date, p.close
        FROM prices p
        JOIN instruments i ON i.fund_id = p.fund_id
        WHERE 1 = 1
    """]
    params = []

    if fund_ids:
        sql.append(f"AND p.fund_id IN ({_placeholders(fund_ids)})")
        params.extend(fund_ids)
    if min_date:
        sql.append("AND p.date >= ?")
        params.append(min_date)

    sql.append("ORDER BY p.fund_id, p.date")
    df = db.query("\n".join(sql), tuple(params))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def instruments(fund_ids=None) -> pd.DataFrame:
    """Instrument metadata. Columns: fund_id, name, asset_type, currency,
    price_unit, category."""
    sql = ("SELECT fund_id, name, asset_type, currency, price_unit, category "
           "FROM instruments")
    params = []
    if fund_ids:
        sql += f" WHERE fund_id IN ({_placeholders(fund_ids)})"
        params.extend(fund_ids)
    sql += " ORDER BY category, name"
    return db.query(sql, tuple(params))


def open_positions() -> pd.DataFrame:
    """
    Currently held instruments (units > 0), the "My holdings" universe.

    Sourced from portfolio_holdings, not derived from transactions - some
    holdings (e.g. HSBC funds) never appear in the transactions table.
    Columns: fund_id, units, name, asset_type, category, currency.
    """
    return db.query("""
        SELECT h.fund_id, h.units, i.name, i.asset_type, i.category, i.currency
        FROM portfolio_holdings h
        JOIN instruments i ON i.fund_id = h.fund_id
        WHERE h.units > 0
        ORDER BY i.asset_type, i.name
    """)


def open_position_ids() -> list:
    df = open_positions()
    return df["fund_id"].tolist() if not df.empty else []


def all_instrument_ids() -> list:
    """Every fund that has at least one price - the "All instruments" universe."""
    df = db.query("SELECT DISTINCT fund_id FROM prices ORDER BY fund_id")
    return df["fund_id"].tolist() if not df.empty else []


# --- Transactions --------------------------------------------------------

def transaction_fund_ids() -> list:
    df = db.query("SELECT DISTINCT fund_id FROM transactions ORDER BY fund_id")
    return df["fund_id"].tolist() if not df.empty else []


def transactions(fund_id: str) -> pd.DataFrame:
    """Buys and sells for one fund, oldest first.
    Columns: trade_date, type, quantity, price."""
    df = db.query("""
        SELECT trade_date, type, quantity, price
        FROM transactions WHERE fund_id = ? ORDER BY trade_date
    """, (fund_id,))
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


# --- Returns -------------------------------------------------------------

def _ytd_anchor() -> pd.Timestamp:
    """Last weekday of the previous year - the YTD baseline date."""
    d = datetime(datetime.now().year - 1, 12, 31)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return pd.Timestamp(d)

# Cache of finished returns tables. Keyed on the universe, the Since date AND a
# fingerprint of the database file, so a new price or edit changes the key and
# forces a recompute - it cannot serve stale numbers. One lock means two
# callbacks asking for the same table at once compute it once, not twice.
_RETURNS_CACHE: dict = {}
_RETURNS_LOCK = threading.Lock()
_CACHE_MAX = 40


def _db_stamp() -> tuple:
    """
    Fingerprint of the database: changes whenever anything writes to it.

    In WAL mode a write lands in the -wal file first and only reaches the main
    file at a checkpoint, so both files are checked. An empty -wal means no
    pending writes (a reader can create one), so it counts the same as none.
    """
    stamp = []
    for suffix in ("", "-wal"):
        try:
            st = os.stat(f"{config.DB_PATH}{suffix}")
        except OSError:
            stamp.append(None)
            continue
        empty_wal = suffix and st.st_size == 0
        stamp.append(None if empty_wal else (st.st_mtime_ns, st.st_size))
    return tuple(stamp)


def window_start(since_date) -> str:
    """
    Earliest price date any return column needs, plus a 30-day cushion for
    holidays and gaps. Loading from here instead of the full history is what
    keeps the table fast; nothing older can change a result.
    """
    longest = max(days for _, days in PERIODS if days)
    starts = [pd.Timestamp.now().normalize() - timedelta(days=longest),
              _ytd_anchor()]
    if since_date:
        starts.append(pd.Timestamp(since_date))
    return (min(starts) - timedelta(days=30)).strftime("%Y-%m-%d")


def period_returns(fund_ids=None, since_date=None,
                   price_df=None) -> pd.DataFrame:
    """
    One row per fund with return over each period in PERIODS, plus Since.

    Loads only the price window the columns need and caches the finished table
    (see _RETURNS_CACHE). Funds with no price inside that window are omitted.

    Pass ``price_df`` to reuse a frame already in hand; that path computes
    directly from the frame and bypasses the cache.

    Columns: fund_id, Fund, Type, Price, 1D, 1W, 1M, 3M, 6M, YTD, 1Y, Since.
    Return values are percentages. Funds excluded by type are dropped.
    """
    if price_df is not None:
        return _compute_returns(price_df, since_date)

    key = (tuple(sorted(fund_ids)) if fund_ids else None,
           str(since_date)[:10] if since_date else None, _db_stamp())
    with _RETURNS_LOCK:
        out = _RETURNS_CACHE.get(key)
        if out is None:
            out = _compute_returns(
                prices(fund_ids, min_date=window_start(since_date)),
                since_date)
            if len(_RETURNS_CACHE) >= _CACHE_MAX:
                _RETURNS_CACHE.pop(next(iter(_RETURNS_CACHE)))
            _RETURNS_CACHE[key] = out
    return out.copy()

def _compute_returns(df: pd.DataFrame, since_date) -> pd.DataFrame:
    """One pass per fund: latest close vs the last close at or before each
    cutoff, found by binary search on the sorted dates."""
    if df.empty:
        return pd.DataFrame()

    df = df[~df["asset_type"].isin(EXCLUDED_TYPES)]
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values(["fund_id", "date"]).drop_duplicates(["fund_id", "date"])
    latest_date = df["date"].max()
    ytd = _ytd_anchor()

    cutoffs = [(label, ytd if days is None else latest_date - timedelta(days=days))
               for label, days in PERIODS]
    cutoffs.append(("Since", pd.Timestamp(since_date) if since_date else None))

    rows = []
    for fid, g in df.groupby("fund_id", sort=False):
        dates = g["date"].to_numpy()
        closes = g["close"].to_numpy()
        last_price = closes[-1]
        meta = g.iloc[-1]

        row = {
            "fund_id": fid,
            "Fund": meta["fund_name"] if pd.notna(meta["fund_name"]) else fid,
            "Type": meta["asset_type"] if pd.notna(meta["asset_type"]) else "-",
            "Price": round(last_price, 2) if pd.notna(last_price) else None,
        }
        for label, cutoff in cutoffs:
            row[label] = _pct_from_arrays(dates, closes, cutoff, last_price)
        rows.append(row)

    out = pd.DataFrame(rows)
    return out.sort_values("YTD", ascending=False, na_position="last")


def _pct_from_arrays(dates, closes, cutoff, last_price) -> float | None:
    """Percentage change from the last close at/before cutoff to last_price."""
    if cutoff is None:
        return None
    i = dates.searchsorted(cutoff.to_datetime64(), side="right") - 1
    if i < 0:
        return None
    base = closes[i]
    if not base:
        return None
    return round((last_price / base - 1) * 100, 2)

def monthly_returns(fund_id: str) -> pd.DataFrame:
    """
    Month-by-month returns for one fund, plus a compounded yearly figure.

    Returns a frame indexed by year (descending) with columns 1..12 and
    'Year'; values are percentages, NaN where a month has no data. Shaped
    for the Transactions monthly heatmap.
    """
    df = prices([fund_id])
    if df.empty:
        return pd.DataFrame()

    monthly = (df.set_index("date")["close"]
                 .resample("ME").last().pct_change().mul(100).dropna())
    if monthly.empty:
        return pd.DataFrame()

    frame = pd.DataFrame({
        "year": monthly.index.year,
        "month": monthly.index.month,
        "ret": monthly.values,
    })
    pivot = frame.pivot_table(index="year", columns="month",
                              values="ret", aggfunc="first")
    # Compounded return per calendar year across whatever months exist.
    yearly = {}
    for year in pivot.index:
        yr = monthly[monthly.index.year == year]
        yearly[year] = (1 + yr / 100).prod() * 100 - 100 if len(yr) else None
    pivot["Year"] = pd.Series(yearly)
    return pivot.sort_index(ascending=False)


def _placeholders(items) -> str:
    return ",".join("?" for _ in items)
