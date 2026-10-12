"""
Pension fund holdings and composite (blended) funds.

Everything here honours an "as of" date: the latest snapshot on or before the
date requested. That makes composites honest - previously a composite mixed
each component's own latest date, so a June component could be blended with a
July one without anything saying so.
"""
import pandas as pd

from core import db, universe
from core.repo import etf as etf_repo


# --- Pension funds -------------------------------------------------------

def list_funds() -> pd.DataFrame:
    """Funds that have any holdings data."""
    return db.query(
        "SELECT DISTINCT fund_id, fund_name FROM pension_holdings "
        "ORDER BY fund_name")


def effective_date(fund_id: str, as_of: str | None = None) -> str | None:
    """Latest snapshot on or before as_of. Latest overall if as_of is None."""
    if as_of:
        df = db.query(
            "SELECT MAX(scraped_date) AS d FROM pension_holdings "
            "WHERE fund_id = ? AND scraped_date <= ?", (fund_id, as_of))
    else:
        df = db.query(
            "SELECT MAX(scraped_date) AS d FROM pension_holdings "
            "WHERE fund_id = ?", (fund_id,))
    return df["d"].iloc[0] if not df.empty else None


def fund_holdings(fund_id: str, as_of: str | None = None) -> tuple[pd.DataFrame, str | None]:
    date = effective_date(fund_id, as_of)
    if not date:
        return pd.DataFrame(columns=["rank", "name", "ticker", "weight_pct",
                                     "key"]), None
    df = db.query(
        "SELECT rank, name, ticker, weight_pct FROM pension_holdings "
        "WHERE fund_id = ? AND scraped_date = ? ORDER BY rank", (fund_id, date))
    return _consolidate(df), date


def _consolidate(df: pd.DataFrame) -> pd.DataFrame:
    """Resolve each holding through the ticker map and merge share classes.

    Mapped holdings are keyed by group FIGI, so Samsung common and preference
    shares - or TSMC as two funds spell it - become one line, shown under the
    FT name of the group's own security (the common share). Unmapped ones
    keep their FT name as the key, as before.
    """
    if df.empty:
        return df.assign(key=pd.Series(dtype=str))
    keys, tickers, parent = [], [], []
    for r in df.itertuples():
        hit = etf_repo.resolve_fund_holding(r.ticker, r.name)
        if hit:
            keys.append(hit[0]); tickers.append(hit[1]); parent.append(hit[2])
        else:
            keys.append("NAME:" + str(r.name).strip().upper())
            tickers.append(r.ticker or ""); parent.append(True)
    df = df.assign(key=keys, ticker=tickers, _parent=parent,
                   name=df["name"].astype(str).str.strip())
    # Parent rows first so "first" picks the common share's name and ticker.
    df = df.sort_values("_parent", ascending=False, kind="stable")
    out = (df.groupby("key", sort=False)
             .agg(name=("name", "first"), ticker=("ticker", "first"),
                  weight_pct=("weight_pct", "sum"))
             .reset_index()
             .sort_values("weight_pct", ascending=False)
             .reset_index(drop=True))
    out.insert(0, "rank", range(1, len(out) + 1))
    return out[["rank", "name", "ticker", "weight_pct", "key"]]


def unmapped_fund_names(limit: int = 40) -> pd.DataFrame:
    """Latest pension-fund holdings the ticker map cannot resolve, heaviest first."""
    df = db.query("""
        SELECT h.fund_name, h.name, h.ticker, h.weight_pct
        FROM pension_holdings h
        JOIN (SELECT fund_id, MAX(scraped_date) AS d FROM pension_holdings
              GROUP BY fund_id) l
          ON l.fund_id = h.fund_id AND l.d = h.scraped_date
        WHERE h.weight_pct > 0
    """)
    if df.empty:
        return df
    miss = [etf_repo.resolve_fund_holding(r.ticker, r.name) is None
            for r in df.itertuples()]
    df = df[miss]
    if df.empty:
        return df
    return (df.groupby(["name", "ticker"], dropna=False, sort=False)
              .agg(weight_pct=("weight_pct", "max"),
                   funds=("fund_name", lambda s: ", ".join(sorted(set(s)))))
              .reset_index()
              .sort_values("weight_pct", ascending=False)
              .head(limit))


# --- Composites ----------------------------------------------------------

def composite_holdings(composite_id: str,
                       as_of: str | None = None) -> tuple[pd.DataFrame, str | None]:
    """
    Blend a composite's components by their allocation weights.

    Returns the blended holdings and the effective date, which is the OLDEST
    of the components' effective dates - the composite is only as current as
    its stalest input.
    """
    comp = universe.composite(composite_id)
    if not comp:
        return pd.DataFrame(columns=["rank", "name", "ticker", "weight_pct"]), None

    merged: dict[str, dict] = {}
    dates: list[str] = []
    shares = _value_shares(comp["components"], as_of)

    for component in comp["components"]:
        df, date = fund_holdings(component["fund_id"], as_of)
        if df.empty:
            continue
        dates.append(date)
        share = shares[component["fund_id"]]
        for row in df.itertuples():
            if not row.weight_pct:
                continue
            entry = merged.setdefault(row.key, {"name": row.name,
                                                "weight": 0.0,
                                                "ticker": row.ticker or ""})
            entry["weight"] += share * row.weight_pct

    if not merged:
        return pd.DataFrame(columns=["rank", "name", "ticker", "weight_pct"]), None

    out = (pd.DataFrame([{"name": v["name"], "ticker": v["ticker"],
                          "weight_pct": round(v["weight"], 2)}
                         for v in merged.values()])
           .sort_values("weight_pct", ascending=False)
           .reset_index(drop=True))
    out.insert(0, "rank", range(1, len(out) + 1))
    return out, (min(dates) if dates else None)


