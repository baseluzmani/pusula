"""
Composite price series.

Composite funds - the HSBC pension lines - have no market price of their own.
Each is a weighted blend of real funds, and that blend is written to prices
like any other instrument.

What is stored is a real GBP price per unit, so units * price = value on every
screen. It used to be an index rebased to 100 at the first date all components
shared. That was not a price: the Portfolio tab showed 142 for Sharia beside a
value implying 0.0611, because the value came from valuation.holding_price_gbp
and the price column came from this table.

The rebase existed for a reason - it made components dimensionless so a
pence-quoted fund could be added to a pound-quoted one without the mismatch
showing. That is now handled properly instead: each component is converted to
GBP first, using its own currency and price_unit and the FX rate *on that
date*, mirroring core.finance.to_gbp. Using today's rate for a series spanning
years would put an FX error into every historical point.

Returns are unaffected by the change - a rebase cancels out of any percentage
change - so charts keep the same shape. Only the levels become meaningful.

Run after a price import, since it reads whatever components have just landed.

Usage
-----
    python3 -m importers.composites
"""

from __future__ import annotations

import pandas as pd

from core import db, valuation as val

# Yahoo fund_ids for the crosses, as stored in prices.
GBPUSD = "YF:GBPUSD=X"
GBPTRY = "YF:GBPTRY=X"


def _prices_frame(conn) -> pd.DataFrame:
    df = pd.read_sql_query("""
        SELECT fund_id, date, close FROM prices ORDER BY fund_id, date
    """, conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _instrument_meta(conn) -> dict:
    df = pd.read_sql_query("""
        SELECT fund_id, currency, price_unit FROM instruments
    """, conn)
    return {r["fund_id"]: {"currency": r["currency"] or "GBP",
                           "price_unit": r["price_unit"] or "pound"}
            for r in df.to_dict("records")}


def _fx_series(df: pd.DataFrame, fund_id: str) -> pd.Series:
    """Daily rate, forward-filled.

    A component can trade on a day the FX series has no row for - a UK bank
    holiday, or a gap in the Yahoo cross - and the last known rate is a far
    better answer than dropping the date from the blend, which would show as a
    step in the series.
    """
    sub = df[df["fund_id"] == fund_id][["date", "close"]].sort_values("date")
    if sub.empty:
        return pd.Series(dtype=float)
    return sub.set_index("date")["close"]


def _to_gbp_series(series: pd.Series, meta: dict, usd: pd.Series,
                   try_: pd.Series) -> pd.Series | None:
    """One component's native series converted to GBP pounds.

    Mirrors core.finance.to_gbp, applied per date rather than to a single
    latest price. Kept in step with it deliberately: if that function changes,
    this must too, or the price column and the value column will disagree
    again in a different way.
    """
    price_unit = (meta.get("price_unit") or "pound").lower()
    currency = (meta.get("currency") or "GBP").upper()

    out = series.astype(float).copy()

    if price_unit == "pence":
        out = out / 100.0
    elif price_unit == "point":
        if currency == "TRY" and not try_.empty:
            rate = try_.reindex(out.index).ffill().bfill()
            out = out / rate
        else:
            return None            # unconvertible, same as to_gbp
    elif price_unit == "ratio":
        return None

    if currency == "USD":
        if usd.empty:
            return None
        rate = usd.reindex(out.index).ffill().bfill()
        out = out / rate
    elif currency == "TRY" and price_unit != "point":
        if try_.empty:
            return None
        rate = try_.reindex(out.index).ffill().bfill()
        out = out / rate

    return out


def _build(df: pd.DataFrame, composites: list, meta: dict) -> pd.DataFrame:
    usd = _fx_series(df, GBPUSD)
    try_ = _fx_series(df, GBPTRY)

    rows = []
    for comp in composites:
        fund_id = comp["fund_id"]
        components = comp.get("components", [])

        series = {}
        for c in components:
            cid = c["fund_id"]
            sub = df[df["fund_id"] == cid][["date", "close"]].sort_values("date")
            if sub.empty:
                print(f"  {fund_id}: no prices for {cid}")
                continue
            gbp = _to_gbp_series(sub.set_index("date")["close"],
                                 meta.get(cid, {}), usd, try_)
            if gbp is None:
                print(f"  {fund_id}: {cid} cannot be converted to GBP "
                      f"(price_unit/currency) - skipped")
                continue
            series[cid] = gbp

        if not series:
            print(f"  {fund_id}: no usable component prices - skipped")
            continue

        # Only dates every component has, so the blend is never computed from
        # a partial set - which would show as a spurious jump.
        if len(series) != len([c for c in components]):
            print(f"  {fund_id}: only {len(series)}/{len(components)} "
                  f"components usable - weights will not sum to 1")

        common = None
        for s in series.values():
            common = set(s.index) if common is None else common & set(s.index)
        if not common:
            print(f"  {fund_id}: components share no dates - skipped")
            continue

        common = sorted(common)
        blended = pd.Series(0.0, index=common)
        for c in components:
            cid = c["fund_id"]
            if cid not in series:
                continue
            blended += series[cid].loc[common] * float(c["weight"])

        for date, price in blended.items():
            rows.append({"fund_id": fund_id, "date": date.strftime("%Y-%m-%d"),
                         "close": float(price)})
    return pd.DataFrame(rows)


def _ensure_instruments(conn, composites):
    """A composite needs an instruments row or it cannot be priced or grouped.

    price_unit is 'pound' and currency GBP because the series written here is
    already in GBP pounds - no further conversion should be applied to it.
    """
    for comp in composites:
        conn.execute("""
            INSERT OR IGNORE INTO instruments
                (fund_id, name, asset_type, currency, price_unit, category,
                 source)
            VALUES (?, ?, ?, 'GBP', 'pound', 'Pension', 'composite')
        """, (comp["fund_id"], comp.get("display_name", comp["fund_id"]),
              comp.get("asset_type", "Fund")))


def run() -> dict:
    composites = val.composite_definitions()
    if not composites:
        return {"saved": 0, "message": "No composite definitions found"}

    with db.get_conn() as conn:
        print("Loading component prices...")
        df = _prices_frame(conn)
        print(f"  {len(df):,} rows")

        meta = _instrument_meta(conn)
        _ensure_instruments(conn, composites)

        print("\nBuilding composite series...")
        built = _build(df, composites, meta)
        if built.empty:
            conn.commit()
            return {"saved": 0, "message": "Nothing built"}

        for fund_id in built["fund_id"].unique():
            sub = built[built["fund_id"] == fund_id]
            print(f"  {fund_id}: {len(sub)} rows, "
                  f"{sub['date'].min()} to {sub['date'].max()}, "
                  f"last {sub.sort_values('date')['close'].iloc[-1]:.4f}")

        # REPLACE rather than IGNORE: a composite is a derived series, so when
        # a component's price is revised the blend must be recomputed, not
        # left at its first value.
        for r in built.to_dict("records"):
            conn.execute("""
                INSERT OR REPLACE INTO prices
                    (fund_id, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, 0)
            """, (r["fund_id"], r["date"], r["close"], r["close"],
                  r["close"], r["close"]))
        conn.commit()

    print(f"\nDone. {len(built)} composite rows written.")
    return {"saved": len(built),
            "message": f"{len(built)} rows for "
                       f"{built['fund_id'].nunique()} composites"}


if __name__ == "__main__":
    run()