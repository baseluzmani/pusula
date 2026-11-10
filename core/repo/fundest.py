"""
Data for the ETF -> Day estimate tab: which funds are estimated, what they
hold, and the prices that move them. No Dash, no arithmetic beyond shaping -
core/fundest.py does the estimating.

Two small tables, created on first use. SEMI and the HSBC composites are
seeded once, while the targets table is empty; the futures baskets in
BASKETS are added at start-up whenever their target row is missing, so a
basket added in a later release appears without touching existing rows. To
hide a basket, set active = 0 rather than deleting its row (a deleted row
comes back with the default weights).

    etf_est_targets   one row per estimated fund
        target            selection key, as funds.holdings_for takes it -
                          ETF:YF:SEMI.L, COMPOSITE:HSBC_SHARIA - or
                          BASKET:<name> for a fund priced from a basket
        label             shown on the page
        kind              'holdings' (look-through) or 'basket'
        actual_fund_id    the fund's own price series, for the backtest
        residual_fund_id  optional proxy for the weight the holdings do not
                          cover. Empty = the covered names are scaled up.
        hedged            1 = judge the fund before FX (a GBP-hedged class)
        sort_order, active

    etf_est_basket    target, fund_id, weight (percent)
        Components of a 'basket' target: a swap-based commodity ETF/ETC with
        no holdings, estimated from Yahoo front-month futures.
          UC14  UBS CMCI Composite, approximate sector weights
          AIGA  WisdomTree Agriculture = Bloomberg Agriculture Subindex,
                WisdomTree's published benchmark weights
        Edit the weights here; the backtest on the page shows how well each
        basket tracks the product's own price.
"""

from __future__ import annotations

import pandas as pd

from core import db
from core.repo import funds as funds_repo

# UBS CMCI Composite, approximated with Yahoo front-month futures. Sector
# targets: energy ~37, agriculture ~28, industrial metals ~25, precious ~6,
# livestock ~4. Copper stands in for the whole industrial-metals sector
# (aluminium, zinc, nickel and lead have no reliable Yahoo future).
UC14_BASKET = [
    ("YF:BZ=F", 13.0), ("YF:CL=F", 12.0), ("YF:NG=F", 6.0),
    ("YF:HO=F", 3.0), ("YF:RB=F", 3.0),
    ("YF:HG=F", 25.0),
    ("YF:GC=F", 5.0), ("YF:SI=F", 1.0),
    ("YF:ZS=F", 7.0), ("YF:ZC=F", 6.0), ("YF:ZW=F", 5.0), ("YF:SB=F", 3.0),
    ("YF:KC=F", 2.0), ("YF:CT=F", 2.0), ("YF:ZM=F", 1.5), ("YF:ZL=F", 1.5),
    ("YF:LE=F", 2.5), ("YF:HE=F", 1.5),
]

# WisdomTree Agriculture (AIGA.L / AGAP.L) tracks the Bloomberg Commodity
# Agriculture Subindex 4W TR. Weights are WisdomTree's benchmark composition
# as of 3 Jul 2026; BCOM resets to target weights each January and drifts
# with prices in between. Front-month futures stand in for BCOM's contracts.
AIGA_BASKET = [
    ("YF:ZS=F", 19.42), ("YF:ZC=F", 15.18), ("YF:ZL=F", 15.13),
    ("YF:ZM=F", 9.67), ("YF:KC=F", 9.46), ("YF:ZW=F", 9.06),
    ("YF:SB=F", 8.39), ("YF:KE=F", 6.14), ("YF:CT=F", 5.17),
    ("YF:CC=F", 2.37),
]

# Futures baskets: target -> (label, actual price series, sort order, rows).
BASKETS = {
    "BASKET:UC14": ("UC14 - UBS CMCI Composite (futures basket)",
                    "YF:UC14.L", 90, UC14_BASKET),
    "BASKET:AIGA": ("AIGA - WisdomTree Agriculture (futures basket)",
                    "YF:AIGA.L", 91, AIGA_BASKET),
}

