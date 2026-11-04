"""
core.risk
=========

Risk while held: volatility, maximum drawdown, Sharpe and Sortino for each
position over the days it was actually held, and for a basket of positions.
Read-only; writes nothing. Nothing here knows about Dash. Consumed by
pages/pnl_analysis.py alongside core.roce.


DEFINITIONS
-----------
For every calendar day t on which the position was held at the previous
day's close (quantity > 0 at the end of t-1):

    r_t      = P_t / P_(t-1) - 1     P is the close in GBP at that day's FX,
                                     carried forward over weekends and
                                     holidays, so those days return 0
    rf_t     = Bank Rate_t / 100 / 365
    Vol      = sqrt(365) * sd(r)
    Sharpe   = 365 * mean(r - rf) / Vol
    Sortino  = 365 * mean(r - rf) / (sqrt(365) * sqrt(mean(min(r - rf, 0)^2)))
    Max DD   = worst fall from a running peak of prod(1 + r), starting from 1

Calendar days and 365, not trading days and 252, to match how ROCE counts
capital-days, and so Bitcoin, priced seven days a week, can share a basket
with shares. Vol comes out within about 2% of the trading-day figure.

Time-weighted: a day's return does not depend on how much was held, only on
whether anything was. Size matters only in a basket, where each day's return
is the summed GBP P&L over the summed opening value.

Price return only. Dividends are not added: the stored Yahoo history is partly
dividend-adjusted (the importer's first download of a ticker is), so adding
the ledger's dividends would count some of them twice.

The day of a buy that opens a holding is not in the series - nothing was held
at the previous close - so fill prices never enter it. The series is purely
close-to-close.


SPEED
-----
The page recomputes on every callback (see pages/pnl_analysis.get_data), so
this runs in numpy over each instrument's own holding window: prices are read
only from a fortnight before its first trade, and only up to its exit if it
is closed. The first version reindexed a full calendar per instrument in
pandas and read every history back to the earliest trade; it took ~0.85s on
a ledger of 80 instruments. This one takes a fraction of that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core import db
from core import finance as fin
from core.repo import macro
from core.repo import portfolio as repo

# Risk-free rate for Sharpe and Sortino: a macro series id holding an annual
# rate in percent. Set to None to use zero.
RISK_FREE = "BANKRATE"

DAYS = 365

# How far before an instrument's first trade its prices are read, so the
# first held day has a previous close even after a long weekend.
LEAD_DAYS = 14

ONE_DAY = np.timedelta64(1, "D")


@dataclass
class Risk:
    vol: float | None = None
    max_dd: float | None = None
    sharpe: float | None = None
    sortino: float | None = None
    # Held days that produced a return.
    n_days: int = 0
    # Held days with no price (before the series starts, or unconvertible).
    missing_days: int = 0
    # Per held day: v_prev (GBP value at the previous close) and pnl (GBP
    # move). Kept so a basket can be built from its members.
    daily: pd.DataFrame = field(repr=False, default_factory=pd.DataFrame)


@dataclass
class RiskBook:
    by_id: dict[str, Risk]
    # Daily risk-free rate on calendar days (a fraction, not percent).
    rf: pd.Series
    # What rf is: the series id, "zero" by choice, or "zero (no data)".
    rf_source: str


# --------------------------------------------------------------------------
# Pure parts - no database. These are what the tests exercise.
# --------------------------------------------------------------------------

def _stats(r: np.ndarray, rf: np.ndarray) -> dict:
    """Core of stats() on plain arrays of equal length."""
    n = int(r.size)
    out = dict(vol=None, max_dd=None, sharpe=None, sortino=None, n_days=n)
    if n == 0:
        return out

    index = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(np.concatenate([[1.0], index]))[1:]
    out["max_dd"] = float(min((index / peak - 1.0).min(), 0.0))

    if n < 2:
        return out
    excess = r - rf
    mean_ex = float(excess.mean())
    sd = float(r.std(ddof=1))
    if sd > 0:
        out["vol"] = sd * math.sqrt(DAYS)
        out["sharpe"] = mean_ex * DAYS / out["vol"]
    down = math.sqrt(float((np.minimum(excess, 0.0) ** 2).mean()))
    if down > 0:
        out["sortino"] = mean_ex * DAYS / (down * math.sqrt(DAYS))
    return out


def stats(r: pd.Series, rf: pd.Series | None = None) -> dict:
    """Vol, Max DD, Sharpe and Sortino of daily (calendar-day) returns.

    rf is a daily rate on dates; dates it does not cover count as zero.
    Values that cannot be computed come back as None: Max DD needs one
    return, the rest need two and a non-zero spread.
    """
    r = r.dropna()
    rf_arr = (rf.reindex(r.index).fillna(0.0).to_numpy(dtype=float)
              if rf is not None else np.zeros(len(r)))
    return _stats(r.to_numpy(dtype=float), rf_arr)


def _day(ts) -> np.datetime64:
    return np.datetime64(pd.Timestamp(ts).date(), "D")


def _days(col: pd.Series) -> np.ndarray:
    """A date column (strings or timestamps) as datetime64[D]."""
    return pd.to_datetime(col).to_numpy().astype("datetime64[D]")


def _daily_arrays(trade_days: np.ndarray, qty_end: np.ndarray,
                  price_days: np.ndarray, px: np.ndarray,
                  asof: np.datetime64):
    """Held-day arrays for one position. All day arrays are datetime64[D],
    sorted ascending.

    Returns (days, v_prev, pnl, missing) for the held days that have a price
    today and yesterday; `missing` counts held days that do not.
    """
    start = trade_days[0]
    days = np.arange(start, asof + ONE_DAY, dtype="datetime64[D]")
    if days.size == 0:
        return days, np.empty(0), np.empty(0), 0

    # Units at the end of each day: the last trade on or before it.
    i = np.searchsorted(trade_days, days, side="right") - 1
    q = qty_end[i]
    q_prev = np.concatenate([[0.0], q[:-1]])

    # Close on or before each day (carried forward), and the day before.
    j = np.searchsorted(price_days, days, side="right") - 1
    p = np.where(j >= 0, px[np.clip(j, 0, None)], np.nan) if px.size else \
        np.full(days.size, np.nan)
    j0 = np.searchsorted(price_days, start - ONE_DAY, side="right") - 1
    p_first_prev = px[j0] if px.size and j0 >= 0 else np.nan
    p_prev = np.concatenate([[p_first_prev], p[:-1]])

    held = q_prev > 1e-9
    priced = ~np.isnan(p) & ~np.isnan(p_prev) & (np.nan_to_num(p_prev) > 0)
    ok = held & priced
    v_prev = q_prev[ok] * p_prev[ok]
    pnl = q_prev[ok] * (p[ok] - p_prev[ok])
    return days[ok], v_prev, pnl, int((held & ~priced).sum())


def position_daily(qty_end: pd.Series, px_gbp: pd.Series,
                   asof: pd.Timestamp) -> tuple[pd.DataFrame, int]:
    """Daily GBP series for one position.

    qty_end: units held at the end of each trade date; carried forward
             between trades.
    px_gbp:  GBP closes on whatever dates exist; carried forward.

    Returns (frame, missing): frame has v_prev, pnl and r on each held day
    that has a price today and yesterday; missing counts held days without.
    """
    empty = pd.DataFrame(columns=["v_prev", "pnl", "r"], dtype=float)
    if qty_end.empty:
        return empty, 0
    q = qty_end.groupby(level=0).last().sort_index()
    p = px_gbp.dropna()
    p = p[~p.index.duplicated(keep="last")].sort_index()
    days, v_prev, pnl, missing = _daily_arrays(
        q.index.values.astype("datetime64[D]"), q.to_numpy(dtype=float),
        p.index.values.astype("datetime64[D]"), p.to_numpy(dtype=float),
        _day(asof))
    frame = pd.DataFrame({"v_prev": v_prev, "pnl": pnl},
                         index=pd.DatetimeIndex(days.astype("datetime64[ns]")))
    frame["r"] = frame["pnl"] / frame["v_prev"]
    return frame, missing


def basket(dailies: list[pd.DataFrame], rf: pd.Series | None = None) -> dict:
    """Stats for several positions held together: each day's return is the
    summed GBP P&L over the summed opening value, so size and correlation
    both count."""
    frames = [d[["v_prev", "pnl"]] for d in dailies if d is not None and not d.empty]
    if not frames:
        return stats(pd.Series(dtype=float), rf)
    total = pd.concat(frames).groupby(level=0).sum().sort_index()
    total = total[total["v_prev"] > 0]
    return stats(total["pnl"] / total["v_prev"], rf)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

# Each ledger instrument's closes, from LEAD_DAYS before its first trade to
# its last trade if it is now flat (nothing after an exit is needed), else to
# the latest price. Cuts the read to the windows actually used.
WINDOWED_PRICES_SQL = f"""
    WITH w AS (
        SELECT fund_id,
               date(MIN(trade_date), '-{LEAD_DAYS} days') AS d0,
               CASE WHEN ABS(SUM(CASE UPPER(type)
                                     WHEN 'BUY'  THEN quantity
                                     WHEN 'SELL' THEN -quantity
                                     ELSE 0 END)) < 1e-9
                    THEN MAX(trade_date) ELSE '9999-12-31' END AS d1
        FROM transactions
        WHERE UPPER(type) IN ('BUY', 'SELL')
        GROUP BY fund_id
    )
    SELECT p.fund_id, p.date, p.close
    FROM prices p JOIN w ON w.fund_id = p.fund_id
    WHERE p.date >= w.d0 AND p.date <= w.d1
    ORDER BY p.fund_id, p.date
