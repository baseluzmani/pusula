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
    """
    One row per ETF that has holdings data, with its download URL if set.
    ETFs with no URL recorded still appear, so gaps are visible.
    """
    ensure_sources_table()
    return db.query("""
        SELECT h.etf_fund_id, COALESCE(s.url, '') AS url,
               MAX(h.scraped_date) AS last_import,
               COUNT(DISTINCT h.scraped_date) AS snapshots
        FROM etf_holdings h
        LEFT JOIN etf_sources s ON s.etf_fund_id = h.etf_fund_id
        GROUP BY h.etf_fund_id ORDER BY h.etf_fund_id
    """)


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




# --- Comparison ----------------------------------------------------------
# One engine for both cases: same ETF at two dates (change over time), or two
# ETFs at their latest dates (cross-sectional difference).

MIN_WEIGHT = 0.0   # set >0 to ignore tiny positions


def compare(etf_a: str, date_a: str, etf_b: str, date_b: str,
            min_weight: float = MIN_WEIGHT) -> dict:
    """
    Diff two holdings snapshots.

    Returns a dict with:
      a, b            - the two consolidated frames
      common          - DataFrame: canonical_id, name, sector, weight_a,
                        weight_b, delta   (sorted by |delta| desc)
      only_a, only_b  - DataFrames of positions unique to one side
      overlap         - sum of min(weight_a, weight_b), the true overlap
      same_fund       - True when both sides are the same ETF
    """
    a = holdings(etf_a, date_a)
    b = holdings(etf_b, date_b)

    if min_weight:
        a = a[a["weight_pct"] >= min_weight]
        b = b[b["weight_pct"] >= min_weight]

    ia = a.set_index("canonical_id")
    ib = b.set_index("canonical_id")
    keys_a, keys_b = set(ia.index), set(ib.index)
    shared = keys_a & keys_b

    common = pd.DataFrame([{
        "canonical_id": k,
        "name": ia.at[k, "name"] or ib.at[k, "name"],
        "sector": ia.at[k, "sector"],
        "weight_a": float(ia.at[k, "weight_pct"] or 0),
        "weight_b": float(ib.at[k, "weight_pct"] or 0),
    } for k in shared])

    if not common.empty:
        common["delta"] = common["weight_b"] - common["weight_a"]
        common = common.sort_values("delta", key=abs, ascending=False)
        overlap = float(common[["weight_a", "weight_b"]].min(axis=1).sum())
    else:
        overlap = 0.0

    return {
        "a": a, "b": b,
        "common": common,
        "only_a": a[a["canonical_id"].isin(keys_a - keys_b)],
        "only_b": b[b["canonical_id"].isin(keys_b - keys_a)],
        "overlap": overlap,
        "same_fund": etf_a == etf_b,
    }


def top_n(df: pd.DataFrame, n: int = 10) -> pd.DataFrame:
    return df.sort_values("weight_pct", ascending=False).head(n)


# --- Changes between two snapshots ---------------------------------------

def changes(etf_id: str, date_from: str, date_to: str,
            min_weight: float = 0.5) -> dict[str, pd.DataFrame]:
    """
    Compare two snapshots of the same ETF.

    Returns four frames keyed 'new', 'removed', 'increased', 'decreased'.
    Holdings below min_weight are ignored, so trivial rounding moves in the
    long tail don't swamp the result.
    """
    a = holdings(etf_id, date_from)
    b = holdings(etf_id, date_to)
    if a.empty or b.empty:
        empty = pd.DataFrame(columns=["canonical_id", "name", "weight_from",
                                      "weight_to", "change"])
        return {k: empty.copy() for k in ("new", "removed", "increased", "decreased")}

    a = a[a["weight_pct"] >= min_weight]
    b = b[b["weight_pct"] >= min_weight]

    merged = a.merge(b, on="canonical_id", how="outer",
                     suffixes=("_from", "_to"), indicator=True)
    merged["name"] = merged["name_to"].fillna(merged["name_from"])
    merged = merged.rename(columns={"weight_pct_from": "weight_from",
                                    "weight_pct_to": "weight_to"})
    merged["change"] = merged["weight_to"].fillna(0) - merged["weight_from"].fillna(0)

    cols = ["canonical_id", "name", "weight_from", "weight_to", "change"]
    both = merged["_merge"] == "both"

    return {
        "new": merged[merged["_merge"] == "right_only"][cols]
                 .sort_values("weight_to", ascending=False),
        "removed": merged[merged["_merge"] == "left_only"][cols]
                 .sort_values("weight_from", ascending=False),
        "increased": merged[both & (merged["change"] > 0)][cols]
                 .sort_values("change", ascending=False),
        "decreased": merged[both & (merged["change"] < 0)][cols]
                 .sort_values("change"),
    }


# --- Compare two ETFs ----------------------------------------------------

