"""
Write operations: transactions and cash.

Deliberately separate from core/repo/portfolio.py, which only reads. Every
function here changes data, so keeping them in one small module makes them
easy to audit and hard to call by accident.

Holdings are kept in step automatically: any insert, edit or delete of a BUY
or SELL recalculates that fund's units from its whole transaction history,
the same way the legacy dashboard did on every entry. Dividends do not affect
units and so do not trigger a recalculation.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from core import db

FX_PAIRS = {"USD": "YF:GBPUSD=X", "TRY": "YF:GBPTRY=X"}
FLAT_COMMISSION = {"GBP": 4.0, "USD": 5.5}


# --- Prefills -------------------------------------------------------------

def price_on(fund_id: str, on_date: str):
    """Close for a fund on or before a date, in its own native unit.

    On or before rather than exactly on, so a weekend or holiday entry picks
    up the previous session rather than coming back empty.
    """
    if not fund_id or not on_date:
        return None
    df = db.query("""
        SELECT close FROM prices
        WHERE fund_id = ? AND date <= ? ORDER BY date DESC LIMIT 1
    """, (fund_id, on_date))
    return float(df["close"].iloc[0]) if not df.empty else None


def fx_on(currency: str, on_date: str) -> float:
    """
    GBP cross rate for a currency on or before a date.

    This is stored on the transaction and fixes its cost basis permanently, so
    it wants the rate that applied at the time - not today's. Getting it from
    the price history removes the most error-prone field in the form.
    """
    if not currency or currency.upper() in ("GBP", "GBPC"):
        return 1.0
    pair = FX_PAIRS.get(currency.upper())
    if not pair:
        return 1.0
    df = db.query("""
        SELECT close FROM prices
        WHERE fund_id = ? AND date <= ? ORDER BY date DESC LIMIT 1
    """, (pair, on_date))
    return round(float(df["close"].iloc[0]), 6) if not df.empty else 1.0


def units_held(fund_id: str, account: str | None = None) -> float:
    """
    Net units from the transaction ledger, optionally within one account.

    Used to prefill the quantity on a SELL, which both saves typing and stops
    the oversell that would otherwise pass unnoticed.
    """
    sql = ["""
        SELECT COALESCE(SUM(CASE WHEN type = 'BUY'  THEN quantity
                                 WHEN type = 'SELL' THEN -quantity
                                 ELSE 0 END), 0) AS units
        FROM transactions WHERE fund_id = ?
    """]
    params = [fund_id]
    if account:
        sql.append("AND account = ?")
        params.append(account)
    df = db.query("\n".join(sql), tuple(params))
    return float(df["units"].iloc[0]) if not df.empty else 0.0


def stored_units(fund_id: str) -> float:
    """
    Units as actually held in portfolio_holdings.

    Different from units_held, which is the raw ledger sum and can go negative
    if a sell exceeds what was bought. recalc_holding floors the stored figure
    at zero and removes the row entirely when nothing is left, so this is what
    to quote back to the user after a save.
    """
    df = db.query("SELECT units FROM portfolio_holdings WHERE fund_id = ?",
                  (fund_id,))
    return float(df["units"].iloc[0]) if not df.empty else 0.0


def default_commission(currency: str) -> float:
    """The flat broker fee, by currency. Editable in the form - this is only
    the starting value, and it is what you pay on most trades."""
    return FLAT_COMMISSION.get((currency or "GBP").upper(), 0.0)


# --- Transactions ---------------------------------------------------------

def recent_transactions(limit: int = 30) -> pd.DataFrame:
    df = db.query("""
        SELECT t.id, t.trade_date, t.account, t.fund_id, t.type, t.quantity,
               t.price, t.currency, t.fx_rate,
               COALESCE(t.commission, 0) AS commission,
               COALESCE(i.name, t.fund_id) AS name, i.price_unit
        FROM transactions t
        LEFT JOIN instruments i ON i.fund_id = t.fund_id
        ORDER BY t.trade_date DESC, t.id DESC LIMIT ?
    """, (limit,))
    if not df.empty:
        df["trade_date"] = pd.to_datetime(df["trade_date"])
    return df


def transaction(txn_id) -> dict | None:
    # Ids arriving from a DataFrame are numpy integers, which SQLite matches
    # against nothing at all - silently, returning no row rather than raising.
    # An edit or delete would then look like it worked while changing nothing.
    df = db.query("""
        SELECT id, trade_date, account, fund_id, type, quantity, price,
               currency, fx_rate, COALESCE(commission, 0) AS commission
        FROM transactions WHERE id = ?
    """, (int(txn_id),))
    return df.iloc[0].to_dict() if not df.empty else None


def add_transaction(fund_id, account, trade_date, ttype, quantity, price,
                    currency, fx_rate, commission=0.0) -> None:
    """Insert one transaction and bring the holding back in step."""
    # A dividend records the cash received in `quantity`; price is not
    # meaningful, so it is pinned at 1 as the legacy form did.
    if ttype == "DIVIDEND":
        price = 1.0
    db.execute("""
        INSERT INTO transactions
            (fund_id, account, trade_date, type, quantity, price, currency,
             fx_rate, commission)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (fund_id, account or "", trade_date, ttype, float(quantity),
          float(price), currency or "GBP", float(fx_rate or 1.0),
          float(commission or 0.0)))
    if ttype != "DIVIDEND":
        recalc_holding(fund_id)


