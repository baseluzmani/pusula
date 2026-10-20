"""
Your own levels per instrument, for the Markets -> Monitor tab.

    buy_target    a price you would buy at
    sell_target   a price you would sell at
    review_level  your invalidation price: at or below it, the thesis needs
                  re-reading
    note          free text - the invalidation condition in words

Prices are in the instrument's quote units, the same units as its closes in
the prices table (pence for a .L listing, dollars for a US stock), so a level
is typed the way the price is quoted.

The table is created on first use. Any field can be left empty; a row with
every field empty is deleted rather than kept.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from core import db

COLUMNS = ["fund_id", "buy_target", "sell_target", "review_level", "note",
           "updated_at"]


def ensure_table() -> None:
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mkt_targets (
                fund_id      TEXT PRIMARY KEY,
                buy_target   REAL,
                sell_target  REAL,
                review_level REAL,
                note         TEXT,
                updated_at   TEXT
            )
        """)


def all_targets(fund_ids=None) -> pd.DataFrame:
    """Targets for the given instruments (all when None). Columns: COLUMNS."""
    ensure_table()
    sql = f"SELECT {', '.join(COLUMNS)} FROM mkt_targets"
    params: tuple = ()
    if fund_ids:
        sql += f" WHERE fund_id IN ({','.join('?' for _ in fund_ids)})"
        params = tuple(fund_ids)
    return db.query(sql, params)


def get(fund_id: str) -> dict | None:
    df = all_targets([fund_id])
    if df.empty:
        return None
    return df.iloc[0].to_dict()


def save(fund_id: str, buy_target=None, sell_target=None,
         review_level=None, note=None) -> str:
    """Insert or replace one instrument's levels. Empty values are stored as
    NULL; if everything is empty the row is removed. Returns "saved" or
    "cleared"."""
    vals = [_num(buy_target), _num(sell_target), _num(review_level)]
    note = (note or "").strip() or None
    if all(v is None for v in vals) and note is None:
        clear(fund_id)
        return "cleared"
    ensure_table()
    db.execute("""
        INSERT INTO mkt_targets
            (fund_id, buy_target, sell_target, review_level, note, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(fund_id) DO UPDATE SET
            buy_target = excluded.buy_target,
            sell_target = excluded.sell_target,
            review_level = excluded.review_level,
            note = excluded.note,
            updated_at = excluded.updated_at
    """, (fund_id, *vals, note, datetime.now().strftime("%Y-%m-%d %H:%M")))
    return "saved"


def clear(fund_id: str) -> int:
    ensure_table()
    return db.execute("DELETE FROM mkt_targets WHERE fund_id = ?", (fund_id,))


def _num(v):
    """A positive number, or None for blank / zero / unparseable input."""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None
