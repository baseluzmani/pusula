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

from dataclasses import dataclass, field

import pandas as pd

from core import db
from core import finance as fin
from core import valuation
from core.repo import portfolio as repo

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

# Reads go through core.db.query, which opens the file read-only (mode=ro),
# applies the lock timeout and guarantees the connection is closed. This module
# therefore has no connection handling of its own and no DB_PATH: both come
# from core.config via core.db, exactly like every other reader.

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
    df = db.query(TRADES_SQL)

    df = df[~df["instrument"].isin(EXCLUDED)].copy()
    df["date"] = pd.to_datetime(df["date"])

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


def load_latest_prices() -> tuple[dict[str, float], list[str], list[str]]:
    """Latest mark per instrument, in GBP, from the same source as every
    other tab.

    This used to run its own SQL and convert non-GBP instruments using the FX
    rate stored on that instrument's most recent trade. That is a historical
    rate, so this page drifted from the P&L tab by however far sterling had
    moved since the last trade - 5.6% on SEMI.L, which turned a -1,750
    position into a -6,190 one.

    Marks now come from core.valuation.holding_price_gbp, which applies the
    live cross from YF:GBPUSD=X, so the two pages agree by construction.
    Historical FX still governs cost: each transaction's own fx_rate is used
    in load_trades, which is correct and unchanged. Current value at current
    rates, historical cost at historical rates.

    Returns (prices, approximated, unpriceable). `approximated` is kept so the
    page's banner keeps working but is now always empty - no mark is an
    approximation any more. `unpriceable` lists instruments with no usable GBP
    price; they are omitted from the dict rather than given a zero, because a
    zero mark silently reports an open position as a total loss.
    """
    price_frame = repo.latest_prices()
    instruments = repo.instruments()
    rates = fin.fx_rates(price_frame)
    price_map = fin.latest_price_map(price_frame)

    out: dict[str, float] = {}
    unpriceable: list[str] = []
    for fund_id in price_map:
        if fund_id in EXCLUDED:
            continue
        gbp = valuation.holding_price_gbp(
            fund_id, instruments, price_map, rates["USD"], rates)
        if gbp is None or pd.isna(gbp):
            unpriceable.append(fund_id)
        else:
            out[fund_id] = float(gbp)
    return out, [], unpriceable


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

    # False when an OPEN position has no usable mark, so unrealised P&L is
    # unknown rather than zero. Such a position reports realised P&L only and
    # is left out of basket aggregation. A closed position needs no mark, so it
    # stays priced=True.
    priced: bool = True

    # Set when the ledger for this instrument could not be walked (e.g. a sell
    # with no matching buy). The position is returned with zeros so one bad
    # ledger cannot take down the whole page.
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if not self.priced:
            return "unpriced"
        return "closed" if self.qty == 0 else "open"


def compute_position(
    trades: pd.DataFrame,
    latest_price: float | None,
    asof: pd.Timestamp,
    instrument: str = "",
    name: str = "",
) -> Position:
    """Run weighted-average-cost over one instrument's full ledger.

    latest_price may be None, meaning "no mark available". For a position that
    closed flat this is harmless - unrealised is genuinely zero. For an open
    position it is not: unrealised is unknown, so the position reports realised
    P&L only and comes back with priced=False.
    """
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

    # An open position with no mark has unknown unrealised P&L. Report realised
    # only and flag it, rather than marking the position to zero - which would
    # show the entire book cost as a loss.
    priced = bool(qty == 0 or latest_price is not None)
    if priced:
        unrealised = qty * (latest_price or 0.0) - book
    else:
        unrealised = 0.0
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
        latest_price=float(latest_price) if latest_price is not None else 0.0,
        # Built from the list, not dict(...): two sells on the same date are two
        # events, and dict() would keep only the last one.
        realised_events=(
            pd.Series([v for _, v in realised_events],
                      index=pd.to_datetime([d for d, _ in realised_events]))
            if realised_events else pd.Series(dtype=float)
        ),
        priced=priced,
    )