def update_transaction(txn_id, fund_id, account, trade_date, ttype, quantity,
                       price, currency, fx_rate, commission=0.0) -> None:
    """Edit a transaction. If the fund changed, both funds are recalculated -
    the old one loses these units and the new one gains them."""
    txn_id = int(txn_id)
    before = transaction(txn_id)
    if ttype == "DIVIDEND":
        price = 1.0
    db.execute("""
        UPDATE transactions
        SET fund_id = ?, account = ?, trade_date = ?, type = ?, quantity = ?,
            price = ?, currency = ?, fx_rate = ?, commission = ?
        WHERE id = ?
    """, (fund_id, account or "", trade_date, ttype, float(quantity),
          float(price), currency or "GBP", float(fx_rate or 1.0),
          float(commission or 0.0), txn_id))

    for fid in {fund_id, (before or {}).get("fund_id")}:
        if fid:
            recalc_holding(fid)


def delete_transaction(txn_id) -> str | None:
    """Remove a transaction and recalculate the affected holding."""
    txn_id = int(txn_id)
    before = transaction(txn_id)
    if not before:
        return None
    db.execute("DELETE FROM transactions WHERE id = ?", (txn_id,))
    recalc_holding(before["fund_id"])
    return before["fund_id"]


def recalc_holding(fund_id: str) -> float:
    """
    Rebuild a fund's units in portfolio_holdings from its transactions.

    Port of the legacy recalc: dividends are excluded, the total is floored at
    zero, and a position that nets to nothing is removed rather than stored as
    a zero row.
    """
    df = db.query("""
        SELECT type, quantity FROM transactions
        WHERE fund_id = ? AND type != 'DIVIDEND' ORDER BY trade_date
    """, (fund_id,))
    total = 0.0
    for r in df.itertuples():
        if r.type == "BUY":
            total += float(r.quantity)
        elif r.type == "SELL":
            total -= float(r.quantity)
    total = max(total, 0.0)

    if total > 0:
        db.execute("""
            INSERT OR REPLACE INTO portfolio_holdings (fund_id, units,
                                                       updated_at)
            VALUES (?, ?, ?)
        """, (fund_id, total,
              datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    else:
        db.execute("DELETE FROM portfolio_holdings WHERE fund_id = ?",
                   (fund_id,))
    return total


# --- Cash -----------------------------------------------------------------

def cash_accounts() -> pd.DataFrame:
    return db.query("SELECT id, name, currency, amount FROM cash_accounts "
                    "ORDER BY name, currency")


def add_cash(name: str, currency: str, amount: float) -> None:
    db.execute("""
        INSERT INTO cash_accounts (name, currency, amount) VALUES (?, ?, ?)
    """, (name, currency, float(amount)))


def delete_cash(row_id) -> int:
    return db.execute("DELETE FROM cash_accounts WHERE id = ?",
                      (int(row_id),))
