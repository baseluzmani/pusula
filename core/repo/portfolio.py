"""
Database access for the Portfolio section.

All SQL lives here; callers get DataFrames or plain dicts and never touch a
connection. Reads are the focus for now (P&L, Charts, Summary). Write
operations (holdings upsert, transaction insert, cash accounts) are added as
their tabs are built, each tested against a throwaway DB copy first.

Prices are served through here too, but the actual price maths (latest price,
composites, FX) stays in core.finance so it is testable without a database.
"""

from __future__ import annotations

import pandas as pd

from core import db


# --- Instruments ----------------------------------------------------------

def instruments() -> dict:
    """All instruments keyed by fund_id -> metadata dict. Mirrors
    data.load_instruments so downstream code is unchanged."""
    df = db.query("SELECT fund_id, name, asset_type, currency, price_unit, "
                  "category FROM instruments")
    out = {}
    for r in df.itertuples():
        out[r.fund_id] = {
            "name": r.name, "asset_type": r.asset_type, "currency": r.currency,
            "price_unit": r.price_unit, "category": r.category or "\u2014",
        }
    return out


# --- Prices ---------------------------------------------------------------

def prices() -> pd.DataFrame:
    """Raw price history joined to instrument name/type. Columns:
    fund_id, fund_name, asset_type, date, open, high, low, close, volume."""
    df = db.query("""
        SELECT p.fund_id, i.name AS fund_name, i.asset_type, p.date,
               p.open, p.high, p.low, p.close, p.volume
        FROM prices p
        LEFT JOIN instruments i ON i.fund_id = p.fund_id
        ORDER BY p.fund_id, p.date
    """)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# --- Transactions ---------------------------------------------------------

def transactions() -> pd.DataFrame:
    """All transactions joined to instrument metadata, fund/date ordered.

    Includes a commission column: if the transactions table has no commission
    column yet (pre-migration), it is synthesised as 0.0 so downstream P&L is
    unchanged. Once the column is added, real values flow through.
    """
    has_comm = _has_column("transactions", "commission")
    comm_sql = "t.commission" if has_comm else "0.0 AS commission"
    df = db.query(f"""
        SELECT t.fund_id, t.account, t.trade_date, t.type, t.quantity, t.price,
               t.currency, t.fx_rate, {comm_sql},
               i.name, i.price_unit, i.category
        FROM transactions t
        LEFT JOIN instruments i ON i.fund_id = t.fund_id
        ORDER BY t.fund_id, t.trade_date
    """)
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def transactions_filtered(funds=None, date_from=None, date_to=None,
                          txn_type=None) -> pd.DataFrame:
    """Transactions for the Transactions tab, newest first, with optional
    filters. Same commission handling as transactions()."""
    has_comm = _has_column("transactions", "commission")
    comm_sql = "t.commission" if has_comm else "0.0 AS commission"
    sql = [f"""
        SELECT t.fund_id, t.trade_date, t.type, t.quantity, t.price,
               t.currency, t.fx_rate, {comm_sql}, i.name, i.price_unit
        FROM transactions t
        LEFT JOIN instruments i ON i.fund_id = t.fund_id
        WHERE 1 = 1
    """]
    params = []
    if funds:
        sql.append(f"AND t.fund_id IN ({','.join('?' * len(funds))})")
        params += list(funds)
    if date_from:
        sql.append("AND t.trade_date >= ?"); params.append(date_from)
    if date_to:
        sql.append("AND t.trade_date <= ?"); params.append(date_to)
    if txn_type and txn_type != "ALL":
        sql.append("AND t.type = ?"); params.append(txn_type)
    sql.append("ORDER BY t.trade_date DESC, t.fund_id")
    df = db.query("\n".join(sql), tuple(params))
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def transaction_fund_ids() -> list:
    df = db.query("SELECT DISTINCT fund_id FROM transactions ORDER BY fund_id")
    return df["fund_id"].tolist() if not df.empty else []


def transaction_fund_options() -> list:
    """Dropdown options for the Transactions filter: every fund that has been
    traded, labelled with its instrument name."""
    df = db.query("""
        SELECT DISTINCT t.fund_id, i.name
        FROM transactions t LEFT JOIN instruments i ON i.fund_id = t.fund_id
        ORDER BY COALESCE(i.name, t.fund_id)
    """)
    if df.empty:
        return []
    return [{"label": r.name or r.fund_id, "value": r.fund_id}
            for r in df.itertuples()]


# --- Holdings -------------------------------------------------------------

def holdings() -> pd.DataFrame:
    """portfolio_holdings rows. Columns: fund_id, units."""
    return db.query("SELECT fund_id, units FROM portfolio_holdings "
                    "ORDER BY fund_id")


# --- Cash accounts --------------------------------------------------------

