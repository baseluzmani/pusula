"""
Monitor: how far each instrument is from its 52-week high, where it sits
against its 200-day average, how far it is from your own levels, and a
status built from those.

Pure calculation - price frame and targets in, DataFrame out. No Dash, no SQL.

The measures, per instrument, on its own closes in its own quote units
---------------------------------------------------------------------
    drawdown       last / 52-week high - 1            (zero at the high)
    drawdown in s  drawdown / (sigma * sqrt(N))       N = trading days since
                                                      the high
    200-day        last / average of the last 200 closes - 1, and how many
                   consecutive closes have been below that average
    50-day         last / average of the last 50 closes - 1
    50/200 cross   whether the 50-day average is above the 200-day (after a
                   "golden cross") or below it (after a "death cross"), and
                   how long ago it last crossed. Informational only: it lags
                   the 200-day count, so it does not feed the status.

sigma is the standard deviation of daily returns over the last year, floored
like the Momentum tab's. Dividing by sigma * sqrt(N) is the same idea as the
Momentum scores: a 15% fall in two weeks scores far higher than the same fall
over six months, and a 15% fall in Bitcoin means less than one in a gilt.

Status
------
    Review   price at or below your review level, or
             drawdown >= review_sigma (sigma units), or
             drawdown >= review_pct (%)
    Watch    within near_pct of your review level, or
             drawdown >= watch_sigma, or
             below the 200-day average for >= ma_watch_days closes
    OK       none of the above

Buy and sell targets do not feed the status: status is about deterioration,
targets are your action levels. Each target gets its own state instead -
"hit" when the price has reached it, "near" within near_pct.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

HIGH_DAYS = 365          # 52 weeks, calendar days
VOL_DAYS = 365           # sigma from a year of daily returns
VOL_FLOOR = 0.02         # annualised; same floor as Momentum

STATUS_ORDER = {"Review": 0, "Watch": 1, "OK": 2}


@dataclass(frozen=True)
class Rules:
    review_sigma: float = 2.5
    review_pct: float = 25.0
    watch_sigma: float = 1.5
    ma_days: int = 200
    ma_fast_days: int = 50
    ma_watch_days: int = 10
    near_pct: float = 5.0


def lookback_days(rules: Rules) -> int:
    """Calendar days of prices needed: a year for the high and sigma, plus
    enough before that for the 200-day average to exist across the year."""
    return max(HIGH_DAYS, VOL_DAYS) + int(rules.ma_days * 7 / 5) + 30


def with_ma(closes: pd.Series, ma_days: int,
            fast_days: int | None = None) -> pd.DataFrame:
    """date-indexed close plus its moving averages, NaN until there are
    enough closes: "ma" (ma_days) and, when fast_days is given, "ma_fast".
    For the Monitor chart."""
    s = closes.dropna().sort_index()
    out = pd.DataFrame({"close": s,
                        "ma": s.rolling(ma_days, min_periods=ma_days).mean()})
    if fast_days:
        out["ma_fast"] = s.rolling(fast_days, min_periods=fast_days).mean()
    return out


def crosses(fast, slow, dates) -> list:
    """Every date the fast average crosses the slow one, oldest first, as
    (date, "Golden" | "Death"). Golden = fast moves above slow."""
    diff = np.asarray(fast, dtype=float) - np.asarray(slow, dtype=float)
    out, prev = [], 0.0
    for d, x in zip(dates, diff):
        if np.isnan(x) or x == 0:
            continue
        sign = np.sign(x)
        if prev and sign != prev:
            out.append((pd.Timestamp(d), "Golden" if sign > 0 else "Death"))
        prev = sign
    return out


def metrics(prices: pd.DataFrame, targets: pd.DataFrame | None = None,
            rules: Rules = Rules()) -> pd.DataFrame:
    """
    One row per instrument in ``prices`` (long frame: fund_id, date, close).
    ``targets`` is the mkt_targets frame (fund_id, buy_target, sell_target,
    review_level, note); instruments without a row simply have no levels.

    Percentages (dd, vol, vs_ma, dist_*) are in percent; dd_sigma is negative
    below the high, like dd.
    """
    cols = ["fund_id", "as_of", "price", "high", "high_date", "dd",
            "dd_sigma", "days_since_high", "vol", "ma", "vs_ma",
            "days_below_ma", "ma_fast", "vs_ma_fast", "cross", "cross_date",
            "cross_days", "cross_span", "short", "buy_target", "sell_target",
            "review_level", "note", "dist_buy", "dist_sell", "dist_review",
            "buy_state", "sell_state", "review_state", "status", "reasons"]
    if prices is None or prices.empty:
        return pd.DataFrame(columns=cols)

    df = prices[["fund_id", "date", "close"]].dropna()
    df = df[df["close"] > 0]
    df = (df.assign(date=pd.to_datetime(df["date"]))
            .sort_values(["fund_id", "date"])
            .drop_duplicates(["fund_id", "date"], keep="last"))

    levels = {}
    if targets is not None and not targets.empty:
        levels = targets.set_index("fund_id").to_dict("index")

    rows = []
    for fid, g in df.groupby("fund_id", sort=False):
        if len(g) < 2:
            continue
        r = _one(g["date"].to_numpy(dtype="datetime64[ns]"),
                 g["close"].to_numpy(dtype=float), rules)
        r.update(_levels(r["price"], levels.get(fid, {}), rules))
        r["status"], r["reasons"] = _status(r, rules)
        rows.append({"fund_id": fid, **r})

    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame(rows)[cols]


# --- internals ---------------------------------------------------------------

def _one(dates, closes, rules: Rules) -> dict:
    end = dates[-1]
    price = closes[-1]

    # 52-week high: the latest close at the maximum, so "days since" counts
    # from the last time it was there.
    start = end - np.timedelta64(HIGH_DAYS, "D")
    w0 = int(np.searchsorted(dates, start, side="left"))
    window = closes[w0:]
    hi_rel = len(window) - 1 - int(np.argmax(window[::-1]))
    hi_i = w0 + hi_rel
    high = closes[hi_i]
    dd = price / high - 1
    n_since = len(closes) - 1 - hi_i

    seven_day = (pd.DatetimeIndex(dates).dayofweek >= 5).mean() > 0.10
    periods = 365 if seven_day else 252
    v0 = int(np.searchsorted(dates, end - np.timedelta64(VOL_DAYS, "D"),
                             side="left"))
    seg = closes[max(v0 - 1, 0):]
    daily = seg[1:] / seg[:-1] - 1
    sd = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
    sigma = max(sd, VOL_FLOOR / np.sqrt(periods))
    dd_sigma = dd / (sigma * np.sqrt(n_since)) if n_since > 0 else 0.0

    day = np.timedelta64(1, "D")
    ma, vs_ma, below, roll = np.nan, np.nan, 0, None
    if len(closes) >= rules.ma_days:
        roll = pd.Series(closes).rolling(rules.ma_days).mean().to_numpy()
        ma = roll[-1]
        vs_ma = price / ma - 1
        # Consecutive closes below their own 200-day average, counting back
        # from today.
        for c, m in zip(closes[::-1], roll[::-1]):
            if np.isnan(m) or c >= m:
                break
            below += 1

    # 50-day average, and its position against the 200-day.
    ma_fast, vs_fast = np.nan, np.nan
    cross = cross_date = cross_days = cross_span = None
    n_fast = rules.ma_fast_days
    if n_fast and len(closes) >= n_fast:
        fast = pd.Series(closes).rolling(n_fast).mean().to_numpy()
        ma_fast = fast[-1]
        vs_fast = price / ma_fast - 1
        if roll is not None and n_fast < rules.ma_days:
            cross = "Golden" if ma_fast > ma else "Death"
            # How far back both averages exist - "no cross in N days".
            cross_span = int((end - dates[rules.ma_days - 1]) / day)
            events = crosses(fast, roll, dates)
            if events:
                cross_date = events[-1][0]
                cross_days = int((end - cross_date.to_datetime64()) / day)

    return {
        "as_of": pd.Timestamp(end),
        "price": float(price),
        "high": float(high),
        "high_date": pd.Timestamp(dates[hi_i]),
        "dd": float(dd * 100),
        "dd_sigma": float(dd_sigma),
        "days_since_high": int((end - dates[hi_i]) / np.timedelta64(1, "D")),
        "vol": float(sigma * np.sqrt(periods) * 100),
        "ma": float(ma) if not np.isnan(ma) else None,
        "vs_ma": float(vs_ma * 100) if not np.isnan(vs_ma) else None,
        "days_below_ma": int(below),
        "ma_fast": float(ma_fast) if not np.isnan(ma_fast) else None,
        "vs_ma_fast": float(vs_fast * 100) if not np.isnan(vs_fast) else None,
        "cross": cross,
        "cross_date": cross_date,
        "cross_days": cross_days,
        "cross_span": cross_span,
        # Less than a year of history: the "52-week" high is the high of
        # whatever there is.
        "short": bool(dates[0] > start),
    }


def _levels(price: float, lv: dict, rules: Rules) -> dict:
    near = rules.near_pct / 100

    def num(k):
        v = lv.get(k)
        return float(v) if v is not None and pd.notna(v) and v > 0 else None

    buy, sell, review = num("buy_target"), num("sell_target"), num("review_level")
    note = lv.get("note")
    note = note if isinstance(note, str) and note.strip() else None

    def dist(level):
        return (level / price - 1) * 100 if level else None

    def below_state(level):           # buy target, review level
        if not level:
            return None
        if price <= level:
            return "hit"
        return "near" if price <= level * (1 + near) else None

    def above_state(level):           # sell target
        if not level:
            return None
        if price >= level:
            return "hit"
        return "near" if price >= level * (1 - near) else None

    return {"buy_target": buy, "sell_target": sell, "review_level": review,
            "note": note, "dist_buy": dist(buy), "dist_sell": dist(sell),
            "dist_review": dist(review), "buy_state": below_state(buy),
            "sell_state": above_state(sell),
            "review_state": below_state(review)}


def _status(r: dict, rules: Rules) -> tuple[str, str]:
    """Status plus the reasons behind it, for the hover on the status cell."""
    dd_s, dd_p = -r["dd_sigma"], -r["dd"]
    review, watch = [], []

    if r["review_state"] == "hit":
        review.append("at or below your review level")
    if dd_s >= rules.review_sigma:
        review.append(f"drawdown {dd_s:.1f}\u03c3")
    if dd_p >= rules.review_pct:
        review.append(f"drawdown {dd_p:.0f}%")
    if review:
        return "Review", "; ".join(review)

    if r["review_state"] == "near":
        watch.append(f"within {rules.near_pct:g}% of your review level")
    if dd_s >= rules.watch_sigma:
        watch.append(f"drawdown {dd_s:.1f}\u03c3")
    if r["days_below_ma"] >= rules.ma_watch_days:
        watch.append(f"below the {rules.ma_days}-day average for "
                     f"{r['days_below_ma']} closes")
    if watch:
        return "Watch", "; ".join(watch)
    return "OK", ""
