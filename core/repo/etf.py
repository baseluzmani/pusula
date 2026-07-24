"""
Everything that reads or writes ETF holdings data. No Dash, no formatting,
no colours - just data in, DataFrames out.

The identifier map is loaded once and cached. The old dashboard reloaded it
inside every call, which meant a 20-date trend chart read the full 1,667-row
map twenty times.
"""
from functools import lru_cache

import pandas as pd

from core import db

# --- Reference data ------------------------------------------------------

def list_etfs() -> list[str]:
    df = db.query(
        "SELECT DISTINCT etf_fund_id FROM etf_holdings ORDER BY etf_fund_id")
    return df["etf_fund_id"].tolist()


def list_dates(etf_id: str) -> list[str]:
    df = db.query(
        "SELECT DISTINCT scraped_date FROM etf_holdings "
        "WHERE etf_fund_id = ? ORDER BY scraped_date DESC", (etf_id,))
    return df["scraped_date"].tolist()


def latest_date(etf_id: str) -> str | None:
    dates = list_dates(etf_id)
    return dates[0] if dates else None


def latest_dates_all() -> dict[str, str]:
    """Latest snapshot date for every ETF, in one query."""
    df = db.query(
        "SELECT etf_fund_id, MAX(scraped_date) AS d "
        "FROM etf_holdings GROUP BY etf_fund_id")
    return dict(zip(df["etf_fund_id"], df["d"]))


# --- Identifier map ------------------------------------------------------

@lru_cache(maxsize=1)
def _stock_map() -> tuple:
    """
    Load stock_identifier_map once and build lookup dicts.

    Cached for the life of the process. Call clear_cache() after any write
    to the map, or the dashboard will keep serving stale mappings.
    """
    smap = db.query(
        "SELECT figi, name, bloomberg_code, base_ticker, raw_ticker, "
        "       sedol, isin, yahoo_id, group_figi "
        "FROM stock_identifier_map")

    lookups = {k: {} for k in
               ("bloomberg_code", "base_ticker", "raw_ticker", "sedol", "isin")}
    for idx, row in smap.iterrows():
        for col, table in lookups.items():
            val = row[col]
            if val and str(val).lower() != "nan":
                table.setdefault(str(val).upper().strip(), idx)

    # Canonical name and yahoo_id per group, resolved once rather than
    # re-searched for every holding row.
    parents = smap.set_index("figi")[["name", "yahoo_id"]].to_dict("index")

    return smap, lookups, parents


def clear_cache() -> None:
    """Call after editing stock_identifier_map."""
    _stock_map.cache_clear()


def _resolve(ticker, name, isin, smap, lookups, parents) -> tuple[str, str, str | None]:
    """Resolve one holding to (group_figi, canonical_name, yahoo_id)."""
    t = str(ticker).strip().upper() if ticker else ""
    base = t.split()[0] if t else ""

    idx = (lookups["bloomberg_code"].get(t)
           or lookups["raw_ticker"].get(t)
           or lookups["base_ticker"].get(t)
           or (lookups["base_ticker"].get(base) if base else None)
           or lookups["sedol"].get(t)
           or lookups["isin"].get(t))

    if idx is None and isin and str(isin).lower() not in ("nan", "none", ""):
        idx = lookups["isin"].get(str(isin).strip().upper())

    if idx is None:
        suffix = f"|{isin}" if isin else ""
        return f"RAW:{ticker}{suffix}|{name}", name, None

    row = smap.iloc[idx]
    gfigi = row["group_figi"] if row["group_figi"] and str(row["group_figi"]).lower() != "nan" else row["figi"]
    parent = parents.get(gfigi, {})

    cname = parent.get("name") or row["name"] or name
    if str(cname).lower() == "nan":
        cname = name
    return str(gfigi), cname, parent.get("yahoo_id") or row["yahoo_id"]


# --- Consolidated holdings ----------------------------------------------

_AGG_COLUMNS = [
    "name", "ticker", "sector", "asset_class",
    "weight_pct", "market_value", "location", "currency", "isin",
]


def _consolidate(raw: pd.DataFrame) -> pd.DataFrame:
    """Group raw holdings rows by resolved FIGI, summing weights."""
    if raw.empty:
        return raw

    smap, lookups, parents = _stock_map()

    resolved = [
        _resolve(r.ticker, r.name_, r.isin, smap, lookups, parents)
        for r in raw.rename(columns={"name": "name_"}).itertuples()
    ]
    raw = raw.copy()
    raw["canonical_id"] = [r[0] for r in resolved]
    raw["canonical_name"] = [r[1] for r in resolved]
    raw["yahoo_id"] = [r[2] for r in resolved]

    def agg(g):
        heaviest = (g.loc[g["weight_pct"].idxmax()]
                    if g["weight_pct"].notna().any() else g.iloc[0])
        return pd.Series({
            "name": heaviest["canonical_name"],
            "sector": heaviest["sector"],
            "asset_class": heaviest["asset_class"],
            "weight_pct": g["weight_pct"].sum() if g["weight_pct"].notna().any() else None,
            "market_value": g["market_value"].sum() if g["market_value"].notna().any() else None,
            "location": heaviest["location"],
            "currency": heaviest["currency"],
            "yahoo_id": heaviest["yahoo_id"],
        })

    out = (raw.groupby("canonical_id", sort=False)
              .apply(agg, include_groups=False)
              .reset_index())
    return out.sort_values("weight_pct", ascending=False, na_position="last")