def cash_accounts() -> pd.DataFrame:
    """Cash accounts. Columns depend on schema; typically id, name, currency,
    amount."""
    return db.query("SELECT * FROM cash_accounts ORDER BY currency, name")


# --- helpers --------------------------------------------------------------

def _has_column(table: str, column: str) -> bool:
    info = db.query(f"PRAGMA table_info({table})")
    return not info.empty and column in info["name"].values


# --- Snapshots ------------------------------------------------------------

def snapshot_options() -> list:
    """Dropdown options for the Summary comparison picker, newest first."""
    df = db.query("SELECT snap_date FROM portfolio_snapshots "
                  "ORDER BY snap_date DESC")
    options = [{"label": "None", "value": "none"}]
    for r in df.itertuples():
        options.append({"label": pd.Timestamp(r.snap_date).strftime("%d %b %Y"),
                        "value": r.snap_date})
    return options


def latest_snapshot_date() -> str | None:
    df = db.query("SELECT snap_date FROM portfolio_snapshots "
                  "ORDER BY snap_date DESC LIMIT 1")
    return df["snap_date"].iloc[0] if not df.empty else None


def snapshot_values(snap_date: str) -> dict:
    """{fund_id: value_gbp} frozen at that snapshot, or {} if unknown."""
    if not snap_date or snap_date == "none":
        return {}
    df = db.query("""
        SELECT sh.fund_id, sh.value_gbp
        FROM snapshot_holdings sh
        JOIN portfolio_snapshots ps ON ps.id = sh.snapshot_id
        WHERE ps.snap_date = ?
    """, (snap_date,))
    if df.empty:
        return {}
    return dict(zip(df["fund_id"], df["value_gbp"]))


def snapshot_category_history() -> pd.DataFrame:
    """Category value per snapshot date, for the stacked-area chart.
    Columns: date, category, value_gbp."""
    df = db.query("""
        SELECT ps.snap_date AS date, sc.category, sc.value_gbp
        FROM snapshot_categories sc
        JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
        ORDER BY ps.snap_date
    """)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def networth_history() -> pd.DataFrame:
    """Columns: date, value."""
    df = db.query("SELECT date, total_gbp AS value FROM networth_history "
                  "ORDER BY date")
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


# --- Per-account positions ------------------------------------------------

def account_positions() -> pd.DataFrame:
    """
    Net quantity still held per account and fund, derived from transactions.

    Only open positions (net quantity above a rounding tolerance). Columns:
    account, fund_id, name, units.
    """
    return db.query("""
        SELECT t.account, t.fund_id, i.name,
               SUM(CASE WHEN t.type='BUY'  THEN t.quantity ELSE 0 END) -
               SUM(CASE WHEN t.type='SELL' THEN t.quantity ELSE 0 END) AS units
        FROM transactions t
        LEFT JOIN instruments i ON i.fund_id = t.fund_id
        GROUP BY t.account, t.fund_id
        HAVING units > 0.0001
        ORDER BY t.account, i.name
    """)


def snapshot_category_values(snap_date: str) -> dict:
    """
    {category: value_gbp} frozen at that snapshot.

    Read from snapshot_categories rather than derived from holdings, because
    the stored rows preserve how things were categorised at the time. Deriving
    them would silently rewrite history whenever a fund is recategorised.
    """
    if not snap_date or snap_date == "none":
        return {}
    df = db.query("""
        SELECT sc.category, SUM(sc.value_gbp) AS value_gbp
        FROM snapshot_categories sc
        JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
        WHERE ps.snap_date = ?
        GROUP BY sc.category
    """, (snap_date,))
    return dict(zip(df["category"], df["value_gbp"])) if not df.empty else {}


def snapshot_asset_type_values(snap_date: str) -> dict:
    """
    {asset_type: value_gbp} at that snapshot.

    Snapshots do not store asset type, so this maps the frozen holdings onto
    today's instrument metadata. Asset types change far less often than
    categories, so the approximation is safe - but it is an approximation.
    """
    if not snap_date or snap_date == "none":
        return {}
    # CASH: rows are snapshot aggregates with no instrument row to join to,
    # so they would fall into "Other" and fail to line up with the current
    # figures, where cash is its own asset type.
    df = db.query("""
        SELECT CASE WHEN sh.fund_id LIKE 'CASH:%' THEN 'Cash'
                    ELSE COALESCE(i.asset_type, 'Other') END AS asset_type,
               SUM(sh.value_gbp) AS value_gbp
        FROM snapshot_holdings sh
        JOIN portfolio_snapshots ps ON ps.id = sh.snapshot_id
        LEFT JOIN instruments i ON i.fund_id = sh.fund_id
        WHERE ps.snap_date = ?
        GROUP BY 1
    """, (snap_date,))
    return dict(zip(df["asset_type"], df["value_gbp"])) if not df.empty else {}