# Names for new futures rows. Yahoo's own carry the contract month
# ("Corn Futures,Dec-2026"), which is stale after the first roll.
FUTURE_NAMES = {
    "YF:BZ=F": "Brent crude (front month)", "YF:CL=F": "WTI crude (front month)",
    "YF:NG=F": "Natural gas (front month)", "YF:HO=F": "Heating oil (front month)",
    "YF:RB=F": "RBOB gasoline (front month)", "YF:HG=F": "Copper (front month)",
    "YF:GC=F": "Gold (front month)", "YF:SI=F": "Silver (front month)",
    "YF:ZS=F": "Soybeans (front month)", "YF:ZC=F": "Corn (front month)",
    "YF:ZW=F": "Wheat (front month)", "YF:SB=F": "Sugar No.11 (front month)",
    "YF:KC=F": "Coffee (front month)", "YF:CT=F": "Cotton (front month)",
    "YF:ZM=F": "Soybean meal (front month)", "YF:ZL=F": "Soybean oil (front month)",
    "YF:LE=F": "Live cattle (front month)", "YF:HE=F": "Lean hogs (front month)",
    "YF:KE=F": "KC HRW wheat (front month)", "YF:CC=F": "Cocoa (front month)",
}

_DDL = """
CREATE TABLE IF NOT EXISTS etf_est_targets (
    target            TEXT PRIMARY KEY,
    label             TEXT NOT NULL,
    kind              TEXT NOT NULL DEFAULT 'holdings',
    actual_fund_id    TEXT,
    residual_fund_id  TEXT,
    hedged            INTEGER NOT NULL DEFAULT 0,
    sort_order        INTEGER NOT NULL DEFAULT 50,
    active            INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS etf_est_basket (
    target    TEXT NOT NULL,
    fund_id   TEXT NOT NULL,
    weight    REAL NOT NULL,
    PRIMARY KEY (target, fund_id)
);
"""

_ready = False


def ensure_tables() -> None:
    """Create the two tables, seed SEMI and the composites while the targets
    table is empty, and add any default basket whose target row is missing.
    Existing rows are never changed."""
    global _ready
    if _ready:
        return
    with db.get_conn() as conn:
        conn.executescript(_DDL)
        empty = conn.execute(
            "SELECT COUNT(*) FROM etf_est_targets").fetchone()[0] == 0
        if empty:
            _seed(conn)
        _add_missing_baskets(conn)
    _ready = True