def compute_all(asof: pd.Timestamp | None = None) -> list[Position]:
    """Run the engine over every instrument in the ledger."""
    trades = load_trades()
    prices, _approx, _unpriceable = load_latest_prices()
    asof = pd.Timestamp(asof or pd.Timestamp.today().normalize())

    out = []
    for instrument, grp in trades.groupby("instrument", sort=False):
        name = grp["name"].iloc[-1]
        try:
            out.append(
                compute_position(
                    grp,
                    # .get returns None for an instrument with no usable mark.
                    # That is the point: None means "unknown", 0.0 would mean
                    # "worthless".
                    latest_price=prices.get(instrument),
                    asof=asof,
                    instrument=instrument,
                    name=name,
                )
            )
        except ValueError as exc:
            # A broken ledger for one instrument must not take down the page.
            # Surface it as a row the user can see and fix.
            out.append(_error_position(instrument, name, str(exc)))
    return sorted(out, key=lambda p: p.capital_days, reverse=True)


def _error_position(instrument: str, name: str, message: str) -> Position:
    """A zeroed placeholder carrying the reason the ledger could not be walked."""
    return Position(
        instrument=instrument, name=name, qty=0.0, book_cost=0.0, wac=0.0,
        realised=0.0, unrealised=0.0, total_pnl=0.0,
        daily_book=pd.Series(dtype=float),
        capital_days=0.0, days_deployed=0, avg_capital=0.0, peak_capital=0.0,
        roce_total=0.0, roce_annualised=0.0, latest_price=0.0,
        priced=False, error=message,
    )


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
    # Positions left out because they have no mark or a broken ledger. The page
    # should show this count: a silently smaller basket is worse than a warning.
    excluded: list[Position] = field(default_factory=list)


def aggregate(members: list[Position]) -> Basket | None:
    """Pool the daily series first, then compute once. Never average rates.

    Members with no mark (priced=False) or a ledger error are excluded, because
    their total P&L is unknown and folding a partial figure into the basket
    would understate the rate without saying so.
    """
    excluded = [p for p in members if not p.priced or p.error]
    members = [p for p in members if p.priced and not p.error]
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
        excluded=excluded,
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

    # Reconcile only what the basket actually contains. Unpriced and errored
    # positions are reported separately in check 4 rather than failing check 1,
    # which would be a true statement about the wrong population.
    priced = basket.members

    # 1. total P&L == market value of what's left + net cash flow, per instrument
    worst = 0.0
    for p in priced:
        net_cash = float(trades.loc[trades["instrument"] == p.instrument, "cash_flow"].sum())
        expected = p.qty * p.latest_price + net_cash
        worst = max(worst, abs(expected - p.total_pnl))
    checks.append((
        "P&L reconciles to ledger and mark",
        worst < 0.01,
        f"largest difference £{worst:,.4f}",
    ))

    # 2. capital-days are additive across instruments
    summed = sum(p.capital_days for p in priced)
    diff = abs(summed - basket.capital_days)
    checks.append((
        "Capital-days additive",
        diff < 1.0,
        f"£{summed:,.0f} vs £{basket.capital_days:,.0f}",
    ))

    # 3. total and annualised tie, and the contribution weights reproduce the rate.
    # The identity only holds if every member carrying P&L also carries
    # capital-days; check 5 is what tells you when it does not.
    weighted = sum(
        (p.capital_days / basket.capital_days) * p.roce_annualised for p in priced
    ) if basket.capital_days else 0.0
    diff3 = abs(weighted - basket.roce_annualised)
    checks.append((
        "Contributions reproduce basket rate",
        diff3 < 1e-9,
        f"{weighted:.6%} vs {basket.roce_annualised:.6%}",
    ))

    # 4. every position made it into the basket
    bad = basket.excluded
    checks.append((
        "All positions priced and walkable",
        not bad,
        "all included" if not bad else
        f"{len(bad)} excluded: " + ", ".join(
            f"{p.instrument} ({p.error or 'no mark'})" for p in bad[:5]
        ),
    ))

    # 5. same-day round trips carry P&L but no capital-days, which breaks the
    # contribution identity in check 3. Name them rather than let check 3 fail
    # with no explanation.
    flat = [p for p in priced if p.capital_days == 0 and abs(p.total_pnl) > 0.01]
    checks.append((
        "No P&L without capital employed",
        not flat,
        "none" if not flat else
        f"{len(flat)} same-day: " + ", ".join(p.instrument for p in flat[:5]),
    ))

    return checks
