"""
Composite definitions and holding-account mappings.

These used to live as two dicts in FTScrapper's config.py, loaded by file
location because Pusula is not on that import path. They are now rows in
`composites` and `composite_components`, editable from Data -> Composites.

definitions() deliberately returns the same shape config.COMPOSITE_FUNDS did:

    [{"fund_id": ..., "display_name": ..., "asset_type": ...,
      "components": [{"fund_id": ..., "weight": ...}, ...]}, ...]

so importers/composites.py and core/valuation.py consume it unchanged.
"""

from __future__ import annotations

from core import db


# --- Reads ---------------------------------------------------------------

def definitions() -> list[dict]:
    """
    Composites that can actually be priced, i.e. those with components.

    ASSET:HOUSE lives in the same table so it has somewhere to carry its
    account, but it is a leaf with no components. Excluding it here keeps the
    price importer's behaviour identical to the old config list.
    """
    comps = db.query("""
        SELECT fund_id, display_name, asset_type
        FROM composites ORDER BY fund_id
    """)
    parts = db.query("""
        SELECT composite_fund_id, component_fund_id, weight
        FROM composite_components ORDER BY composite_fund_id, component_fund_id
    """)

    out = []
    for c in comps.to_dict("records"):
        rows = parts[parts["composite_fund_id"] == c["fund_id"]]
        if rows.empty:
            continue
        out.append({
            "fund_id": c["fund_id"],
            "display_name": c["display_name"],
            "asset_type": c["asset_type"] or "Fund",
            "components": [{"fund_id": r["component_fund_id"],
                            "weight": float(r["weight"])}
                           for r in rows.to_dict("records")],
        })
    return out


def holding_accounts() -> dict:
    """{fund_id: account} for holdings the transaction ledger does not cover."""
    df = db.query("""
        SELECT fund_id, account FROM composites
        WHERE account IS NOT NULL AND account <> ''
    """)
    return dict(zip(df["fund_id"], df["account"]))


def all_composites():
    """Every row, house included - what the editor grid shows."""
    return db.query("""
        SELECT fund_id, display_name, asset_type, account
        FROM composites ORDER BY asset_type, fund_id
    """)


def components(composite_fund_id: str):
    return db.query("""
        SELECT c.component_fund_id, c.weight, i.name
        FROM composite_components c
        LEFT JOIN instruments i ON i.fund_id = c.component_fund_id
        WHERE c.composite_fund_id = ?
        ORDER BY c.weight DESC
    """, (composite_fund_id,))


def weight_totals():
    """Per composite, so the editor can flag anything not summing to 1.0."""
    return db.query("""
        SELECT composite_fund_id, SUM(weight) AS total, COUNT(*) AS n
        FROM composite_components GROUP BY composite_fund_id
    """)


# --- Writes --------------------------------------------------------------

def upsert_composite(fund_id, display_name, asset_type="Fund", account=None):
    return db.execute("""
        INSERT INTO composites (fund_id, display_name, asset_type, account)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(fund_id) DO UPDATE SET
            display_name = excluded.display_name,
            asset_type   = excluded.asset_type,
            account      = excluded.account
    """, (fund_id, display_name, asset_type or "Fund", account or None))


def delete_composite(fund_id):
    db.execute("DELETE FROM composite_components WHERE composite_fund_id = ?",
               (fund_id,))
    return db.execute("DELETE FROM composites WHERE fund_id = ?", (fund_id,))


def replace_components(composite_fund_id, rows):
    """
    rows: [{"fund_id": ..., "weight": ...}, ...]

    Delete-then-insert in one transaction: a component can be removed as well
    as changed, and an UPDATE-only path would silently leave orphans behind.
    """
    from core.db import get_conn
    with get_conn() as conn:
        conn.execute("DELETE FROM composite_components "
                     "WHERE composite_fund_id = ?", (composite_fund_id,))
        for r in rows:
            fid = (r.get("fund_id") or "").strip()
            if not fid:
                continue
            try:
                w = float(r.get("weight") or 0)
            except (TypeError, ValueError):
                continue
            conn.execute("""
                INSERT OR REPLACE INTO composite_components
                    (composite_fund_id, component_fund_id, weight)
                VALUES (?, ?, ?)
            """, (composite_fund_id, fid, w))
    return len(rows)
