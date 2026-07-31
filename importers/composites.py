"""
Composite price series.

Composite funds - your HSBC pension lines - have no market price of their own.
Each is a weighted blend of real funds, rebased to 100 at the first date all
its components share, and that blended series is written to prices like any
other instrument.

Rebasing to 100 means the series tracks relative performance rather than a
notional unit price. That is what makes a composite comparable against its own
history; the absolute level is arbitrary.

Run after a price import, since it reads whatever components have just landed.

Usage
-----
    python3 -m importers.composites
"""

from __future__ import annotations

import pandas as pd

from core import db, valuation as val


def _components_frame(conn) -> pd.DataFrame:
    df = pd.read_sql_query("""
        SELECT fund_id, date, close FROM prices ORDER BY fund_id, date
    """, conn)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _build(df: pd.DataFrame, composites: list) -> pd.DataFrame:
    rows = []
    for comp in composites:
        fund_id = comp["fund_id"]
        series = {}
        for c in comp.get("components", []):
            cdf = (df[df["fund_id"] == c["fund_id"]][["date", "close"]]
                   .sort_values("date"))
            if not cdf.empty:
                series[c["fund_id"]] = cdf.set_index("date")["close"]

        if not series:
            print(f"  {fund_id}: no component prices - skipped")
            continue

        # Only dates every component has, so the blend is never computed from
        # a partial set - which would show as a spurious jump.
        common = None
        for s in series.values():
            common = set(s.index) if common is None else common & set(s.index)
        if not common or len(common) < 2:
            print(f"  {fund_id}: fewer than 2 common dates - skipped")
            continue

        common = sorted(common)
        base_date = common[0]
        blended = pd.Series(0.0, index=common)
        for c in comp.get("components", []):
            cid = c["fund_id"]
            if cid not in series:
                continue
            s = series[cid].loc[common]
            base = s.loc[base_date]
            if base == 0:
                continue
            blended += (s / base) * 100 * c["weight"]

        for date, price in blended.items():
            rows.append({"fund_id": fund_id, "date": date.strftime("%Y-%m-%d"),
                         "close": float(price)})
    return pd.DataFrame(rows)


def _ensure_instruments(conn, composites):
    """A composite needs an instruments row or it cannot be priced or grouped."""
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
        df = _components_frame(conn)
        print(f"  {len(df):,} rows")

        _ensure_instruments(conn, composites)

        print("\nBuilding composite series...")
        built = _build(df, composites)
        if built.empty:
            conn.commit()
            return {"saved": 0, "message": "Nothing built"}

        for fund_id in built["fund_id"].unique():
            sub = built[built["fund_id"] == fund_id]
            print(f"  {fund_id}: {len(sub)} rows, "
                  f"{sub['date'].min()} to {sub['date'].max()}")

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