def _seed(conn) -> None:
    rows = [("ETF:YF:SEMI.L", "SEMI - iShares Global Semiconductors",
             "holdings", "YF:SEMI.L", None, 0, 10)]
    comps = conn.execute(
        "SELECT fund_id, display_name FROM composites "
        "WHERE fund_id LIKE 'COMPOSITE:HSBC%' ORDER BY display_name"
    ).fetchall()
    for i, (fid, name) in enumerate(comps):
        rows.append((fid, name or fid, "holdings", fid, None, 0, 20 + i))
    conn.executemany("""
        INSERT OR IGNORE INTO etf_est_targets
            (target, label, kind, actual_fund_id, residual_fund_id, hedged,
             sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)


def _add_missing_baskets(conn) -> None:
    have = {r[0] for r in conn.execute("SELECT target FROM etf_est_targets")}
    for key, (label, actual, order, parts) in BASKETS.items():
        if key in have:
            continue
        conn.execute("""
            INSERT INTO etf_est_targets
                (target, label, kind, actual_fund_id, sort_order)
            VALUES (?, ?, 'basket', ?, ?)
        """, (key, label, actual, order))
        conn.execute("DELETE FROM etf_est_basket WHERE target = ?", (key,))
        conn.executemany(
            "INSERT INTO etf_est_basket (target, fund_id, weight) "
            "VALUES (?, ?, ?)", [(key, f, w) for f, w in parts])


def targets() -> pd.DataFrame:
    """Active targets in page order."""
    ensure_tables()
    return db.query("""
        SELECT target, label, kind, actual_fund_id, residual_fund_id,
               COALESCE(hedged, 0) AS hedged
        FROM etf_est_targets
        WHERE COALESCE(active, 1) = 1
        ORDER BY sort_order, label
    """)


def target(key: str) -> dict | None:
    df = targets()
    hit = df[df["target"] == key]
    return hit.iloc[0].to_dict() if not hit.empty else None


def basket(key: str) -> pd.DataFrame:
    ensure_tables()
    return db.query("SELECT fund_id, weight FROM etf_est_basket "
                    "WHERE target = ? ORDER BY weight DESC", (key,))


def constituents(row: dict, as_of: str | None = None
                 ) -> tuple[pd.DataFrame, str | None]:
    """
    What a target holds: name, fund_id (YF:<yahoo id>, None if there is no
    Yahoo symbol), weight_pct, and ticker - the ticker the holdings show
    (the FT ticker for a pension holding the map does not know), so a
    symbol that could not be built can be traced. And the holdings date.

    Look-through targets read the same holdings the Fund compare tab shows,
    so a fix made in the Ticker map reaches both.
    """
    cols = ["name", "fund_id", "weight_pct", "ticker"]
    if row["kind"] == "basket":
        b = basket(row["target"])
        if b.empty:
            return pd.DataFrame(columns=cols), None
        names = instrument_meta(b["fund_id"].tolist())
        label = dict(zip(names["fund_id"], names["name"])) \
            if not names.empty else {}
        b["name"] = [label.get(f) or (f[3:] if f.startswith("YF:") else f)
                     for f in b["fund_id"]]
        b["ticker"] = b["fund_id"]
        return (b.rename(columns={"weight": "weight_pct"})[cols], None)

    df, _, date = funds_repo.holdings_for(row["target"], as_of)
    if df.empty and as_of:
        # Asked about a day before the first snapshot: the nearest weights
        # are a better answer than none.
        df, _, date = funds_repo.holdings_for(row["target"], None)
    if df.empty:
        return pd.DataFrame(columns=cols), date
    yahoo = df["yahoo_id"] if "yahoo_id" in df.columns \
        else pd.Series([None] * len(df))
    out = pd.DataFrame({
        "name": df["name"].astype(str),
        "fund_id": [yahoo_fund_id(y) for y in yahoo],
        "weight_pct": pd.to_numeric(df["weight_pct"], errors="coerce"),
        "ticker": [_text(t) or "" for t in df["ticker"]]
        if "ticker" in df.columns else "",
    })
    out = out[out["weight_pct"].fillna(0) > 0]
    # Two lines that resolve to the same Yahoo symbol (share classes the map
    # has not grouped) are one position for pricing purposes.
    priced = out[out["fund_id"].notna()]
    if priced["fund_id"].duplicated().any():
        merged = (priced.groupby("fund_id", sort=False)
                        .agg(name=("name", "first"),
                             weight_pct=("weight_pct", "sum"),
                             ticker=("ticker", "first"))
                        .reset_index())
        out = pd.concat([merged[cols], out[out["fund_id"].isna()][cols]],
                        ignore_index=True)
    return (out.sort_values("weight_pct", ascending=False)
               .reset_index(drop=True)[cols], date)


def instrument_meta(fund_ids: list[str]) -> pd.DataFrame:
    """fund_id, name, currency, price_unit for the ids that exist."""
    ids = sorted({f for f in fund_ids if f})
    if not ids:
        return pd.DataFrame(columns=["fund_id", "name", "currency",
                                     "price_unit"])
    marks = ",".join("?" * len(ids))
    return db.query(f"""
        SELECT fund_id, name, currency, price_unit FROM instruments
        WHERE fund_id IN ({marks})
    """, tuple(ids))


def closes(fund_ids: list[str], since: str) -> pd.DataFrame:
    """Long frame fund_id, date, close for dates on or after ``since``."""
    ids = sorted({f for f in fund_ids if f})
    if not ids:
        return pd.DataFrame(columns=["fund_id", "date", "close"])
    marks = ",".join("?" * len(ids))
    return db.query(f"""
        SELECT fund_id, date, close FROM prices
        WHERE fund_id IN ({marks}) AND date >= ? AND close > 0
        ORDER BY fund_id, date
    """, (*ids, since))


def yahoo_fund_id(yahoo_id) -> str | None:
    """The instruments fund_id for a ticker-map yahoo_id. The map stores it
    already prefixed ("YF:AMD", as importers/figi.py writes it); a bare
    "AMD" typed in by hand is accepted too."""
    text = _text(yahoo_id)
    if not text:
        return None
    return text if text.upper().startswith("YF:") else f"YF:{text}"


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ("nan", "none") else None