def compare(etf_a: str, etf_b: str) -> dict:
    """
    Side-by-side comparison of the latest snapshot of two ETFs.

    Portfolio overlap is the sum of min(weight_a, weight_b) across common
    holdings - the share of capital genuinely invested in the same names.
    """
    date_a, date_b = latest_date(etf_a), latest_date(etf_b)
    if not date_a or not date_b:
        return {}

    a, b = holdings(etf_a, date_a), holdings(etf_b, date_b)
    if a.empty or b.empty:
        return {}

    merged = a.merge(b, on="canonical_id", how="outer",
                     suffixes=("_a", "_b"), indicator=True)
    merged["name"] = merged["name_a"].fillna(merged["name_b"])
    merged["sector"] = merged["sector_a"].fillna(merged["sector_b"])
    merged = merged.rename(columns={"weight_pct_a": "weight_a",
                                    "weight_pct_b": "weight_b"})
    merged["diff"] = merged["weight_a"].fillna(0) - merged["weight_b"].fillna(0)

    cols = ["canonical_id", "name", "sector", "weight_a", "weight_b", "diff"]
    common = merged[merged["_merge"] == "both"][cols].copy()
    common["avg"] = (common["weight_a"] + common["weight_b"]) / 2
    common = common.sort_values("avg", ascending=False).drop(columns="avg")

    only_a = (merged[merged["_merge"] == "left_only"][cols]
              .sort_values("weight_a", ascending=False))
    only_b = (merged[merged["_merge"] == "right_only"][cols]
              .sort_values("weight_b", ascending=False))

    overlap = float(common[["weight_a", "weight_b"]].min(axis=1).sum()) if not common.empty else 0.0

    return {
        "date_a": date_a, "date_b": date_b,
        "holdings_a": a, "holdings_b": b,
        "common": common, "only_a": only_a, "only_b": only_b,
        "overlap": overlap,
        "top10_a": a.head(10), "top10_b": b.head(10),
        "top10_weight_a": float(a.head(10)["weight_pct"].sum()),
        "top10_weight_b": float(b.head(10)["weight_pct"].sum()),
    }

def map_summary() -> dict:
    df = db.query(
        "SELECT COUNT(*) AS total, "
        "       SUM(CASE WHEN reviewed = 1 THEN 1 ELSE 0 END) AS reviewed, "
        "       SUM(CASE WHEN reviewed = 0 OR reviewed IS NULL THEN 1 ELSE 0 END) AS unreviewed "
        "FROM stock_identifier_map")
    r = df.iloc[0]
    return {"total": int(r["total"] or 0),
            "reviewed": int(r["reviewed"] or 0),
            "unreviewed": int(r["unreviewed"] or 0)}


MAP_COLUMNS = ("figi, name, base_ticker, exch_code, bloomberg_code, raw_ticker, "
               "sedol, isin, yahoo_id, security_type, group_figi, reviewed, notes")

# Heaviest weight each identifier reaches in any fund's latest snapshot, so the
# review queue can be ordered by what actually matters. Built as a CTE over the
# latest date per ETF rather than a correlated subquery per row.
_LATEST_WEIGHTS = """
WITH latest AS (
    SELECT etf_fund_id, MAX(scraped_date) AS d
    FROM etf_holdings GROUP BY etf_fund_id
),
w AS (
    SELECT UPPER(TRIM(h.ticker)) AS t, MAX(h.weight_pct) AS max_weight
    FROM etf_holdings h
    JOIN latest l ON l.etf_fund_id = h.etf_fund_id AND l.d = h.scraped_date
    WHERE h.ticker IS NOT NULL AND TRIM(h.ticker) != ''
    GROUP BY UPPER(TRIM(h.ticker))
)
"""


def map_records(status: str = "unreviewed", search: str = "",
                search_yahoo: str = "", search_group: str = "",
                limit: int = 500) -> pd.DataFrame:
    """Rows for the review table, heaviest holdings first."""
    conditions, params = [], []

    if status == "unreviewed":
        conditions.append("(s.reviewed = 0 OR s.reviewed IS NULL)")
    elif status == "reviewed":
        conditions.append("s.reviewed = 1")
    elif status == "empty":
        conditions.append("(s.yahoo_id IS NULL OR s.yahoo_id = '')")
    elif status == "has_yahoo":
        conditions.append("(s.yahoo_id IS NOT NULL AND s.yahoo_id != '')")

    if search:
        conditions.append("(LOWER(s.name) LIKE ? OR LOWER(s.bloomberg_code) LIKE ? "
                          "OR LOWER(s.base_ticker) LIKE ? OR LOWER(s.raw_ticker) LIKE ?)")
        params += [f"%{search.lower()}%"] * 4
    if search_yahoo:
        conditions.append("LOWER(COALESCE(s.yahoo_id, '')) LIKE ?")
        params.append(f"%{search_yahoo.lower()}%")
    if search_group:
        conditions.append("LOWER(COALESCE(s.group_figi, '')) LIKE ?")
        params.append(f"%{search_group.lower()}%")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    cols = ", ".join(f"s.{c.strip()}" for c in MAP_COLUMNS.split(","))
    params.append(limit)

    return db.query(f"""
        {_LATEST_WEIGHTS}
        SELECT {cols}, ROUND(w.max_weight, 2) AS max_weight
        FROM stock_identifier_map s
        LEFT JOIN w ON w.t IN (s.bloomberg_code, s.base_ticker, s.raw_ticker, s.sedol)
        {where}
        ORDER BY s.reviewed ASC, w.max_weight DESC NULLS LAST, s.name
        LIMIT ?
    """, tuple(params))


