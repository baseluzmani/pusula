"""
Bank of England series importer.

The interactive database serves CSV over a query string, which makes it the
one BoE source that does not need a spreadsheet parser. The yield-curve
workbooks carry more tenors but move between releases; this returns two
columns and a date.

One request per series, not a batch. A single bad code in a multi-code request
returns nothing at all, so a batch would fail silently and leave no way to
tell which series was at fault.

Incremental in the same way as the FRED importer: ask from the stored latest
date less a revision window, rather than pulling twenty years each run.

Usage
-----
    python3 -m importers.boe               # every active BOE series
    python3 -m importers.boe GBP10Y        # one
    python3 -m importers.boe --full        # ignore stored dates
"""

from __future__ import annotations

import csv
import io
import sys
import time
from datetime import datetime, timedelta

import requests

from core import db
from core.repo import macro as repo

BASE = ("https://www.bankofengland.co.uk/boeapps/database/"
        "_iadb-fromshowcolumns.asp")

# The endpoint returns HTML rather than an error for an unknown code, so a
# browser user-agent is not optional - without it some requests come back as
# a consent page instead of data.
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Pusula/1.0)"}

# The BoE restates occasionally, though far less than FRED.
REVISION_DAYS = 30

# Its history starts in the 1970s for some series; this is only the floor for
# a first import, and the endpoint clamps anything earlier.
FIRST_DATE = "01/Jan/1990"

PAUSE = 0.35
TIMEOUT = 30


def _fmt(date_str: str) -> str:
    """YYYY-MM-DD to the dd/Mmm/yyyy the endpoint expects."""
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d/%b/%Y")


def fetch(code: str, start: str = None) -> list:
    """
    [(date, value)] for one series code, oldest first.

    Dates come back as '02 Jan 2026'. Blank values are dropped rather than
    stored as zero: a missing observation and a rate of zero are different
    things, and one of them is now plausible.
    """
    params = {
        "csv.x": "yes",
        "Datefrom": _fmt(start) if start else FIRST_DATE,
        "Dateto": datetime.today().strftime("%d/%b/%Y"),
        "SeriesCodes": code,
        "CSVF": "TN",
        "UsingCodes": "Y",
        "VPD": "Y",
        "VFD": "N",
    }
    resp = requests.get(BASE, params=params, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")

    text = resp.text.strip()
    if not text or not text.upper().startswith("DATE"):
        # An unknown code returns the database's own HTML page.
        raise RuntimeError("no CSV returned - is the code right?")

    out = []
    for row in csv.reader(io.StringIO(text)):
        if not row or len(row) < 2 or row[0].upper() == "DATE":
            continue
        raw = row[1].strip()
        if not raw:
            continue
        try:
            when = datetime.strptime(row[0].strip(), "%d %b %Y")
            out.append((when.strftime("%Y-%m-%d"), float(raw)))
        except (TypeError, ValueError):
            continue
    return out


def _start_for(latest, full: bool) -> str | None:
    """None on a first import so the whole history arrives; otherwise the
    stored latest date less the revision window.

    A series with no observations yet returns NULL from MAX(date), which
    pandas types as float - so the value here is NaN rather than None, and
    checking the type is what makes both paths work.
    """
    if full or not latest or not isinstance(latest, str):
        return None
    when = datetime.strptime(latest, "%Y-%m-%d") - timedelta(days=REVISION_DAYS)
    return when.strftime("%Y-%m-%d")


def to_fetch(series_id: str = None):
    """Active BOE-sourced series, with each one's stored latest date."""
    df = db.query("""
        SELECT s.id, s.name, s.source_id,
               (SELECT MAX(o.date) FROM macro_observations o
                 WHERE o.series_id = s.id) AS latest
        FROM macro_series s
        WHERE COALESCE(s.active, 1) = 1
          AND UPPER(COALESCE(s.source, '')) = 'BOE'
        ORDER BY s.id
    """)
    rows = df.to_dict("records") if not df.empty else []
    if series_id:
        target = series_id.upper()
        rows = [r for r in rows
                if r["id"].upper() == target
                or (r["source_id"] or "").upper() == target]
    return rows


def run(series_id: str | None = None, full: bool = False) -> dict:
    """Fetch and store. Returns a summary for the importer registry."""
    wanted = to_fetch(series_id)
    if not wanted:
        return {"saved": 0,
                "message": f"No active BOE series"
                           f"{' matching ' + series_id if series_id else ''}"}

    print(f"Bank of England importer\n{len(wanted)} series"
          f"{' (full refetch)' if full else ''}\n")

    total, failed = 0, []
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for s in wanted:
        sid, code = s["id"], s["source_id"]
        start = _start_for(s.get("latest"), full)
        label = "all history" if start is None else f"from {start}"

        try:
            rows = fetch(code, start)
        except Exception as exc:                               # noqa: BLE001
            print(f"  x {sid:<12} {code:<10} {exc}")
            failed.append(sid)
            time.sleep(PAUSE)
            continue

        if not rows:
            print(f"  · {sid:<12} {code:<10} nothing returned ({label})")
            time.sleep(PAUSE)
            continue

        repo.save([(sid, d, v) for d, v in rows])
        repo.mark_updated(sid, now)
        total += len(rows)
        print(f"  + {sid:<12} {code:<10} {len(rows):>6,} rows  "
              f"{rows[0][0]} to {rows[-1][0]}  ({label})")
        time.sleep(PAUSE)

    message = f"{total:,} observations across {len(wanted) - len(failed)} series"
    if failed:
        message += f", {len(failed)} failed: {', '.join(failed[:5])}"
    print(f"\nDone. {message}")
    return {"saved": total, "failed": len(failed), "message": message}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run(args[0] if args else None, full="--full" in sys.argv)