"""


def _fx_records(fx_ids: list[str], since: str, dates: np.ndarray) -> list[dict]:
    """One {currency: GBP cross} dict per date, from every YF:GBPxxx=X series,
    carried forward. USD and TRY always present (FX_FALLBACK when there is no
    data at all), because core.finance.to_gbp reads them by name."""
    marks = ",".join("?" * len(fx_ids))
    fx = db.query(f"SELECT fund_id, date, close FROM prices "
                  f"WHERE fund_id IN ({marks}) AND date >= ? ORDER BY fund_id, date",
                  tuple(fx_ids) + (since,))
    cols: dict[str, np.ndarray] = {}
    for fid, g in fx.groupby("fund_id", sort=False):
        d = _days(g["date"])
        v = g["close"].to_numpy(dtype=float)
        k = np.searchsorted(d, dates, side="right") - 1
        # Before the first FX close, use the first one.
        cols[fid[6:9]] = v[np.clip(k, 0, None)]           # YF:GBPUSD=X -> USD
    for ccy, fallback in fin.FX_FALLBACK.items():
        cols.setdefault(ccy, np.full(dates.size, float(fallback)))
    names = list(cols)
    return [dict(zip(names, row)) for row in zip(*(cols[c] for c in names))]


def _risk_free(start: pd.Timestamp, asof: pd.Timestamp) -> tuple[pd.Series, str]:
    days = pd.date_range(start, asof, freq="D")
    zero = pd.Series(0.0, index=days)
    if RISK_FREE is None:
        return zero, "zero"
    # The whole series, not from `start`: if it is stored only on the dates
    # the rate changed, the rate in force on day one is an earlier row.
    try:
        f = macro.frame([RISK_FREE])
    except Exception:                                   # noqa: BLE001
        f = pd.DataFrame()
    if f.empty or RISK_FREE not in f.columns or f[RISK_FREE].dropna().empty:
        return zero, "zero (no data)"
    s = f[RISK_FREE].dropna()
    s.index = pd.to_datetime(s.index)
    s = s.reindex(s.index.union(days)).sort_index().ffill().bfill().reindex(days)
    return s / 100.0 / DAYS, RISK_FREE


def compute_risk(trades: pd.DataFrame, asof: pd.Timestamp | None = None) -> RiskBook:
    """Risk while held for every instrument in `trades` (core.roce.load_trades).

    GBP closes come from core.finance.to_gbp, the same conversion the marks
    on every other tab use, applied with each day's FX. to_gbp is linear in
    price, so it is evaluated once per date for each unit/currency pair and
    the factor multiplied in, rather than once per price row.
    """
    asof = pd.Timestamp(asof or pd.Timestamp.today()).normalize()
    if trades.empty:
        return RiskBook({}, pd.Series(dtype=float), "zero")

    start = pd.Timestamp(trades["date"].min())
    rf, rf_source = _risk_free(start, asof)
    rf_arr = rf.to_numpy(dtype=float)
    rf_start = _day(start)
    asof_d = _day(asof)

    instruments = repo.instruments()
    prices = db.query(WINDOWED_PRICES_SQL)
    closes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    all_dates = np.empty(0, dtype="datetime64[D]")
    if not prices.empty:
        for fid, g in prices.groupby("fund_id", sort=False):
            closes[fid] = (_days(g["date"]), g["close"].to_numpy(dtype=float))
        all_dates = np.unique(_days(prices["date"]))

    fx_ids = sorted({k for k in instruments if k.startswith("YF:GBP") and k.endswith("=X")}
                    | {"YF:GBPUSD=X", "YF:GBPTRY=X"})
    since = str(rf_start - LEAD_DAYS * ONE_DAY)
    records = _fx_records(fx_ids, since, all_dates) if all_dates.size else []
    factors: dict[tuple, np.ndarray] = {}

    def factor(punit, ccy) -> np.ndarray:
        """GBP value of one unit of price on every date in all_dates."""
        key = (punit, ccy)
        if key not in factors:
            vals = [fin.to_gbp(1.0, punit, ccy, rec["USD"], rec) for rec in records]
            factors[key] = np.array([np.nan if v is None else v for v in vals],
                                    dtype=float)
        return factors[key]

    out: dict[str, Risk] = {}
    for fid, grp in trades.groupby("instrument", sort=False):
        by_day = grp.groupby("date")["qty"].sum().cumsum()
        qty_end = by_day.to_numpy(dtype=float, copy=True)
        qty_end[np.abs(qty_end) < 1e-9] = 0.0
        trade_days = by_day.index.values.astype("datetime64[D]")

        if fid in closes:
            pdays, raw = closes[fid]
            inst = instruments.get(fid, {})
            f = factor(inst.get("price_unit", "pound"), inst.get("currency", "GBP"))
            px = raw * f[np.searchsorted(all_dates, pdays)]
            keep = ~np.isnan(px)
            pdays, px = pdays[keep], px[keep]
        else:
            pdays, px = np.empty(0, dtype="datetime64[D]"), np.empty(0)

        days, v_prev, pnl, missing = _daily_arrays(trade_days, qty_end, pdays, px, asof_d)
        offsets = (days - rf_start).astype(int)
        s = _stats(pnl / v_prev if v_prev.size else np.empty(0),
                   rf_arr[np.clip(offsets, 0, rf_arr.size - 1)] if rf_arr.size else
                   np.zeros(days.size))
        daily = pd.DataFrame({"v_prev": v_prev, "pnl": pnl},
                             index=pd.DatetimeIndex(days.astype("datetime64[ns]")))
        out[fid] = Risk(**s, missing_days=missing, daily=daily)
    return RiskBook(out, rf, rf_source)
