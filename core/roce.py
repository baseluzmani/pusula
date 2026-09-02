"""
core.roce
=========

Return-on-capital-employed engine. Read-only; writes nothing to the database.

Consumed by pages/pnl_analysis.py, and intended to be the single source of
these numbers when the P&L page is rebuilt. Nothing here knows about Dash.


METRIC CONTRACT
---------------
These definitions are the thing the P&L page will later depend on, so they are
fixed here and everything else is derived from them.

    capital_days     Sum over calendar days of the book cost of the open
                     position, in GBP-days. Flat days contribute zero.
    days_deployed    Count of days where book cost > 0.
    avg_capital      capital_days / days_deployed
    peak_capital     max daily book cost
    roce_total       total_pnl / avg_capital
    roce_annualised  365 * total_pnl / capital_days      (simple, not compound)

Total and annualised tie exactly, by construction:

    roce_annualised == roce_total * 365 / days_deployed

Aggregation across instruments:

    basket capital_days   = sum of instrument capital_days
    basket total_pnl      = sum of instrument total_pnl
    basket days_deployed  = days where the SUMMED book cost > 0   (union, not sum)
    basket peak_capital   = max of the SUMMED series              (not sum of peaks)
    basket annualised     = sum(w_i * annualised_i), w_i = capital_days_i / total

The last identity is what makes the contribution column meaningful: an
instrument's pull on the basket rate is exactly its share of capital-days.


COST METHOD
-----------
Weighted average cost. Within a single date, buys are processed before sells.
That is deterministic and it avoids a transient short position on same-day
round trips (e.g. the 2026-06-25 buy 1,000 / sell 2,000 pair in wheat).

The engine always runs over the FULL ledger. There is no date filter on this
page by design: a windowed ROCE needs an opening and closing mark, and the
unrealised P&L at a window start is not zero. That is a separate problem.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------
# 1. DATA ACCESS
# --------------------------------------------------------------------------
# Built against the real schema:
#   transactions(id, fund_id, account, trade_date, type, quantity, price,
#                currency, fx_rate, commission)
#   instruments(fund_id, name, ..., currency, price_unit, category, active, ...)
#   prices(id, fund_id, date, open, high, low, close, volume)
#
# quantity is always positive; direction comes from `type`.
# price is in `currency` and in the unit given by instruments.price_unit, so
# pence-quoted lines (HSBC, BHP, Shell) divide by 100.
# fx_rate is units of `currency` per GBP: GBP cost = native / fx_rate.

# core.config already holds the migrated settings, including DB_PATH. The env
# var and literal are only fallbacks for running the engine outside the app.
try:
    from core import config as _config
    DB_PATH = Path(getattr(_config, "DB_PATH", "")
                   or os.getenv("PUSULA_DB", Path.home() / "data" / "funds.db"))
except ImportError:
    DB_PATH = Path(os.getenv("PUSULA_DB", Path.home() / "data" / "funds.db"))

# Commission: GBP trades carry a flat 4.00, which is clearly sterling. On a
# USD trade it is ambiguous. True = treat it as trade currency and convert.
# Flip if your broker bills commission in GBP regardless.
COMMISSION_IN_TRADE_CCY = True

# price_unit values meaning "minor units" (pence, cents) rather than major.
# Listed explicitly so an unrecognised price_unit shows up as unconverted
# rather than silently wrong. "gbp" and "usd" are major units, not here.
MINOR_UNITS = {"gbx", "p", "pence", "gbp_pence", "usc", "cents"}

# Pseudo-instruments with no cost basis in the sense the engine assumes.
EXCLUDED = {"CASH:TOTAL", "ASSET:HOUSE", "LIABILITY:MORTGAGE"}


def _connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


TRADES_SQL = """
    SELECT t.trade_date                          AS date,
           t.fund_id                             AS instrument,
           COALESCE(i.name, t.fund_id)           AS name,
           UPPER(t.type)                         AS type,
           t.quantity                            AS raw_qty,
           t.price                               AS price,
           UPPER(COALESCE(t.currency, 'GBP'))    AS currency,
           COALESCE(t.fx_rate, 1.0)              AS fx_rate,
           COALESCE(t.commission, 0.0)           AS commission,
           LOWER(COALESCE(i.price_unit, ''))     AS price_unit
    FROM transactions t
    LEFT JOIN instruments i ON i.fund_id = t.fund_id
    WHERE UPPER(t.type) IN ('BUY', 'SELL')
    ORDER BY t.trade_date, t.id
