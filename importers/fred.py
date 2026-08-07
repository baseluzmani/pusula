"""
FRED series importer.

Fetches only what is missing. The version this replaces called
fred.get_series(), which returns a series' entire history, then discarded all
but the last thirty days - about 223,000 rows downloaded to save perhaps
thirty. FRED's API takes observation_start, so asking for the right window is
both simpler and faster.

No fredapi dependency. It is a thin wrapper over one REST endpoint, and going
direct is what makes the date parameters available in the first place. requests
is already here for OpenFIGI.

The revision window matters and is why this is not purely incremental: FRED
restates recent figures, particularly the monthly and quarterly series, so the
last REVISION_DAYS are always refetched and written with REPLACE.

Usage
-----
    python3 -m importers.fred                # every active series
    python3 -m importers.fred DGS10          # one series
    python3 -m importers.fred --full         # ignore stored dates, refetch all
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta

import requests

from core import config
from core.repo import macro as repo

BASE = "https://api.stlouisfed.org/fred/series/observations"

# Recent figures get revised, so always refetch this far back. Thirty days
# covers a monthly release cycle and the usual second estimate.
REVISION_DAYS = 45

# FRED is not aggressive about rate limits, but thirty-odd sequential calls
# deserve a pause rather than a burst.
PAUSE = 0.2
TIMEOUT = 30


def _api_key():
    key = getattr(config, "FRED_API_KEY", "") or ""
    if not key:
        raise RuntimeError(
            "FRED_API_KEY is not set. Add it to ~/pusula/.env - it used to sit "
            "in macro-data/config.py, which is in a git repo.")
    return key


def fetch(series_id: str, start: str = None) -> list:
    """
    [(date, value)] from FRED, oldest first.

    Missing observations come back as '.', which is not a number and not a
    zero - a yield of zero and a day the market was shut are different things,
    so they are dropped rather than stored.
    """
    params = {
        "series_id": series_id,
        "api_key": _api_key(),
        "file_type": "json",
    }
    if start:
        params["observation_start"] = start

    resp = requests.get(BASE, params=params, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:160]}")

    payload = resp.json()
    out = []
    for o in payload.get("observations", []):
        raw = o.get("value")
        if raw is None or raw == "." or raw == "":
            continue
        try:
            out.append((o["date"], float(raw)))
        except (TypeError, ValueError):
            continue
    return out


def _start_for(latest, full: bool) -> str | None:
    """Where to ask FRED to begin.

    None on a first import, so the whole history arrives. Otherwise the stored
    latest date less the revision window, which is a few hundred rows rather
    than a few thousand.

    A series with no observations yet returns NULL from MAX(date), and pandas
    types that column as float - so the value arriving here is NaN, not None.
    Checking the type rather than truthiness is what makes a first import work
    alongside an incremental one.
    """
    if full or not latest or not isinstance(latest, str):
        return None
    when = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=REVISION_DAYS)
    return when.strftime("%Y-%m-%d")


def run(series_id: str | None = None, full: bool = False) -> dict:
    """Fetch and store. Returns a summary for the importer registry."""
    wanted = repo.to_fetch()
    if series_id:
        target = series_id.upper()
        wanted = [s for s in wanted if s["id"].upper() == target]
        if not wanted:
            return {"saved": 0, "message": f"{series_id} is not an active "
                                           f"FRED series"}

    if not wanted:
        return {"saved": 0, "message": "No active FRED series"}

    print(f"FRED importer\n{len(wanted)} series"
          f"{' (full refetch)' if full else ''}\n")

    total = 0
    failed = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for s in wanted:
        sid = s["id"]
        start = _start_for(s.get("latest"), full)
        label = "all history" if start is None else f"from {start}"

        try:
            rows = fetch(s["source_id"] or sid, start)
        except Exception as exc:                                # noqa: BLE001
            print(f"  x {sid:<14} {exc}")
            failed.append(sid)
            time.sleep(PAUSE)
            continue

        if not rows:
            print(f"  · {sid:<14} nothing returned ({label})")
            time.sleep(PAUSE)
            continue

        saved = repo.save([(sid, d, v) for d, v in rows])
        repo.mark_updated(sid, now)
        total += len(rows)
        print(f"  + {sid:<14} {len(rows):>6,} rows  {rows[0][0]} to "
              f"{rows[-1][0]}  ({label})")
        time.sleep(PAUSE)

    message = f"{total:,} observations across {len(wanted) - len(failed)} series"
    if failed:
        message += f", {len(failed)} failed: {', '.join(failed[:5])}"
    print(f"\nDone. {message}")
    return {"saved": total, "failed": len(failed), "message": message}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(args[0] if args else None, full="--full" in sys.argv)
