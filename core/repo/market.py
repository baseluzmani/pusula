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

from datetime import datetime, timedelta

import pandas as pd

from core import db


# Period definitions shared by the returns table and the heatmap.
# days=None means "year to date" (measured from last close of prior year).
PERIODS = [
    ("1D", 1),
    ("1W", 5),
    ("1M", 21),
    ("3M", 63),
    ("6M", 126),
    ("YTD", None),
    ("1Y", 252),
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


def period_returns(fund_ids=None, since_date=None,
                   price_df=None) -> pd.DataFrame:
    """
    One row per fund with return over each period in PERIODS, plus Since.

    Vectorised: sorts once, then for each fund takes the latest close and,
    per period, the last close at or before the cutoff date. This replaces
    the original per-cell calc_return loop.

    Pass ``price_df`` to reuse a frame already in hand (the pages load prices
    once for the chart and pass the same frame here, avoiding a second read).

    Columns: fund_id, Fund, Type, Price, 1D, 1W, 1M, 3M, 6M, YTD, 1Y, Since.
    Return values are percentages. Funds excluded by type are dropped.
    """
    df = price_df if price_df is not None else prices(fund_ids)
    if df.empty:
        return pd.DataFrame()

    df = df[~df["asset_type"].isin(EXCLUDED_TYPES)]
    if df.empty:
        return pd.DataFrame()

    df = df.sort_values(["fund_id", "date"])
    latest_date = df["date"].max()
    ytd = _ytd_anchor()
    since = pd.Timestamp(since_date) if since_date else None

    rows = []
    for fid, g in df.groupby("fund_id", sort=False):
        g = g.drop_duplicates("date")
        closes = g.set_index("date")["close"]
        last_price = closes.iloc[-1]
        meta = g.iloc[-1]

        row = {
            "fund_id": fid,
            "Fund": meta["fund_name"] if pd.notna(meta["fund_name"]) else fid,
            "Type": meta["asset_type"] if pd.notna(meta["asset_type"]) else "-",
            "Price": round(last_price, 2) if pd.notna(last_price) else None,
        }

        for label, days in PERIODS:
            cutoff = ytd if days is None else latest_date - timedelta(days=days)
            row[label] = _pct_at_or_before(closes, cutoff, last_price)

        row["Since"] = (_pct_at_or_before(closes, since, last_price)
                        if since is not None else None)
        rows.append(row)

    out = pd.DataFrame(rows)
    return out.sort_values("YTD", ascending=False, na_position="last")


def _pct_at_or_before(closes: pd.Series, cutoff, last_price) -> float | None:
    """Percentage change from the last close at/before cutoff to last_price."""
    prior = closes[closes.index <= cutoff]
    if prior.empty:
        return None
    base = prior.iloc[-1]
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
