"""
Momentum map: where each instrument sits on trend vs recent move, measured in
its own volatility.

Pure calculation - price frames in, DataFrames out. No Dash, no SQL.

The sums, per instrument, on its own price series
------------------------------------------------
    daily return   r_t = P_t / P_(t-1) - 1
    sigma          standard deviation of daily returns over the trend window,
                   floored so a near-flat series (a money market fund) cannot
                   produce an enormous score
    X  (trend)     R_trend  / (sigma * sqrt(N_trend))
    Y  (recent)    R_recent / (sigma * sqrt(N_recent))

R is the simple return over the window and N the number of daily returns in
it - the instrument's own count, so a 7-day crypto calendar and a 5-day fund
calendar are each scaled correctly. Dividing by sigma * sqrt(N) turns a return
into "how many standard deviations of this asset's ordinary noise", which is
roughly the t-statistic of the drift. Above about 1 is a real move; within
about +-0.5 is noise. One sigma (the trend window's) scales both axes: a
1-month sigma is a noisy estimate, and a sell-off inflates its own 1-month
sigma, shrinking the very move you want to see.

Benchmark
---------
With a benchmark the same sums run on the ratio series P_fund / P_bench, the
benchmark taken as its last close on or before each of the fund's dates.
Sigma then becomes tracking error and X, Y are relative scores.

Currency
--------
Returns are in each instrument's own quote currency, matching the other
Markets tabs. Against a benchmark that is roughly an FX-hedged comparison.

Dates
-----
Each instrument is measured up to its OWN last price on or before the as-of
date, so a fund whose NAV lags by two days still gets a full window rather
than a window cut short by everyone else's later date.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from core.repo.market import PERIODS

# "3M" -> 91 etc. The same definitions the Heatmap uses, so a label means the
# same number of days on every tab. YTD has no fixed length, so it is not a
# valid horizon label.
PERIOD_DAYS = {label: days for label, days in PERIODS if days}
# Two weeks exists only here, for the Tactical preset; adding it to PERIODS
# would also add a 2W column to the Heatmap and Compare tables.
PERIOD_DAYS["2W"] = 14

# Defaults for the presets: trend window, recent window, trail spacing.
# Overridable from Data -> Config (keys MOMENTUM_TACTICAL / _SHORT / _MEDIUM
# / _LONG).
HORIZON_DEFAULTS = {
    "Tactical": "3M,2W,1W",
    "Short": "3M,1M,1W",
    "Medium": "6M,1M,1W",
    "Long": "1Y,3M,1M",
}

VOL_FLOOR = 0.02        # annualised; sigma never goes below this
MIN_RETURNS = 10        # fewer daily returns than this: no score at all
MIN_COVERAGE = 0.80     # below this share of expected days: flagged short

QUADRANTS = {
    (True, True): "Rising",       # trend up, still rising
    (True, False): "Pullback",    # trend up, recent move down
    (False, False): "Falling",    # trend down, still falling
    (False, True): "Recovering",  # trend down, recent move up
}


# --- Horizons --------------------------------------------------------------

@dataclass(frozen=True)
class Horizon:
    trend: str      # e.g. "6M"
    recent: str     # e.g. "1M"
    step: str       # trail spacing, e.g. "1W"

    @property
    def trend_days(self) -> int:
        return PERIOD_DAYS[self.trend]

    @property
    def recent_days(self) -> int:
        return PERIOD_DAYS[self.recent]

    @property
    def step_days(self) -> int:
        return PERIOD_DAYS[self.step]


def _parse(text) -> Horizon | None:
    """'6M,1M,1W' -> Horizon, or None if the text is not a valid horizon."""
    if not isinstance(text, str):
        return None
    parts = [p.strip().upper() for p in text.split(",")]
    if len(parts) != 3 or any(p not in PERIOD_DAYS for p in parts):
        return None
    trend, recent, step = parts
    # The recent window must be shorter than the trend window, or the two
    # axes measure the same thing.
    if PERIOD_DAYS[recent] >= PERIOD_DAYS[trend]:
        return None
    return Horizon(trend, recent, step)


def parse_horizon(text, default: str) -> Horizon:
    """Parse a preset, falling back to ``default`` when the text is invalid -
    the same degrade-to-default behaviour as every other setting."""
    return _parse(text) or _parse(default)


def quadrant(x, y) -> str | None:
    if x is None or y is None or pd.isna(x) or pd.isna(y):
        return None
    return QUADRANTS[(x >= 0, y >= 0)]


# --- Scores ----------------------------------------------------------------

def _series(prices: pd.DataFrame, bench: pd.Series | None) -> dict:
    """
    fund_id -> (dates, values, seven_day) as numpy arrays, sorted, one value
    per date. Values are closes, or close / benchmark when a benchmark is set.
    seven_day is True for a series that trades at weekends (crypto).
    """
    df = prices[["fund_id", "date", "close"]].dropna()
    df = df[df["close"] > 0]
    df = (df.assign(date=pd.to_datetime(df["date"]))
            .sort_values(["fund_id", "date"])
            .drop_duplicates(["fund_id", "date"], keep="last"))

    if bench is not None:
        bench = bench.dropna()
        bench = bench[bench > 0]
        bench.index = pd.to_datetime(bench.index)
        bench = bench[~bench.index.duplicated(keep="last")].sort_index()
        if bench.empty:
            return {}

    out = {}
    for fid, g in df.groupby("fund_id", sort=False):
        dates = g["date"].to_numpy(dtype="datetime64[ns]")
        values = g["close"].to_numpy(dtype=float)
        if bench is not None:
            # Benchmark's last close on or before each fund date.
            b = bench.reindex(pd.DatetimeIndex(dates), method="ffill")
            b = b.to_numpy(dtype=float)
            ok = ~np.isnan(b)
            dates, values = dates[ok], values[ok] / b[ok]
        if len(values) < 2:
            continue
        weekend_share = (pd.DatetimeIndex(dates).dayofweek >= 5).mean()
        out[fid] = (dates, values, bool(weekend_share > 0.10))
    return out


def _score_one(dates, values, seven_day, as_of, h: Horizon,
               vol_floor: float) -> dict | None:
    """Scores for one series as at ``as_of``. None if there is too little
    history to say anything."""
    end_i = np.searchsorted(dates, as_of, side="right") - 1
    if end_i < 1:
        return None
    end_date, end_value = dates[end_i], values[end_i]

    # Trend window: from the last price on or before (end - trend_days). If
    # history starts later than that, use what there is and flag it.
    cut = end_date - np.timedelta64(h.trend_days, "D")
    base_i = np.searchsorted(dates, cut, side="right") - 1
    starts_late = base_i < 0
    base_i = max(base_i, 0)
    n_trend = int(end_i - base_i)
    if n_trend < MIN_RETURNS:
        return None

    window = values[base_i:end_i + 1]
    daily = window[1:] / window[:-1] - 1
    periods = 365 if seven_day else 252
    sigma = max(float(np.std(daily, ddof=1)), vol_floor / np.sqrt(periods))

    # Coverage: how many daily returns we have vs. how many we should.
    base_day = dates[base_i].astype("datetime64[D]")
    end_day = end_date.astype("datetime64[D]")
    if seven_day:
        expected = int((end_day - base_day) / np.timedelta64(1, "D"))
    else:
        expected = int(np.busday_count(base_day, end_day))
    sparse = expected > 0 and n_trend < MIN_COVERAGE * expected

    r_trend = end_value / values[base_i] - 1
    x = r_trend / (sigma * np.sqrt(n_trend))

    # Recent window, scaled by the same sigma.
    rcut = end_date - np.timedelta64(h.recent_days, "D")
    rbase_i = max(np.searchsorted(dates, rcut, side="right") - 1, 0)
    n_recent = int(end_i - rbase_i)
    if n_recent > 0:
        r_recent = end_value / values[rbase_i] - 1
        y = r_recent / (sigma * np.sqrt(n_recent))
    else:
        r_recent, y = np.nan, np.nan

    return {
        "as_of": pd.Timestamp(end_date),
        "x": float(x),
        "y": float(y),
        "r_trend": float(r_trend * 100),
        "r_recent": float(r_recent * 100),
        "vol": float(sigma * np.sqrt(periods) * 100),
        "n_trend": n_trend,
        "short": bool(starts_late or sparse),
    }


def scores(prices: pd.DataFrame, h: Horizon, as_of=None,
           bench: pd.Series | None = None,
           vol_floor: float = VOL_FLOOR) -> pd.DataFrame:
    """
    One row per instrument: fund_id, as_of, x, y, r_trend, r_recent, vol,
    n_trend, short, quadrant.

    prices:  long frame with fund_id, date, close (any extra columns ignored).
    as_of:   score as at this date; default is the latest date in ``prices``.
    bench:   benchmark closes indexed by date, or None for absolute scores.
    r_trend, r_recent and vol are percentages; x, y are in sigma units.
    """
    return trails(prices, h, points=0, bench=bench, vol_floor=vol_floor,
                  as_of=as_of).drop(columns="step")


def trails(prices: pd.DataFrame, h: Horizon, points: int,
           bench: pd.Series | None = None, vol_floor: float = VOL_FLOOR,
           as_of=None) -> pd.DataFrame:
    """
    Scores now and at ``points`` earlier dates, h.step_days apart.

    Same columns as scores() plus ``step``: 0 is now, 1 is one step back, and
    so on. A fund with too little history at an earlier step simply has no
    row for that step.
    """
    cols = ["fund_id", "step", "as_of", "x", "y", "r_trend", "r_recent",
            "vol", "n_trend", "short", "quadrant"]
    if prices is None or prices.empty:
        return pd.DataFrame(columns=cols)

    series = _series(prices, bench)
    latest = pd.Timestamp(as_of) if as_of is not None \
        else pd.to_datetime(prices["date"]).max()

    rows = []
    for k in range(max(int(points), 0) + 1):
        when = np.datetime64(latest - pd.Timedelta(days=k * h.step_days), "ns")
        for fid, (dates, values, seven_day) in series.items():
            r = _score_one(dates, values, seven_day, when, h, vol_floor)
            if r is not None:
                rows.append({"fund_id": fid, "step": k, **r})

    if not rows:
        return pd.DataFrame(columns=cols)
    out = pd.DataFrame(rows)
    out["quadrant"] = [quadrant(x, y) for x, y in zip(out["x"], out["y"])]
    return out[cols]