def _price_on(fund_id: str, as_of: str | None):
    """Latest close on or before as_of (latest overall if None)."""
    if as_of:
        df = db.query("SELECT close FROM prices WHERE fund_id = ? AND date <= ? "
                      "ORDER BY date DESC LIMIT 1", (fund_id, as_of))
    else:
        df = db.query("SELECT close FROM prices WHERE fund_id = ? "
                      "ORDER BY date DESC LIMIT 1", (fund_id,))
    return float(df["close"].iloc[0]) if not df.empty else None


def _value_shares(components: list[dict], as_of: str | None) -> dict[str, float]:
    """Each component's share of the composite's VALUE, summing to 1.

    A composite weight multiplies the component's GBP price (see
    importers/composites.py), so it is not a percentage of value. The value
    share is weight x GBP price / sum of the same. Falls back to the weights
    themselves, normalised, if any component cannot be priced.
    """
    fx_ids = {"USD": "YF:GBPUSD=X", "TRY": "YF:GBPTRY=X"}
    values = {}
    for c in components:
        fid = c["fund_id"]
        meta = db.query("SELECT currency, price_unit FROM instruments "
                        "WHERE fund_id = ?", (fid,))
        cur = (meta["currency"].iloc[0] if not meta.empty else None) or "GBP"
        unit = (meta["price_unit"].iloc[0] if not meta.empty else None) or "pound"
        price = _price_on(fid, as_of)
        if price is None:
            values = None
            break
        if unit == "pence":
            price /= 100.0
        if cur in fx_ids:
            rate = _price_on(fx_ids[cur], as_of)
            if not rate:
                values = None
                break
            price /= rate
        values[fid] = float(c["weight"]) * price

    if values and sum(values.values()) > 0:
        total = sum(values.values())
        return {k: v / total for k, v in values.items()}

    total_w = sum(float(c["weight"]) for c in components) or 1.0
    return {c["fund_id"]: float(c["weight"]) / total_w for c in components}


# --- Unified selector ----------------------------------------------------

def options() -> list[dict]:
    """
    Dropdown options across composites, pension funds and ETFs.

    Values are prefixed so one callback can handle all three:
    COMPOSITE:<id>, FUND:<id>, ETF:<fund_id>
    """
    out = [{"label": f"Composite - {c['display_name']}",
            "value": f"COMPOSITE:{c['fund_id'].replace('COMPOSITE:', '')}"}
           for c in universe.composites()]

    funds = list_funds()
    out += [{"label": f"Fund - {r.fund_name}", "value": f"FUND:{r.fund_id}"}
            for r in funds.itertuples()]

    names = universe.etf_names()
    out += [{"label": f"ETF - {names.get(e, e)}", "value": f"ETF:{e}"}
            for e in etf_repo.list_etfs()]
    return out


def holdings_for(selection: str,
                 as_of: str | None = None) -> tuple[pd.DataFrame, str, str | None]:
    """
    Resolve any prefixed selection to (holdings, display_name, effective_date).

    Holdings columns: rank, name, ticker, weight_pct.
    """
    empty = pd.DataFrame(columns=["rank", "name", "ticker", "weight_pct"])
    if not selection:
        return empty, "-", None

    kind, _, ident = selection.partition(":")

    if kind == "COMPOSITE":
        df, date = composite_holdings(ident, as_of)
        comp = universe.composite(ident)
        return df, (comp["display_name"] if comp else ident), date

    if kind == "FUND":
        df, date = fund_holdings(ident, as_of)
        if df.empty:
            return empty, ident, None
        name = db.query("SELECT fund_name FROM pension_holdings "
                        "WHERE fund_id = ? LIMIT 1", (ident,))
        return df, (name["fund_name"].iloc[0] if not name.empty else ident), date

    if kind == "ETF":
        date = _etf_effective_date(ident, as_of)
        if not date:
            return empty, universe.label(ident), None
        df = etf_repo.holdings(ident, date)
        out = df[["name", "weight_pct"]].copy()
        out["ticker"] = df["canonical_id"].astype(str).str[:14]
        out.insert(0, "rank", range(1, len(out) + 1))
        return out[["rank", "name", "ticker", "weight_pct"]], universe.label(ident), date

    return empty, "-", None


def _etf_effective_date(etf_id: str, as_of: str | None) -> str | None:
    if as_of:
        df = db.query("SELECT MAX(scraped_date) AS d FROM etf_holdings "
                      "WHERE etf_fund_id = ? AND scraped_date <= ?",
                      (etf_id, as_of))
        return df["d"].iloc[0] if not df.empty else None
    return etf_repo.latest_date(etf_id)


def date_bounds() -> tuple[str | None, str | None]:
    """Earliest and latest snapshot dates across ETFs and pension funds."""
    df = db.query(
        "SELECT MIN(d) AS lo, MAX(d) AS hi FROM ("
        "  SELECT MIN(scraped_date) AS d FROM etf_holdings "
        "  UNION ALL SELECT MAX(scraped_date) FROM etf_holdings "
        "  UNION ALL SELECT MIN(scraped_date) FROM pension_holdings "
        "  UNION ALL SELECT MAX(scraped_date) FROM pension_holdings)")
    return (df["lo"].iloc[0], df["hi"].iloc[0]) if not df.empty else (None, None)