def holdings(etf_id: str, date: str) -> pd.DataFrame:
    """Consolidated holdings for one ETF on one date."""
    raw = db.query(
        "SELECT name, ticker, sector, asset_class, weight_pct, "
        "       market_value, location, currency, isin "
        "FROM etf_holdings WHERE etf_fund_id = ? AND scraped_date = ?",
        (etf_id, date))
    return _consolidate(raw)


def holdings_history(etf_id: str, canonical_ids: list[str] | None = None) -> pd.DataFrame:
    """
    Weight history across every snapshot date, in ONE query.

    Returns columns: scraped_date, canonical_id, name, weight_pct.
    Optionally filtered to a list of canonical_ids (e.g. the current top 20).
    """
    raw = db.query(
        "SELECT scraped_date, name, ticker, sector, asset_class, weight_pct, "
        "       market_value, location, currency, isin "
        "FROM etf_holdings WHERE etf_fund_id = ?", (etf_id,))
    if raw.empty:
        return raw

    frames = []
    for date, chunk in raw.groupby("scraped_date"):
        con = _consolidate(chunk.drop(columns=["scraped_date"]))
        con["scraped_date"] = date
        frames.append(con)

    hist = pd.concat(frames, ignore_index=True)
    if canonical_ids is not None:
        hist = hist[hist["canonical_id"].isin(canonical_ids)]
    return hist[["scraped_date", "canonical_id", "name", "weight_pct"]]


def summary(df: pd.DataFrame) -> dict:
    """Headline statistics for a consolidated holdings frame."""
    if df.empty:
        return {}
    ranked = df.sort_values("weight_pct", ascending=False)
    sectors = df.groupby("sector")["weight_pct"].sum().sort_values(ascending=False)
    return {
        "holdings": len(df),
        "top5": ranked.head(5)["weight_pct"].sum(),
        "top10": ranked.head(10)["weight_pct"].sum(),
        "sectors": int(df["sector"].nunique()),
        "top_sector": sectors.index[0] if not sectors.empty else "-",
        "top_sector_weight": float(sectors.iloc[0]) if not sectors.empty else 0.0,
        "largest_name": ranked.iloc[0]["name"],
        "largest_weight": float(ranked.iloc[0]["weight_pct"] or 0),
        "total_weight": float(df["weight_pct"].sum() or 0),
    }


# --- Sources -------------------------------------------------------------

def ensure_sources_table() -> None:
    with db.get_conn() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS etf_sources ("
                     "etf_fund_id TEXT PRIMARY KEY, url TEXT)")


def sources() -> pd.DataFrame:
    ensure_sources_table()
    return db.query("SELECT etf_fund_id, url FROM etf_sources ORDER BY etf_fund_id")


def set_source(etf_id: str, url: str) -> int:
    ensure_sources_table()
    return db.execute(
        "INSERT INTO etf_sources (etf_fund_id, url) VALUES (?, ?) "
        "ON CONFLICT(etf_fund_id) DO UPDATE SET url = excluded.url",
        (etf_id, url))


# --- Identifier map maintenance -----------------------------------------

def unresolved_count() -> int:
    df = db.query(
        "SELECT COUNT(*) AS n FROM stock_identifier_map "
        "WHERE figi LIKE 'UNRESOLVED%' OR figi IS NULL OR figi = ''")
    return int(df["n"].iloc[0])


def map_rows(status: str = "all", search: str = "",
             search_yahoo: str = "", search_group: str = "") -> pd.DataFrame:
    sql = ("SELECT figi, group_figi, name, base_ticker, raw_ticker, "
           "       bloomberg_code, sedol, isin, yahoo_id, exch_code, reviewed "
           "FROM stock_identifier_map WHERE 1=1")
    params: list = []
    if status == "unresolved":
        sql += " AND (figi LIKE 'UNRESOLVED%' OR yahoo_id IS NULL OR yahoo_id = '')"
    elif status == "reviewed":
        sql += " AND reviewed = 1"
    elif status == "unreviewed":
        sql += " AND (reviewed = 0 OR reviewed IS NULL)"
    if search:
        sql += " AND (UPPER(name) LIKE ? OR UPPER(raw_ticker) LIKE ? OR UPPER(base_ticker) LIKE ?)"
        params += [f"%{search.upper()}%"] * 3
    if search_yahoo:
        sql += " AND UPPER(yahoo_id) LIKE ?"
        params.append(f"%{search_yahoo.upper()}%")
    if search_group:
        sql += " AND UPPER(group_figi) LIKE ?"
        params.append(f"%{search_group.upper()}%")
    sql += " ORDER BY name LIMIT 500"
    return db.query(sql, tuple(params))
