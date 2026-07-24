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
        return pd.DataFrame(columns=["rank", "name", "ticker", "weight_pct"]), None
    df = db.query(
        "SELECT rank, name, ticker, weight_pct FROM pension_holdings "
        "WHERE fund_id = ? AND scraped_date = ? ORDER BY rank", (fund_id, date))
    return df, date


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

    for component in comp["components"]:
        df, date = fund_holdings(component["fund_id"], as_of)
        if df.empty:
            continue
        dates.append(date)
        for row in df.itertuples():
            if not row.weight_pct:
                continue
            key = str(row.name).strip()
            entry = merged.setdefault(key, {"weight": 0.0, "ticker": row.ticker or ""})
            entry["weight"] += component["weight"] * row.weight_pct

    if not merged:
        return pd.DataFrame(columns=["rank", "name", "ticker", "weight_pct"]), None

    out = (pd.DataFrame([{"name": k, "ticker": v["ticker"],
                          "weight_pct": round(v["weight"], 2)}
                         for k, v in merged.items()])
           .sort_values("weight_pct", ascending=False)
           .reset_index(drop=True))
    out.insert(0, "rank", range(1, len(out) + 1))
    return out, (min(dates) if dates else None)


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
