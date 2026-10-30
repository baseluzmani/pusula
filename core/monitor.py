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
    phase          the 50/200 state split by where the price sits against
                   the 50-day (PHASES below): Uptrend, Weakening, Downtrend,
                   Improving.
    next cross     trading days until the 50-day crosses the 200-day if
                   every future close equals today's (shown when within
                   cross_horizon), the same on the price's 50-day trend
                   (log-linear regression slope), and the flat price that
                   puts the averages level on day k (cross_breakeven).
                   Informational, like the 50/200 state.

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

CROSS_LIMIT = 130        # trading days searched ahead for the next cross
BREAKEVEN_NEAR = 10      # first breakeven horizon (the other is cross_horizon)

# (50-day above the 200-day?, price at or above the 50-day?) -> phase.
PHASES = {
    (True, True): "Uptrend",
    (True, False): "Weakening",
    (False, False): "Downtrend",
    (False, True): "Improving",
}

# One line each, in display order, for the note under the Monitor chart.
# {fast} / {slow} are filled with the configured lengths (50 / 200).
PHASE_TEXT = [
    ("Uptrend", "price above the {fast}-day, {fast}-day above the "
                "{slow}-day. The trend is intact."),
    ("Weakening", "{fast}-day still above the {slow}-day, but the price has "
                  "dropped below the {fast}-day. Often the first sign a rise "
                  "is tiring; a death cross follows if it lasts."),
    ("Downtrend", "price below the {fast}-day, {fast}-day below the "
                  "{slow}-day."),
    ("Improving", "{fast}-day still below the {slow}-day, but the price is "
                  "back above the {fast}-day. A golden cross follows if it "
                  "holds."),
]


@dataclass(frozen=True)
class Rules:
    review_sigma: float = 2.5
    review_pct: float = 25.0
    watch_sigma: float = 1.5
    ma_days: int = 200
    ma_fast_days: int = 50
    ma_watch_days: int = 10
    near_pct: float = 5.0
    cross_horizon: int = 20


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


def cross_breakeven(closes, k: int, fast: int, slow: int) -> float | None:
    """The price that, held flat from tomorrow, puts the fast average exactly
    on the slow one after k trading days. Above it the fast average is on top
    on day k; below it, it is not. None when k >= slow.

        k < fast          P = (fast * B - slow * A) / ((slow - fast) * k)
        fast <= k < slow  P = B / (slow - k)

    A is the sum of the latest (fast - k) closes and B the sum of the latest
    (slow - k) closes - the ones still inside each average on day k. The
    result can be zero or negative: then every price gives a fast average on
    top by day k."""
    c = np.asarray(closes, dtype=float)
    if k < 1 or k >= slow or len(c) < slow:
        return None
    b = c[-(slow - k):].sum()
    if k < fast:
        a = c[-(fast - k):].sum()
        return float((fast * b - slow * a) / ((slow - fast) * k))
    return float(b / (slow - k))


def project_cross(closes, path, fast: int, slow: int):
    """Walk the averages forward along ``path`` (future closes, tomorrow
    first). Returns (k, level): the first day k on which the fast average is
    on the other side of the slow one than today, and the level where the
    two meet (interpolated between day k-1 and day k). (None, None) if they
    do not cross along the path."""
    c = np.asarray(closes, dtype=float)
    path = np.asarray(path, dtype=float)
    if len(c) < slow or not len(path):
        return None, None
    ext = np.concatenate([c[-slow:], path])
    cs = np.concatenate([[0.0], np.cumsum(ext)])
    ends = np.arange(slow, len(ext) + 1)          # day 0 (today) .. day K
    ma_f = (cs[ends] - cs[ends - fast]) / fast
    ma_s = (cs[ends] - cs[ends - slow]) / slow
    diff = ma_f - ma_s
    above = diff > 0                              # same test as the state
    flips = np.flatnonzero(above[1:] != above[0])
    if not len(flips):
        return None, None
    k = int(flips[0]) + 1
    d0, d1 = diff[k - 1], diff[k]
    t = d0 / (d0 - d1) if d0 != d1 else 1.0
    level = ma_s[k - 1] + t * (ma_s[k] - ma_s[k - 1])
    return k, float(level)


def trend_slope(closes, days: int) -> float | None:
    """Slope of a straight line through the log of the latest ``days``
    closes: the daily growth rate the recent trend implies (0.001 is about
    +0.1% a day). None with fewer closes than that."""
    c = np.asarray(closes, dtype=float)
    if days < 2 or len(c) < days:
        return None
    return float(np.polyfit(np.arange(days), np.log(c[-days:]), 1)[0])


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
            "cross_days", "cross_span", "phase", "ma_gap", "next_cross",
            "next_days", "next_due", "next_date", "next_level", "trend_slope",
            "trend_days", "be_near_k", "be_near", "be_far_k", "be_far",
            "short", "buy_target", "sell_target",
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

    # Trend phase and the next cross need both averages.
    phase, nxt = None, dict.fromkeys(NEXT_KEYS)
    if cross is not None:
        phase = PHASES[(cross == "Golden", bool(price >= ma_fast))]
        nxt = _next_cross(closes, ma_fast, ma, cross, end, seven_day, rules)

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
        "phase": phase,
        **nxt,
        # Less than a year of history: the "52-week" high is the high of
        # whatever there is.
        "short": bool(dates[0] > start),
    }


NEXT_KEYS = ["ma_gap", "next_cross", "next_days", "next_due", "next_date",
             "next_level", "trend_slope", "trend_days", "be_near_k", "be_near",
             "be_far_k", "be_far"]


def _next_cross(closes, ma_fast, ma, cross, end, seven_day,
                rules: Rules) -> dict:
    """When the averages would cross next: with the price flat at today's
    close, and with it moving on its 50-day trend; plus the flat prices that
    put the averages level on day BREAKEVEN_NEAR and day cross_horizon.
    Days are trading days (calendar days for seven-day instruments)."""
    fast, slow = rules.ma_fast_days, rules.ma_days
    price = closes[-1]
    k, level = project_cross(closes, np.full(CROSS_LIMIT, price), fast, slow)

    slope = trend_slope(closes, fast)
    k_trend = None
    if slope is not None:
        path = price * np.exp(slope * np.arange(1, CROSS_LIMIT + 1))
        k_trend, _ = project_cross(closes, path, fast, slow)

    when = None
    if k:
        if seven_day:
            when = pd.Timestamp(end) + pd.Timedelta(days=k)
        else:
            when = pd.Timestamp(np.busday_offset(
                np.datetime64(end, "D"), k, roll="forward"))

    horizon = max(1, rules.cross_horizon)
    near_k = min(BREAKEVEN_NEAR, horizon)
    far_k = horizon if horizon > near_k else None
    return {
        "ma_gap": float((ma_fast / ma - 1) * 100),
        "next_cross": "Death" if cross == "Golden" else "Golden",
        "next_days": k,
        "next_due": k if k is not None and k <= horizon else None,
        "next_date": when,
        "next_level": level,
        "trend_slope": slope * 100 if slope is not None else None,
        "trend_days": k_trend,
        "be_near_k": near_k,
        "be_near": cross_breakeven(closes, near_k, fast, slow),
        "be_far_k": far_k,
        "be_far": (cross_breakeven(closes, far_k, fast, slow)
                   if far_k else None),
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
