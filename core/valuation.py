"""
Valuing holdings in GBP.

The legacy dashboard repeated the same twenty-odd lines of "work out what this
holding is worth" in four places - Accounts, Summary, Charts and Portfolio -
each with its own copy of the composite/cash/asset branching. This module has
it once.

Four kinds of holding are priced differently:

  COMPOSITE:  no market price of its own; valued from its components' latest
              prices, weighted per the stored definition
  CASH:       face value, with CASH:TRY quoted as a point series
  ASSET:      face value (a house, for instance)
  everything  latest close converted to GBP by price_unit and currency
  else

No Dash imports, no SQL - just functions over data handed in, so the same code
serves every tab and can be tested directly.
"""

from __future__ import annotations

import pandas as pd

from core import finance as fin
from core.repo import composites as comp_repo
from core.repo import settings as settings_repo


# --- Configuration --------------------------------------------------------
# Composite definitions, account mappings and chart settings used to be read
# out of FTScrapper's config.py by file location, because Pusula is not on
# that import path. They are now rows in the database, editable from
# Data -> Composites and Data -> Config. The function signatures are
# unchanged, so every caller carries on working.

def composite_definitions() -> list:
    """Component weights that let pension and other blended funds be marked
    daily from their underlying prices."""
    return comp_repo.definitions()


def holding_accounts() -> dict:
    """{fund_id: account} for holdings the transaction ledger does not cover."""
    return comp_repo.holding_accounts()


def chart_category_threshold(default: float = 0.02) -> float:
    return settings_repo.get("CHART_CATEGORY_THRESHOLD", default)


def reload_config():
    """Kept so existing callers do not break. Nothing to clear now that the
    definitions come from the database - every read is already current."""
    return None


def holding_price_gbp(fund_id, instruments, price_map, gbpusd, rates,
                      composites=None):
    """
    Latest price of one holding in GBP, or None if it cannot be priced.

    price_map: {fund_id: latest native close}, from finance.latest_price_map.
    composites: list of composite definitions.
    """
    inst = instruments.get(fund_id, {})
    punit = inst.get("price_unit", "pound")
    curr = inst.get("currency", "GBP")

    if fund_id.startswith("COMPOSITE:"):
        if composites is None:
            composites = composite_definitions()
        comp = _composite(fund_id, composites)
        if not comp:
            return None
        return fin.composite_price_gbp(fund_id, comp["components"],
                                       price_map.get, instruments, gbpusd,
                                       rates)

    if fund_id.startswith(("CASH:", "ASSET:")):
        # CASH:TRY is held as a point series, so it needs the TRY cross.
        effective_unit = "point" if fund_id == "CASH:TRY" else punit
        return fin.to_gbp(1.0, effective_unit, curr, gbpusd, rates)

    raw = price_map.get(fund_id)
    if raw is None:
        return None
    return fin.to_gbp(raw, punit, curr, gbpusd, rates)


def value_holdings(holdings, instruments, price_map, gbpusd, rates,
                   composites=None, exclude_cash=True) -> pd.DataFrame:
    """
    Value a set of holdings.

    holdings: DataFrame or list of dicts with fund_id and units.
    exclude_cash: drop CASH: rows, which the tabs handle via cash_accounts
                  instead so the two do not double-count.

    Returns a DataFrame: fund_id, name, asset_type, category, units,
    price_gbp, value. Unpriceable holdings keep a null value rather than being
    dropped, so they stay visible.
    """
    if composites is None:
        composites = composite_definitions()
    if isinstance(holdings, pd.DataFrame):
        rows = holdings.to_dict("records")
    else:
        rows = list(holdings or [])

    out = []
    for h in rows:
        fid = h["fund_id"]
        if exclude_cash and fid.startswith("CASH:"):
            continue
        units = float(h.get("units") or 0)
        inst = instruments.get(fid, {})
        price = holding_price_gbp(fid, instruments, price_map, gbpusd, rates,
                                  composites)
        out.append({
            "fund_id": fid,
            "name": inst.get("name") or fid,
            "asset_type": inst.get("asset_type") or "Other",
            "category": inst.get("category") or "Other",
            "units": units,
            "price_gbp": price,
            "value": (price * units
                      if price is not None and pd.notna(price) else None),
        })
    return pd.DataFrame(out)


def cash_total_gbp(cash_accounts, rates) -> float:
    """Sum cash accounts into GBP. Port of data.calc_cash_total_gbp."""
    total = 0.0
    for acc in _records(cash_accounts):
        amount = float(acc.get("amount") or 0)
        curr = acc.get("currency", "GBP")
        if curr == "GBP":
            total += amount
        elif curr in ("USD", "TRY"):
            total += amount / rates.get(curr, fin.FX_FALLBACK.get(curr, 1.0))
    return total


def cash_to_gbp(amount, currency, rates) -> float:
    if currency == "GBP":
        return float(amount)
    return float(amount) / rates.get(currency,
                                     fin.FX_FALLBACK.get(currency, 1.0))


def clean(value):
    """
    None for anything missing or NaN, otherwise a float.

    NaN is truthy in Python, so `if value:` passes for NaN and a single
    unpriceable holding poisons any sum or percentage it touches - which is
    how one nan holding turned every percentage on the Summary tab into nan%.
    Route values through here before summing or formatting.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f          # f != f is only true for NaN


def total(values) -> float:
    """Sum, skipping missing and NaN entries."""
    return sum(v for v in (clean(x) for x in values) if v is not None)


def _composite(fund_id, composites):
    for c in (composites or []):
        if c.get("fund_id") == fund_id:
            return c
    return None


def _records(data):
    if isinstance(data, pd.DataFrame):
        return data.to_dict("records")
    return list(data or [])
