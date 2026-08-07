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

def prices(since: str = None, fund_ids=None) -> pd.DataFrame:
    """Raw price history joined to instrument name/type. Columns:
    fund_id, fund_name, asset_type, date, open, high, low, close, volume.

    since and fund_ids bound the read. The table carries every instrument
    tracked, including indices and commodities held only as indicators, while
    most callers want a handful of funds they actually own - so filtering in
    SQL against the index beats loading 212k rows and discarding most of them.
    """
    sql = """
        SELECT p.fund_id, i.name AS fund_name, i.asset_type, p.date,
               p.open, p.high, p.low, p.close, p.volume
        FROM prices p
        LEFT JOIN instruments i ON i.fund_id = p.fund_id
    """
    where, params = [], []
    if since:
        where.append("p.date >= ?")
        params.append(since)
    if fund_ids:
        ids = list(fund_ids)
        where.append(f"p.fund_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    if where:
        sql += " WHERE " + " AND ".join(where)

    df = db.query(sql + " ORDER BY p.fund_id, p.date", tuple(params))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df

def latest_prices() -> pd.DataFrame:
    """One row per fund: its most recent close.

    Every page did this in pandas over the full 212k-row price frame, which
    prices() loads with a join and no filter. The two consumers -
    latest_price_map and fx_rates - reduce that to about 160 values and throw
    the rest away, so the work is better done where the index already is.

    Columns: fund_id, date, close.
    """
    return db.query("""
        SELECT p.fund_id, p.date, p.close
        FROM prices p
        JOIN (SELECT fund_id, MAX(date) AS d FROM prices GROUP BY fund_id) m
          ON m.fund_id = p.fund_id AND m.d = p.date
    """)

def price_series(fund_id: str, since: str = None) -> pd.DataFrame:
    """One fund's closes, date-indexed. Columns: date, close.

    For callers that want a single series rather than the whole table -
    the Indicators page was loading all 212k rows three times per render to
    filter each one down to a few hundred.
    """
    sql = "SELECT date, close FROM prices WHERE fund_id = ?"
    params = [fund_id]
    if since:
        sql += " AND date >= ?"
        params.append(since)
    return db.query(sql + " ORDER BY date", tuple(params))
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
        WHERE ps.snap_date = ? AND sh.fund_id NOT LIKE 'CASH:%'
        UNION ALL
        -- Cash lives in snapshot_cash, not snapshot_holdings. It used to be
        -- written to both, which double-counted every total from 24 April
        -- until the history rebuild; now it has one home and is folded back
        -- in here under the id the Portfolio tab uses for it.
        SELECT 'CASH:TOTAL', SUM(sc.value_gbp)
        FROM snapshot_cash sc
        JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
        WHERE ps.snap_date = ?
        HAVING SUM(sc.value_gbp) IS NOT NULL
    """, (snap_date, snap_date))
    if df.empty:
        return {}
    return dict(zip(df["fund_id"], df["value_gbp"]))


def snapshot_category_history() -> pd.DataFrame:
    """
    Category value per snapshot date, for the stacked-area chart.

    Derived from holdings and current instrument metadata for the same reason
    as snapshot_category_values: a renamed category would otherwise split the
    history in two, the same money appearing under both names.

    Cash comes from snapshot_cash. It used to be written into
    snapshot_holdings as well, which double-counted every total from 24 April
    until the history rebuild; the rebuild removed those rows, which is why
    cash vanished from this chart and why 3 May showed a spike that was never
    real money.
    """
    return db.query("""
        SELECT date, category, SUM(value_gbp) AS value_gbp FROM (
            SELECT ps.snap_date AS date,
                   COALESCE(i.category, 'Other') AS category,
                   sh.value_gbp
            FROM snapshot_holdings sh
            JOIN portfolio_snapshots ps ON ps.id = sh.snapshot_id
            LEFT JOIN instruments i ON i.fund_id = sh.fund_id
            WHERE sh.fund_id NOT LIKE 'CASH:%'
            UNION ALL
            SELECT ps.snap_date, 'Cash', sc.value_gbp
            FROM snapshot_cash sc
            JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
        )
        GROUP BY date, category
        ORDER BY date, category
    """)


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

    Positions whose net quantity is not zero, in either direction. The test is
    on the absolute value: a closed position nets to zero and is excluded, but
    a liability is held short - the mortgage sits at -1 unit against the house
    - and a positive-only test dropped it, understating the account total by
    the whole outstanding balance.

    Columns: account, fund_id, name, units.
    """
    return db.query("""
        SELECT t.account, t.fund_id, i.name,
               SUM(CASE WHEN t.type='BUY'  THEN t.quantity ELSE 0 END) -
               SUM(CASE WHEN t.type='SELL' THEN t.quantity ELSE 0 END) AS units
        FROM transactions t
        LEFT JOIN instruments i ON i.fund_id = t.fund_id
        GROUP BY t.account, t.fund_id
        HAVING ABS(units) > 0.0001
        ORDER BY t.account, i.name
    """)


def snapshot_category_values(snap_date: str) -> dict:
    """
    {category: value_gbp} at that snapshot, using today's categorisation.

    Derived from the frozen holdings joined to current instrument metadata,
    rather than read from snapshot_categories. The stored rows preserve the
    category *name* as it was, which sounds right until you rename one:
    renaming "UK Passive" to "UK Equity" then splits into two rows, history
    under the old name and today under the new, and the same money appears
    twice.

    A stored row cannot distinguish renaming a category from moving a fund
    between categories, so it gets the rename case wrong. Deriving means the
    whole history is presented under whatever taxonomy you use now - which is
    also how the by-asset-type panel already worked, so the two now agree.
    """
    if not snap_date or snap_date == "none":
        return {}
    df = db.query("""
        SELECT category, SUM(value_gbp) AS value_gbp FROM (
            SELECT COALESCE(i.category, 'Other') AS category,
                   sh.value_gbp
            FROM snapshot_holdings sh
            JOIN portfolio_snapshots ps ON ps.id = sh.snapshot_id
            LEFT JOIN instruments i ON i.fund_id = sh.fund_id
            WHERE ps.snap_date = ? AND sh.fund_id NOT LIKE 'CASH:%'
            UNION ALL
            -- Cash has its own table. It used to be written to
            -- snapshot_holdings as well, which double-counted every total
            -- from 24 April until the history rebuild.
            SELECT 'Cash', sc.value_gbp
            FROM snapshot_cash sc
            JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
            WHERE ps.snap_date = ?
        )
        GROUP BY category
    """, (snap_date, snap_date))
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
        SELECT asset_type, SUM(value_gbp) AS value_gbp FROM (
            SELECT COALESCE(i.asset_type, 'Other') AS asset_type,
                   sh.value_gbp
            FROM snapshot_holdings sh
            JOIN portfolio_snapshots ps ON ps.id = sh.snapshot_id
            LEFT JOIN instruments i ON i.fund_id = sh.fund_id
            WHERE ps.snap_date = ? AND sh.fund_id NOT LIKE 'CASH:%'
            UNION ALL
            SELECT 'Cash', sc.value_gbp
            FROM snapshot_cash sc
            JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
            WHERE ps.snap_date = ?
        )
        GROUP BY asset_type
    """, (snap_date, snap_date))
    return dict(zip(df["asset_type"], df["value_gbp"])) if not df.empty else {}

def snapshot_account_values(snap_date: str) -> dict:
    """
    {account: value_gbp} at that snapshot.

    Snapshots do not store an account, so each frozen holding is apportioned
    across accounts by the units the ledger says were held in each - as at the
    snapshot date, not as at today.

    That distinction matters. Weighting by today's open positions put every
    since-closed holding into Unassigned: 148k of it on 20 June, all of it
    ETFs the ledger still has full account history for. Several spanned three
    or four accounts, so a single fallback account would have misplaced them
    rather than lost them. The split has to be the one in force on the day.

    A holding still in Unassigned after this has no transactions at all before
    the snapshot date, which is a gap in the ledger rather than a mapping
    problem.

    Cash comes from snapshot_cash, whose name column is the account.
    """
    if not snap_date or snap_date == "none":
        return {}

    # Fund -> {account: share of units}, from the ledger as at snap_date.
    pos = db.query("""
        SELECT account, fund_id,
               SUM(CASE WHEN type='BUY'  THEN quantity ELSE 0 END) -
               SUM(CASE WHEN type='SELL' THEN quantity ELSE 0 END) AS units
        FROM transactions
        WHERE type != 'DIVIDEND' AND trade_date <= ?
        GROUP BY account, fund_id
        HAVING ABS(units) > 0.0001
    """, (snap_date,))

    weights = {}
    if not pos.empty:
        totals = pos.groupby("fund_id")["units"].transform("sum")
        pos = pos.assign(w=pos["units"] / totals.replace(0, pd.NA))
        for r in pos.dropna(subset=["w"]).itertuples():
            weights.setdefault(r.fund_id, {})[r.account] = float(r.w)

    df = db.query("""
        SELECT sh.fund_id, sh.value_gbp
        FROM snapshot_holdings sh
        JOIN portfolio_snapshots ps ON ps.id = sh.snapshot_id
        WHERE ps.snap_date = ? AND sh.fund_id NOT LIKE 'CASH:%'
    """, (snap_date,))

    out = {}
    for r in df.itertuples():
        value = float(r.value_gbp or 0)
        split = weights.get(r.fund_id)
        if not split:
            out["Unassigned"] = out.get("Unassigned", 0.0) + value
            continue
        for account, share in split.items():
            out[account] = out.get(account, 0.0) + value * share

    cash = db.query("""
        SELECT sc.name AS account, SUM(sc.value_gbp) AS value_gbp
        FROM snapshot_cash sc
        JOIN portfolio_snapshots ps ON ps.id = sc.snapshot_id
        WHERE ps.snap_date = ?
        GROUP BY sc.name
    """, (snap_date,))
    for r in cash.itertuples():
        out[r.account] = out.get(r.account, 0.0) + float(r.value_gbp or 0)

    return out