def map_record(figi: str) -> dict | None:
    cols = ", ".join(f"s.{c.strip()}" for c in MAP_COLUMNS.split(","))
    df = db.query(f"""
        {_LATEST_WEIGHTS}
        SELECT {cols}, ROUND(w.max_weight, 2) AS max_weight
        FROM stock_identifier_map s
        LEFT JOIN w ON w.t IN (s.bloomberg_code, s.base_ticker, s.raw_ticker, s.sedol)
        WHERE s.figi = ? LIMIT 1
    """, (figi,))
    return df.iloc[0].to_dict() if not df.empty else None


def _clean(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ("nan", "none") else None


_EDITABLE = ("name", "bloomberg_code", "raw_ticker", "sedol", "isin", "notes")


def map_save(figi: str, fields: dict, yahoo_id: str | None,
             group_figi: str | None, new_figi: str | None = None) -> str:
    """
    Save one mapping and mark it reviewed.

    If new_figi differs, the row is rewritten under the new key - FIGI is the
    primary key, so it can't be updated in place. Returns the resulting FIGI.
    """
    values = {k: _clean(fields.get(k)) for k in _EDITABLE}
    yahoo = _clean(yahoo_id)
    if yahoo:
        yahoo = f"YF:{yahoo.replace('YF:', '')}"

    target = _clean(new_figi) or figi
    group = _clean(group_figi) or target

    with db.get_conn() as conn:
        if target != figi:
            old = conn.execute("SELECT * FROM stock_identifier_map WHERE figi = ?",
                               (figi,)).fetchone()
            old = dict(old) if old else {}
            conn.execute("DELETE FROM stock_identifier_map WHERE figi = ?", (figi,))
            conn.execute(f"""
                INSERT OR REPLACE INTO stock_identifier_map ({MAP_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (target,
                  values["name"] or old.get("name"),
                  old.get("base_ticker"), old.get("exch_code"),
                  values["bloomberg_code"] or old.get("bloomberg_code"),
                  values["raw_ticker"] or old.get("raw_ticker"),
                  values["sedol"] or old.get("sedol"),
                  values["isin"] or old.get("isin"),
                  yahoo or old.get("yahoo_id"),
                  old.get("security_type"), group, values["notes"]))
        else:
            conn.execute("""
                UPDATE stock_identifier_map
                SET yahoo_id = ?, reviewed = 1, notes = ?, group_figi = ?,
                    name           = COALESCE(?, name),
                    bloomberg_code = COALESCE(?, bloomberg_code),
                    raw_ticker     = COALESCE(?, raw_ticker),
                    sedol          = COALESCE(?, sedol),
                    isin           = COALESCE(?, isin)
                WHERE figi = ?
            """, (yahoo, values["notes"], group, values["name"],
                  values["bloomberg_code"], values["raw_ticker"],
                  values["sedol"], values["isin"], figi))
    clear_cache()
    return target


def map_mark_empty(figi: str, fields: dict, group_figi: str | None) -> None:
    """Mark reviewed with no Yahoo ID - a deliberate 'this has no price feed'."""
    values = {k: _clean(fields.get(k)) for k in _EDITABLE}
    db.execute("""
        UPDATE stock_identifier_map
        SET yahoo_id = NULL, reviewed = 1, notes = ?, group_figi = ?,
            name           = COALESCE(?, name),
            bloomberg_code = COALESCE(?, bloomberg_code),
            raw_ticker     = COALESCE(?, raw_ticker),
            sedol          = COALESCE(?, sedol),
            isin           = COALESCE(?, isin)
        WHERE figi = ?
    """, (values["notes"], _clean(group_figi) or figi, values["name"],
          values["bloomberg_code"], values["raw_ticker"], values["sedol"],
          values["isin"], figi))
    clear_cache()


def map_auto_approve() -> int:
    """Mark reviewed every unreviewed row that already has a FIGI and Yahoo ID."""
    n = db.execute("""
        UPDATE stock_identifier_map SET reviewed = 1
        WHERE (reviewed = 0 OR reviewed IS NULL)
          AND figi IS NOT NULL AND figi NOT LIKE 'UNRESOLVED%'
          AND yahoo_id IS NOT NULL AND yahoo_id != ''
    """)
    clear_cache()
    return n