"""


def load_trades() -> pd.DataFrame:
    """Full trade ledger, with a signed GBP cash_flow including commission.

    Returns columns: date, instrument, name, qty (signed), cash_flow (signed:
    negative on a buy, positive on a sell).
    """
    with _connect() as con:
        df = pd.read_sql_query(TRADES_SQL, con)

    df = df[~df["instrument"].isin(EXCLUDED)].copy()
    df["date"] = pd.to_datetime(df["trade_date"] if "trade_date" in df else df["date"])

    df["qty"] = df["raw_qty"].where(df["type"] == "BUY", -df["raw_qty"])

    # Pence/cents -> major units.
    unit = df["price_unit"].str.strip().isin(MINOR_UNITS).map({True: 100.0, False: 1.0})
    fx = df["fx_rate"].replace(0, 1.0).fillna(1.0)

    gross_gbp = df["qty"].abs() * df["price"] / unit / fx
    comm_gbp = df["commission"] / (fx if COMMISSION_IN_TRADE_CCY else 1.0)

    df["cash_flow"] = (-(gross_gbp + comm_gbp)).where(df["qty"] > 0, gross_gbp - comm_gbp)

    return df[["date", "instrument", "name", "qty", "cash_flow"]].dropna(
        subset=["qty", "cash_flow"]
    )


LATEST_PRICE_SQL = """
    SELECT p.fund_id                            AS fund_id,
           p.close                              AS close,
           UPPER(COALESCE(i.currency, 'GBP'))   AS currency,
           LOWER(COALESCE(i.price_unit, ''))    AS price_unit
    FROM prices p
    JOIN (SELECT fund_id, MAX(date) AS d FROM prices GROUP BY fund_id) m
      ON m.fund_id = p.fund_id AND m.d = p.date
    LEFT JOIN instruments i ON i.fund_id = p.fund_id
"""


def load_latest_prices() -> tuple[dict[str, float], list[str]]:
    """Latest close per instrument, converted to GBP.

    Returns (prices, approximated) where `approximated` lists instruments whose
    price was converted using the FX rate from their most recent trade rather
    than a live rate. That is an approximation and the page says so; GBP-quoted
    instruments are exact.
    """
    with _connect() as con:
        px = pd.read_sql_query(LATEST_PRICE_SQL, con)
        fx_rows = pd.read_sql_query(
            """
            SELECT fund_id, currency, fx_rate FROM transactions t1
            WHERE t1.trade_date = (
                SELECT MAX(t2.trade_date) FROM transactions t2
                WHERE t2.fund_id = t1.fund_id
            )
            """,
            con,
        )

    last_fx = (
        fx_rows.dropna(subset=["fx_rate"])
        .drop_duplicates("fund_id", keep="last")
        .set_index("fund_id")["fx_rate"]
        .to_dict()
    )

    unit = px["price_unit"].str.strip().isin(MINOR_UNITS).map({True: 100.0, False: 1.0})
    px["major"] = px["close"] / unit

    out: dict[str, float] = {}
    approximated: list[str] = []
    for row, u in zip(px.itertuples(), px["major"]):
        if row.currency == "GBP":
            out[row.fund_id] = float(u)
        else:
            rate = last_fx.get(row.fund_id)
            if rate:
                out[row.fund_id] = float(u) / float(rate)
                approximated.append(row.fund_id)
            else:
                out[row.fund_id] = 0.0
    return out, approximated


# --------------------------------------------------------------------------
# 2. ENGINE
# --------------------------------------------------------------------------

@dataclass
class Position:
    instrument: str
    name: str
    qty: float
    book_cost: float
    wac: float
    realised: float
    unrealised: float
    total_pnl: float
    daily_book: pd.Series
    capital_days: float
    days_deployed: int
    avg_capital: float
    peak_capital: float
    roce_total: float
    roce_annualised: float
    latest_price: float
    realised_events: pd.Series = field(repr=False, default_factory=pd.Series)

    @property
    def status(self) -> str:
        return "closed" if self.qty == 0 else "open"


def compute_position(
    trades: pd.DataFrame,
    latest_price: float,
    asof: pd.Timestamp,
    instrument: str = "",
    name: str = "",
) -> Position:
    """Run weighted-average-cost over one instrument's full ledger."""
    t = trades.copy()
    # Buys (0) before sells (1) within the same date. See COST METHOD above.
    t["_order"] = (t["qty"] < 0).astype(int)
    t = t.sort_values(["date", "_order"], kind="stable")

    qty = book = realised = cost_of_sold = 0.0
    realised_events: list[tuple[pd.Timestamp, float]] = []
    book_points: list[tuple[pd.Timestamp, float]] = []

    for row in t.itertuples():
        if row.qty > 0:
            book += -row.cash_flow          # cash_flow is negative on a buy
            qty += row.qty
        elif row.qty < 0:
            sell_qty = -row.qty
            if sell_qty > qty + 1e-9:
                raise ValueError(
                    f"{instrument}: sell of {sell_qty:,.0f} on "
                    f"{row.date:%Y-%m-%d} exceeds holding of {qty:,.0f}. "
                    "Ledger is incomplete or an opening balance is missing."
                )
            wac = book / qty
            released = sell_qty * wac
            realised += row.cash_flow - released   # cash_flow positive on a sell
            cost_of_sold += released
            book -= released
            qty -= sell_qty

            realised_events.append((row.date, row.cash_flow - released))

        # Snap float dust to zero so a flat position reads as genuinely flat.
        if abs(qty) < 1e-9:
            qty = book = 0.0

        book_points.append((row.date, book))

    # Daily book-cost series. dict() keeps the LAST value per date, which is
    # exactly the end-of-day book cost we want when a date has several trades.
    bp = pd.Series(dict(book_points))
    bp.index = pd.to_datetime(bp.index)
    idx = pd.date_range(bp.index.min(), asof, freq="D")
    daily = bp.sort_index().reindex(idx, method="ffill").fillna(0.0)

    capital_days = float(daily.sum())
    days_deployed = int((daily > 0).sum())
    avg_capital = capital_days / days_deployed if days_deployed else 0.0
    peak_capital = float(daily.max())

    unrealised = qty * latest_price - book
    total_pnl = realised + unrealised

    return Position(
        instrument=instrument,
        name=name,
        qty=qty,
        book_cost=book,
        wac=book / qty if qty else 0.0,
        realised=realised,
        unrealised=unrealised,
        total_pnl=total_pnl,
        daily_book=daily,
        capital_days=capital_days,
        days_deployed=days_deployed,
        avg_capital=avg_capital,
        peak_capital=peak_capital,
        roce_total=total_pnl / avg_capital if avg_capital else 0.0,
        roce_annualised=365 * total_pnl / capital_days if capital_days else 0.0,
        latest_price=latest_price,
        realised_events=pd.Series(dict(realised_events)) if realised_events else pd.Series(dtype=float),
    )


