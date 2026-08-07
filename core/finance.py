"""
Pure financial calculations for the Portfolio section.

Everything here is a plain function of its inputs - no database, no Dash, no
global state. That makes each piece testable in isolation and reusable across
the P&L, Portfolio, Transactions and Summary tabs.

These are faithful ports of the logic in the legacy data.py, with one
addition: transaction commission. Commission increases the effective cost of
a BUY and reduces the proceeds of a SELL, so it always reduces P&L. Historic
transactions have no commission recorded and are treated as 0, leaving their
P&L unchanged from the legacy figures.

Parity: tests/test_finance_parity.py feeds the same live inputs to these
functions and to data.py's originals and asserts the outputs match to the
penny (commission held at 0 so the comparison is like-for-like).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd


# --- FX -------------------------------------------------------------------

FX_FALLBACK = {"USD": 1.26, "TRY": 43.0}


def fx_rates(prices: pd.DataFrame) -> dict:
    """Latest GBP cross rates from the price frame. {'USD': r, 'TRY': r}.

    Mirrors data.get_fx_rates: reads YF:GBPUSD=X and YF:GBPTRY=X, falling
    back to fixed rates when a pair is missing.
    """
    out = {}
    usd = prices[prices["fund_id"] == "YF:GBPUSD=X"].sort_values("date")
    out["USD"] = usd.iloc[-1]["close"] if not usd.empty else FX_FALLBACK["USD"]
    try_ = prices[prices["fund_id"] == "YF:GBPTRY=X"].sort_values("date")
    out["TRY"] = try_.iloc[-1]["close"] if not try_.empty else FX_FALLBACK["TRY"]
    return out


def gbpusd(prices: pd.DataFrame) -> float:
    return fx_rates(prices)["USD"]


def to_gbp(price, price_unit, currency, gbpusd_rate, rates=None):
    """Convert a live/native price to GBP pounds. Port of data.to_gbp.

    Returns None for units that can't be converted (point without TRY, ratio).
    """
    if price is None:
        return None
    if price_unit == "pence":
        price = price / 100
    if price_unit == "point":
        if currency == "TRY" and rates:
            price = price / rates["TRY"]
        else:
            return None
    elif price_unit == "ratio":
        return None
    if currency == "USD":
        price = price / (rates["USD"] if rates else gbpusd_rate)
    return price


def txn_price_to_gbp(price, txn_currency, txn_fx_rate, price_unit="pound"):
    """Convert a transaction's entered price to GBP pounds. Port of
    data.txn_price_to_gbp - uses the fx_rate stored on the transaction, not
    the current market rate, so historic cost basis is fixed at trade time."""
    p = float(price)
    fx = float(txn_fx_rate) if txn_fx_rate else 1.0
    c = str(txn_currency or "GBP").strip().upper()
    if price_unit == "pence" and c == "GBP":
        p = p / 100
    if c in ("GBP", "GBPC"):
        return p
    if c == "USD":
        return p / fx
    if c in ("XAU", "TRY"):
        return p / fx
    return p


def commission_to_gbp(commission, txn_currency, txn_fx_rate) -> float:
    """Commission converted to GBP pounds at the transaction's fx_rate.

    Commission is a cash charge (never priced in pence), so it converts like a
    plain amount: GBP as-is, otherwise divided by the stored fx_rate. Missing
    or zero commission returns 0.0, which is the historic default.
    """
    if commission is None:
        return 0.0
    amt = float(commission)
    if amt == 0:
        return 0.0
    fx = float(txn_fx_rate) if txn_fx_rate else 1.0
    c = str(txn_currency or "GBP").strip().upper()
    if c in ("GBP", "GBPC"):
        return amt
    return amt / fx


def latest_price(prices: pd.DataFrame, fund_id: str):
    """Most recent close for one fund. Port of data.get_latest_price."""
    f = prices[prices["fund_id"] == fund_id]
    if f.empty:
        return None
    return f.loc[f["date"].idxmax(), "close"]


def latest_price_map(prices: pd.DataFrame) -> dict:
    """Latest close for *every* fund in one pass: {fund_id: close}.

    The legacy code called get_latest_price per row, rescanning the whole
    194k-row frame each time (the Transactions and P&L slowness). Building the
    map once turns those scans into dict lookups.
    """
    if prices.empty:
        return {}
    idx = prices.sort_values("date").groupby("fund_id")["close"].last()
    return idx.to_dict()


# --- Cost-basis / P&L -----------------------------------------------------

def composite_price_gbp(fund_id, components, price_lookup, instruments,
                        gbpusd_rate, rates) -> float | None:
    """Weighted latest GBP price of a composite's components.

    price_lookup(fund_id) -> native latest price (pass latest_price_map.get or
    a partial over the price frame). Port of the COMPOSITE branch in calc_pnl.
    """
    weighted = 0.0
    for c in components:
        cp = price_lookup(c["fund_id"])
        ci = instruments.get(c["fund_id"], {})
        cgbp = to_gbp(cp, ci.get("price_unit", "pence"),
                      ci.get("currency", "GBP"), gbpusd_rate, rates)
        if cgbp:
            weighted += cgbp * c["weight"]
    return weighted if weighted > 0 else None


def position_pnl(txns: pd.DataFrame, current_price_gbp) -> dict:
    """
    Realised/unrealised/dividend P&L for one fund from its transactions.

    txns: rows for a single fund, trade-date ascending, with columns
        type, quantity, price, currency, fx_rate, price_unit, and optionally
        commission (absent -> treated as 0).
    current_price_gbp: latest price in GBP pounds, or None if unpriceable.

    Weighted-average-cost method, matching legacy calc_pnl:
      - BUY adds qty and cost (cost includes commission).
      - DIVIDEND reduces cost basis (floored at 0) and accrues separately.
      - SELL realises against running average cost; sell commission reduces
        the realised gain.

    Returns a dict with qty, avg_cost, cost_basis, current_value, realised,
    dividends, unrealised, pnl, pnl_pct - or None-ish values where undefined.
    """
    total_qty = 0.0
    total_cost = 0.0
    realised = 0.0
    dividends = 0.0

    for r in txns.itertuples():
        qty = float(r.quantity)
        price = float(r.price)
        ttype = r.type
        punit = getattr(r, "price_unit", "pound")
        comm = commission_to_gbp(getattr(r, "commission", 0.0),
                                 r.currency, r.fx_rate)
        cost_per_unit = txn_price_to_gbp(price, r.currency, r.fx_rate, punit)

        if ttype == "BUY":
            total_qty += qty
            total_cost += qty * cost_per_unit + comm      # commission raises cost
        elif ttype == "DIVIDEND":
            div_gbp = txn_price_to_gbp(qty, r.currency, r.fx_rate, "pound")
            total_cost = max(total_cost - div_gbp, 0)
            dividends += div_gbp
        elif ttype == "SELL":
            if total_qty > 0:
                avg = total_cost / total_qty
                sell_qty = min(qty, total_qty)
                realised += sell_qty * (cost_per_unit - avg) - comm  # comm cuts gain
                total_cost -= sell_qty * avg
                total_qty = max(total_qty - sell_qty, 0)

    avg_cost = total_cost / total_qty if total_qty > 0 else 0.0

    if total_qty > 0 and current_price_gbp:
        current_value = current_price_gbp * total_qty
        unrealised = current_value - total_cost
    else:
        current_value = None
        unrealised = None

    if unrealised is not None:
        pnl = realised + unrealised + dividends
    elif realised != 0 or dividends != 0:
        pnl = realised + dividends
    else:
        pnl = None

    basis_for_pct = total_cost + abs(realised) + dividends
    pnl_pct = (pnl / basis_for_pct * 100) if (pnl is not None and basis_for_pct > 0) else None

    return {
        "qty": total_qty,
        "avg_cost": avg_cost,
        "cost_basis": total_cost,
        "current_value": current_value,
        "realised": realised,
        "dividends": dividends,
        "unrealised": unrealised,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }


def ytd_date() -> str:
    d = datetime(datetime.now().year - 1, 12, 31)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# --- Returns (vectorised) -------------------------------------------------

def returns_map(prices: pd.DataFrame, fund_ids=None) -> dict:
    """
    Period returns for many funds in one pass: {fund_id: {'1D':.., '1W':..}}.

    Replaces per-row calc_return calls (each of which rescanned the whole
    price frame). Periods: 1D=1, 1W=5, 1M=21, 3M=63 calendar-day lookbacks,
    plus YTD from last year's final weekday. Percentages.

    Every fund is measured from the same date - the latest in the frame - not
    from its own last price. Anchoring per fund meant a fund that last
    published on Thursday computed its "1D" against Wednesday and showed it
    beside funds measuring Friday against Thursday: different periods in the
    same column. A fund with no price at the anchor date returns None for 1D
    rather than a stale figure dressed as a daily move.
    """
    periods = {"1D": 1, "1W": 5, "1M": 21, "3M": 63}
    ytd = pd.Timestamp(ytd_date())
    df = prices if fund_ids is None else prices[prices["fund_id"].isin(fund_ids)]
    if df.empty:
        return {}

    anchor = df["date"].max()
    out = {}
    for fid, g in df.sort_values("date").groupby("fund_id"):
        closes = g.set_index("date")["close"]

        # The latest close at or before the anchor, so a fund that did not
        # trade today is still measured to today rather than to its own last
        # print.
        at_anchor = closes[closes.index <= anchor]
        if at_anchor.empty:
            out[fid] = {k: None for k in list(periods) + ["YTD"]}
            continue
        last = at_anchor.iloc[-1]
        last_date = at_anchor.index[-1]

        r = {}
        for label, days in periods.items():
            cutoff = anchor - timedelta(days=days)
            prior = closes[closes.index <= cutoff]
            if label == "1D" and last_date <= cutoff:
                # The fund has not priced since the comparison point, so there
                # is no one-day move to report - only a gap.
                r[label] = None
                continue
            r[label] = (round((last / prior.iloc[-1] - 1) * 100, 2)
                        if not prior.empty and prior.iloc[-1] else None)

        prior_ytd = closes[closes.index <= ytd]
        r["YTD"] = (round((last / prior_ytd.iloc[-1] - 1) * 100, 2)
                    if not prior_ytd.empty and prior_ytd.iloc[-1] else None)
        out[fid] = r
    return out
