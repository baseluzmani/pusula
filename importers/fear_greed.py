"""
CNN Fear & Greed importer.

CNN serves the index as JSON behind its own chart, at one URL that takes a
start date. It is not a documented API, so this is written to fail loudly
rather than store something half-parsed: a key that has moved raises, and the
run shows as failed on the Data page instead of quietly writing nothing.

Stored in macro_series / macro_observations with source 'CNN', beside the FRED
and BoE series. A score is not a price any more than a yield is, and living
there means the series browser, range_stats and observations() all work on it
without a line of new plumbing.

What it keeps
-------------
CNNFG       the headline score, with history. The endpoint returns about a
            year of daily closes, so the first run backfills the chart.
CNNFG_*     the seven component scores. CNN gives only today's value for
            these - the history arrays beside them hold the raw inputs (the
            S&P level, VIX, the put/call ratio), not the 0-100 scores - so
            their history exists only from the day this starts running.
            Kept from day one for that reason, though nothing charts them yet.

Revisions
---------
CNN restates recent closes. On 19 Oct 2026 the evening value for the day was
23.1; by the next afternoon the same date read 20.8, while previous_close
still said 23.1. So the last REVISION_DAYS are refetched and written with
REPLACE, as the FRED importer does, and previous_close is never used - the
day-on-day change comes from what is stored.

Dates
-----
Daily points are stamped at midnight UTC on the US trading date, and the live
point carries its update time. Both are read as UTC dates. Reading them as New
York time would move every daily close back a day.

If CNN starts refusing requests even with the browser headers below,
curl_cffi (already installed for yfinance) can impersonate a browser outright.

Usage
-----
    python3 -m importers.fear_greed
    python3 -m importers.fear_greed --full      # refetch the whole year
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone

import requests

from core.repo import macro as repo

URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}"

# Without a browser user-agent the endpoint answers 418 rather than JSON.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://edition.cnn.com/",
}
TIMEOUT = 30

# CNN's restatements seen so far are a day or two back; ten covers a long
# weekend and a holiday with room to spare.
REVISION_DAYS = 10

# What a first run asks for. The endpoint has served about a year.
FIRST_DAYS = 365

TOTAL = "CNNFG"

# (series id, JSON key, name). Momentum and volatility each come as two keys -
# the S&P against its 125-day average, VIX against its 50-day - carrying the
# same score, so only one of each pair is kept.
COMPONENTS = [
    ("CNNFG_MOM",  "market_momentum_sp500", "F&G: market momentum"),
    ("CNNFG_STR",  "stock_price_strength",  "F&G: stock price strength"),
    ("CNNFG_BRD",  "stock_price_breadth",   "F&G: stock price breadth"),
    ("CNNFG_PC",   "put_call_options",      "F&G: put/call options"),
    ("CNNFG_VIX",  "market_volatility_vix", "F&G: market volatility"),
    ("CNNFG_JUNK", "junk_bond_demand",      "F&G: junk bond demand"),
    ("CNNFG_SAFE", "safe_haven_demand",     "F&G: safe haven demand"),
]

# The headline is the plain average of the seven. Intraday the components are
# stamped a few minutes apart from the headline, so allow some slack.
CHECK_TOLERANCE = 1.0


def catalogue() -> list:
    """macro_series rows, added on first run and never overwritten after."""
    rows = [(TOTAL, "CNN Fear & Greed", "Score (0-100)", "Daily",
             "Sentiment", "CNN", "fear_and_greed", 1, 1)]
    rows += [(sid, name, "Score (0-100)", "Daily", "Sentiment", "CNN",
              key, 0, 1) for sid, key, name in COMPONENTS]
    return rows


def _day(stamp) -> str:
    """UTC calendar date of a CNN timestamp: epoch milliseconds in the
    arrays and component blocks, an ISO string on the headline block."""
    if isinstance(stamp, str):
        when = datetime.fromisoformat(stamp)
    else:
        when = datetime.fromtimestamp(float(stamp) / 1000, tz=timezone.utc)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _score(raw):
    """A 0-100 float, or None. Anything off the scale is a parse problem, not
    a reading, so it is dropped rather than stored."""
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if 0.0 <= v <= 100.0 else None


def start_for(latest, full: bool = False, today: date | None = None) -> str:
    """Where to ask CNN to begin: a year back on a first run, otherwise the
    stored latest date less the revision window."""
    today = today or datetime.now(timezone.utc).date()
    if full or not latest or not isinstance(latest, str):
        return (today - timedelta(days=FIRST_DAYS)).isoformat()
    when = datetime.strptime(latest, "%Y-%m-%d").date()
    return (when - timedelta(days=REVISION_DAYS)).isoformat()


def fetch(start: str) -> dict:
    resp = requests.get(URL.format(start=start), headers=HEADERS,
                        timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from CNN")
    payload = resp.json()
    if "fear_and_greed_historical" not in payload:
        raise RuntimeError("unexpected response: no fear_and_greed_historical")
    return payload


def history(payload: dict) -> list:
    """[(date, score)] for the headline, one per date, oldest first.

    The array carries a midnight point for today and then the live point,
    both on the same date. Points are taken in time order and a later one
    overwrites an earlier one, so the live reading wins.

    The per-point rating labels are ignored: on the component arrays they
    describe the raw inputs and contradict the scores, and a zone is better
    worked out from the number anyway.
    """
    points = payload["fear_and_greed_historical"].get("data") or []
    by_day = {}
    for p in sorted(points, key=lambda p: p.get("x") or 0):
        v = _score(p.get("y"))
        if v is None or p.get("x") is None:
            continue
        by_day[_day(p["x"])] = v
    return sorted(by_day.items())


def components(payload: dict) -> list:
    """[(series_id, date, score)] for today's component readings."""
    out = []
    for sid, key, _name in COMPONENTS:
        block = payload.get(key) or {}
        v = _score(block.get("score"))
        if v is None or block.get("timestamp") is None:
            continue
        out.append((sid, _day(block["timestamp"]), v))
    return out


def check(payload: dict, comps: list) -> str:
    """The headline should be the mean of the seven. A warning, or ''.

    A warning does not stop the import: it goes into the run message on the
    Data page, where a methodology change at CNN would show up first.
    """
    if len(comps) != len(COMPONENTS):
        return f"only {len(comps)} of {len(COMPONENTS)} components parsed"
    head = _score((payload.get("fear_and_greed") or {}).get("score"))
    if head is None:
        return "no headline score"
    mean = sum(v for _sid, _d, v in comps) / len(comps)
    if abs(mean - head) > CHECK_TOLERANCE:
        return f"headline {head:.1f} vs component mean {mean:.1f}"
    return ""


def run(full: bool = False) -> dict:
    """Fetch and store. Returns a summary for the importer registry."""
    repo.ensure_series(catalogue())

    stored = repo.latest([TOTAL]).get(TOTAL)
    start = start_for(stored[0] if stored else None, full)

    payload = fetch(start)
    hist = history(payload)
    if not hist:
        raise RuntimeError("no headline history in the response")
    comps = components(payload)

    repo.save([(TOTAL, d, v) for d, v in hist])
    repo.save(comps)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for sid in [TOTAL] + [c[0] for c in comps]:
        repo.mark_updated(sid, now)

    last_day, last_val = hist[-1]
    message = (f"{len(hist)} days from {hist[0][0]}, {len(comps)} components. "
               f"{last_day}: {last_val:.1f}")
    warning = check(payload, comps)
    if warning:
        message += f". Check: {warning}"
    print(message)
    return {"saved": len(hist) + len(comps), "message": message}


if __name__ == "__main__":
    run(full="--full" in sys.argv)