def compute_all(asof: pd.Timestamp | None = None) -> list[Position]:
    """Run the engine over every instrument in the ledger."""
    trades = load_trades()
    prices, _approx = load_latest_prices()
    asof = pd.Timestamp(asof or pd.Timestamp.today().normalize())

    out = []
    for instrument, grp in trades.groupby("instrument", sort=False):
        out.append(
            compute_position(
                grp,
                latest_price=prices.get(instrument, 0.0),
                asof=asof,
                instrument=instrument,
                name=grp["name"].iloc[-1],
            )
        )
    return sorted(out, key=lambda p: p.capital_days, reverse=True)


@dataclass
class Basket:
    daily_book: pd.Series
    total_pnl: float
    realised: float
    unrealised: float
    capital_days: float
    days_deployed: int
    avg_capital: float
    peak_capital: float
    roce_total: float
    roce_annualised: float
    members: list[Position]


def aggregate(members: list[Position]) -> Basket | None:
    """Pool the daily series first, then compute once. Never average rates."""
    if not members:
        return None

    daily = (
        pd.DataFrame({p.instrument: p.daily_book for p in members})
        .fillna(0.0)
        .sum(axis=1)
        .sort_index()
    )

    capital_days = float(daily.sum())
    days_deployed = int((daily > 0).sum())
    avg_capital = capital_days / days_deployed if days_deployed else 0.0
    total_pnl = sum(p.total_pnl for p in members)

    return Basket(
        daily_book=daily,
        total_pnl=total_pnl,
        realised=sum(p.realised for p in members),
        unrealised=sum(p.unrealised for p in members),
        capital_days=capital_days,
        days_deployed=days_deployed,
        avg_capital=avg_capital,
        peak_capital=float(daily.max()),
        roce_total=total_pnl / avg_capital if avg_capital else 0.0,
        roce_annualised=365 * total_pnl / capital_days if capital_days else 0.0,
        members=members,
    )


# --------------------------------------------------------------------------
# 3. VALIDATION
# --------------------------------------------------------------------------
# Two of these are genuinely independent of the WAC walk. Check 1 reconciles
# P&L straight from the ledger and the latest price without touching cost
# allocation at all, so it will catch an engine run on filtered data.

MIN_DAYS_TO_ANNUALISE = 180


def validate(members: list[Position], basket: Basket, trades: pd.DataFrame) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    # 1. total P&L == market value of what's left + net cash flow, per instrument
    worst = 0.0
    for p in members:
        net_cash = float(trades.loc[trades["instrument"] == p.instrument, "cash_flow"].sum())
        expected = p.qty * p.latest_price + net_cash
        worst = max(worst, abs(expected - p.total_pnl))
    checks.append((
        "P&L reconciles to ledger and mark",
        worst < 0.01,
        f"largest difference £{worst:,.4f}",
    ))

    # 2. capital-days are additive across instruments
    summed = sum(p.capital_days for p in members)
    diff = abs(summed - basket.capital_days)
    checks.append((
        "Capital-days additive",
        diff < 1.0,
        f"£{summed:,.0f} vs £{basket.capital_days:,.0f}",
    ))

    # 3. total and annualised tie, and the contribution weights reproduce the rate
    weighted = sum(
        (p.capital_days / basket.capital_days) * p.roce_annualised for p in members
    ) if basket.capital_days else 0.0
    diff3 = abs(weighted - basket.roce_annualised)
    checks.append((
        "Contributions reproduce basket rate",
        diff3 < 1e-9,
        f"{weighted:.6%} vs {basket.roce_annualised:.6%}",
    ))

    return checks
