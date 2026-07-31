"""
Standing data: accounts and instruments.

These are the reference tables - the things that exist, as opposed to the
things that happen. Both are edited directly in the Data section rather than
by hand in SQL, which is how they were maintained before.

Writes are deliberately explicit: the pages collect edits and call save_*
once, so nothing is written while you are still typing.
"""

from __future__ import annotations

import pandas as pd

from core import db


# --- Accounts -------------------------------------------------------------

ACCOUNT_COLUMNS = ("name", "owner", "wrapper", "sort_order", "active")


def accounts(active_only: bool = False) -> pd.DataFrame:
    """All accounts. Columns: id, name, owner, wrapper, sort_order, active."""
    sql = "SELECT id, name, owner, wrapper, sort_order, active FROM accounts"
    if active_only:
        sql += " WHERE active = 1"
    sql += " ORDER BY sort_order, name"
    return db.query(sql)


def account_options(active_only: bool = True) -> list:
    """Dropdown options for the transaction form."""
    df = accounts(active_only=active_only)
    if df.empty:
        return []
    return [{"label": r.name, "value": r.name} for r in df.itertuples()]


def save_accounts(rows: list) -> int:
    """
    Upsert a list of account dicts. Rows with an id are updated; rows without
    one are inserted. Returns the number of rows written.

    Names are unique, so renaming an account to an existing name is rejected
    by the database rather than silently merging two accounts.
    """
    written = 0
    with db.get_conn() as conn:
        for r in rows:
            name = (r.get("name") or "").strip()
            if not name:
                continue
            values = (name, _clean(r.get("owner")), _clean(r.get("wrapper")),
                      _int(r.get("sort_order")), _int(r.get("active"), 1))
            if r.get("id"):
                conn.execute("""
                    UPDATE accounts
                    SET name = ?, owner = ?, wrapper = ?, sort_order = ?,
                        active = ?
                    WHERE id = ?
                """, values + (r["id"],))
            else:
                conn.execute("""
                    INSERT OR IGNORE INTO accounts
                        (name, owner, wrapper, sort_order, active)
                    VALUES (?, ?, ?, ?, ?)
                """, values)
            written += 1
        conn.commit()
    return written


def delete_account(account_id: int) -> int:
    """Remove an account. Transactions keep their account name as text, so
    deleting here only removes it from the dropdown - no history is lost."""
    return db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))


def account_usage() -> dict:
    """{account_name: transaction_count} so the page can warn before deleting
    an account that is still referenced."""
    df = db.query("""
        SELECT account AS name, COUNT(*) AS n FROM transactions
        WHERE account IS NOT NULL GROUP BY account
    """)
    return dict(zip(df["name"], df["n"])) if not df.empty else {}


# --- Instruments ----------------------------------------------------------

INSTRUMENT_COLUMNS = ("name", "asset_type", "currency", "price_unit",
                      "category", "active", "source", "source_id", "provider")


def instruments(search: str = "", incomplete_only: bool = False,
                held_only: bool = False) -> pd.DataFrame:
    """
    Instruments for the editing grid.

    incomplete_only: rows missing currency or price_unit. Those are the ones
        that misprice silently - to_gbp matches neither branch, so the price
        is treated as GBP pounds and passes through unconverted.
    held_only: only instruments with an open position or any transaction.
    """
    sql = ["""
        SELECT i.fund_id, i.name, i.asset_type, i.currency, i.price_unit,
               i.category, COALESCE(i.active, 1) AS active,
               i.source, i.source_id, i.provider,
               (SELECT COUNT(*) FROM prices p WHERE p.fund_id = i.fund_id)
                   AS price_rows,
               (SELECT MAX(p.date) FROM prices p WHERE p.fund_id = i.fund_id)
                   AS last_price
        FROM instruments i
        WHERE 1 = 1
    """]
    params = []

    if search:
        sql.append("AND (LOWER(i.name) LIKE ? OR LOWER(i.fund_id) LIKE ?)")
        params += [f"%{search.lower()}%"] * 2
    if incomplete_only:
        sql.append("AND (i.currency IS NULL OR i.currency = '' "
                   "OR i.price_unit IS NULL OR i.price_unit = '')")
    if held_only:
        sql.append("""
            AND (i.fund_id IN (SELECT fund_id FROM portfolio_holdings
                               WHERE units > 0)
                 OR i.fund_id IN (SELECT DISTINCT fund_id FROM transactions))
        """)
    sql.append("ORDER BY i.name")
    return db.query("\n".join(sql), tuple(params))


def save_instruments(rows: list) -> int:
    """Update instrument metadata. fund_id is the key and is never changed
    here - re-keying an instrument would orphan its prices and transactions."""
    written = 0
    with db.get_conn() as conn:
        for r in rows:
            fund_id = r.get("fund_id")
            if not fund_id:
                continue
            conn.execute("""
                UPDATE instruments
                SET name = ?, asset_type = ?, currency = ?, price_unit = ?,
                    category = ?, active = ?, source = ?, source_id = ?,
                    provider = ?
                WHERE fund_id = ?
            """, (_clean(r.get("name")), _clean(r.get("asset_type")),
                  _clean(r.get("currency")), _clean(r.get("price_unit")),
                  _clean(r.get("category")), _int(r.get("active"), 1),
                  _clean(r.get("source")), _clean(r.get("source_id")),
                  _clean(r.get("provider")), fund_id))
            written += 1
        conn.commit()
    return written


def instrument_choices() -> dict:
    """Existing distinct values, to offer as dropdown options rather than
    forcing free text and risking typos."""
    out = {}
    for col in ("asset_type", "currency", "price_unit", "category"):
        df = db.query(f"SELECT DISTINCT {col} AS v FROM instruments "
                      f"WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}")
        out[col] = df["v"].tolist() if not df.empty else []
    return out


# --- helpers --------------------------------------------------------------

def _clean(value):
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ("nan", "none") else None


def _int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def create_instrument(ticker: str, source: str) -> tuple[bool, str]:
    """
    Create a bare instrument row from a ticker and a source.

    Only the identifying fields are set - everything else is filled in on the
    grid afterwards. That is deliberate: name, currency and price_unit are
    easier to get right once the row exists and can be seen alongside its
    neighbours.

    fund_id is derived from the source, because the prefix is what every
    importer and pricing branch keys on:
        yahoo      YF:TICKER
        ft         the FT identifier as-is, which is already unique
        composite  COMPOSITE:NAME
        manual     as given, so CASH: and ASSET: rows can be added
    """
    ticker = (ticker or "").strip()
    source = (source or "").strip().lower()
    if not ticker:
        return False, "A ticker or identifier is required."
    if source not in ("yahoo", "ft", "composite", "manual"):
        return False, f"Unknown source '{source}'."

    if source == "yahoo":
        fund_id = f"YF:{ticker}"
    elif source == "composite":
        fund_id = ticker if ticker.startswith("COMPOSITE:") \
            else f"COMPOSITE:{ticker}"
    else:
        fund_id = ticker

    existing = db.query("SELECT name FROM instruments WHERE fund_id = ?",
                        (fund_id,))
    if not existing.empty:
        return False, f"{fund_id} already exists."

    # source_id is what the importer asks Yahoo or FT for. Without it the row
    # would sit in the table and never fetch a price.
    source_id = ticker if source in ("yahoo", "ft") else None
    db.execute("""
        INSERT INTO instruments (fund_id, name, source, source_id, active)
        VALUES (?, ?, ?, ?, 1)
    """, (fund_id, ticker, source, source_id))
    return True, (f"Created {fund_id}. Fill in name, currency, price unit and "
                  f"category below, then Save.")
