"""
Macro series: yields, spreads, inflation and the rest.

Separate from instruments and prices because a yield is not a price. It has no
currency, cannot be converted to GBP, and cannot be held - putting DGS10 in
the prices table would mean teaching every valuation path to ignore it.

Two tables: macro_series is the catalogue, macro_observations the data. The
dashboard flag on a series decides whether the Indicators page charts it;
everything collected is kept and shown in the series browser regardless.

A series can be backed by either store. FRED series read macro_observations;
a row with source='YAHOO' reads prices instead, with source_id holding the
fund_id. That indirection is what lets DXY sit in the browser beside DGS10
without copying price data into a second table and then having to keep the two
in step.
"""

from __future__ import annotations

import pandas as pd

from core import db

YAHOO = "YAHOO"


def _backing(series_id: str):
    """(source, key) for a series: where its observations live, and under what
    identifier. Falls back to treating an unknown id as a FRED code, which is
    what it will be if someone queries before the catalogue row exists."""
    df = db.query("""
        SELECT source, source_id FROM macro_series WHERE id = ?
    """, (series_id,))
    if df.empty:
        return "FRED", series_id
    source = (df["source"].iloc[0] or "FRED").upper()
    key = df["source_id"].iloc[0] or series_id
    return source, key


# --- Catalogue ------------------------------------------------------------

def series(active_only: bool = True, dashboard_only: bool = False):
    """The catalogue, with row counts and date ranges from whichever table
    backs each series."""
    sql = """
        SELECT s.id, s.name, s.units, s.frequency, s.category, s.source,
               s.source_id, s.dashboard, s.active, s.last_updated,
               CASE WHEN UPPER(COALESCE(s.source,'FRED')) = 'YAHOO'
                    THEN (SELECT COUNT(*) FROM prices p
                           WHERE p.fund_id = s.source_id)
                    ELSE (SELECT COUNT(*) FROM macro_observations o
                           WHERE o.series_id = s.id) END AS observations,
               CASE WHEN UPPER(COALESCE(s.source,'FRED')) = 'YAHOO'
                    THEN (SELECT MAX(p.date) FROM prices p
                           WHERE p.fund_id = s.source_id)
                    ELSE (SELECT MAX(o.date) FROM macro_observations o
                           WHERE o.series_id = s.id) END AS latest,
               CASE WHEN UPPER(COALESCE(s.source,'FRED')) = 'YAHOO'
                    THEN (SELECT MIN(p.date) FROM prices p
                           WHERE p.fund_id = s.source_id)
                    ELSE (SELECT MIN(o.date) FROM macro_observations o
                           WHERE o.series_id = s.id) END AS earliest
        FROM macro_series s
    """
    where = []
    if active_only:
        where.append("COALESCE(s.active, 1) = 1")
    if dashboard_only:
        where.append("COALESCE(s.dashboard, 0) = 1")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return db.query(sql + " ORDER BY s.category, s.id")


def to_fetch():
    """Series the FRED importer should pull, with each one's latest date.

    Yahoo-backed rows are excluded: their data arrives through the price
    importers, and asking FRED for a fund_id would fail.
    """
    df = db.query("""
        SELECT s.id, s.name, s.source_id, s.source,
               (SELECT MAX(o.date) FROM macro_observations o
                 WHERE o.series_id = s.id) AS latest
        FROM macro_series s
        WHERE COALESCE(s.active, 1) = 1
          AND UPPER(COALESCE(s.source, 'FRED')) = 'FRED'
        ORDER BY s.id
    """)
    return df.to_dict("records") if not df.empty else []


def set_dashboard(series_id: str, on: bool) -> int:
    return db.execute("UPDATE macro_series SET dashboard = ? WHERE id = ?",
                      (1 if on else 0, series_id))


def mark_updated(series_id: str, when: str) -> int:
    return db.execute("UPDATE macro_series SET last_updated = ? WHERE id = ?",
                      (when, series_id))


# --- Observations ---------------------------------------------------------

def observations(series_id: str, start: str = None) -> pd.DataFrame:
    """One series as a date-indexed frame with a value column.

    The column is named value whichever table it came from, so callers do not
    have to know which - the routing is the repo's problem, not theirs.
    """
    source, key = _backing(series_id)

    if source == YAHOO:
        sql = "SELECT date, close AS value FROM prices WHERE fund_id = ?"
    else:
        sql = ("SELECT date, value FROM macro_observations "
               "WHERE series_id = ?")
    params = [key]
    if start:
        sql += " AND date >= ?"
        params.append(start)

    df = db.query(sql + " ORDER BY date", tuple(params))
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


def frame(series_ids, start: str = None) -> pd.DataFrame:
    """Several series as one date-indexed frame, a column each.

    FRED-backed ids are read in a single query; Yahoo-backed ones are joined
    on afterwards. Mixed sets are rare enough that the extra queries do not
    matter, and keeping the common case to one round trip does.
    """
    if not series_ids:
        return pd.DataFrame()

    backing = {sid: _backing(sid) for sid in series_ids}
    fred = [sid for sid in series_ids if backing[sid][0] != YAHOO]
    yahoo = [sid for sid in series_ids if backing[sid][0] == YAHOO]

    out = pd.DataFrame()
    if fred:
        marks = ",".join("?" * len(fred))
        sql = f"""
            SELECT series_id, date, value FROM macro_observations
            WHERE series_id IN ({marks})
        """
        params = list(fred)
        if start:
            sql += " AND date >= ?"
            params.append(start)
        df = db.query(sql + " ORDER BY date", tuple(params))
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            out = df.pivot(index="date", columns="series_id", values="value")

    for sid in yahoo:
        one = observations(sid, start)
        if one.empty:
            continue
        col = one["value"].rename(sid)
        out = col.to_frame() if out.empty else out.join(col, how="outer")

    if out.empty:
        return out
    return out.reindex(columns=[s for s in series_ids])


def latest(series_ids) -> dict:
    """{series_id: (date, value)} for the most recent observation of each."""
    out = {}
    for sid in series_ids or []:
        df = observations(sid)
        if df.empty:
            continue
        out[sid] = (df.index[-1].strftime("%Y-%m-%d"),
                    float(df["value"].iloc[-1]))
    return out


def save(rows) -> int:
    """rows: [(series_id, date, value)]. Replaces on conflict.

    REPLACE rather than IGNORE because FRED revises: a figure published today
    can be restated next week, and keeping the first version forever would
    quietly diverge from the source.
    """
    if not rows:
        return 0
    return db.execute_many("""
        INSERT OR REPLACE INTO macro_observations (series_id, date, value)
        VALUES (?, ?, ?)
    """, rows)


def range_stats(series_id: str, days: int = 365) -> dict:
    """Min, max, latest and last change over a trailing window.

    For the regime strip, where a level means little without knowing where it
    sits in its own recent range: VIX at 17 is a different statement in a year
    that ranged 12 to 28 than in one that ranged 15 to 18.

    Accepts a bare fund_id as well as a catalogue id, so the strip can point
    at an instrument that has no macro_series row.
    """
    if series_id.startswith(("YF:", "COMPOSITE:")):
        table, key, col = "prices", "fund_id", "close"
        ident = series_id
    else:
        source, ident = _backing(series_id)
        if source == YAHOO:
            table, key, col = "prices", "fund_id", "close"
        else:
            table, key, col = "macro_observations", "series_id", "value"

    df = db.query(f"""
        SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM {table}
        WHERE {key} = ? AND date >= date('now', ?)
    """, (ident, f"-{days} days"))
    if df.empty or pd.isna(df["lo"].iloc[0]):
        return {}

    last = db.query(f"""
        SELECT date, {col} AS value FROM {table}
        WHERE {key} = ? ORDER BY date DESC LIMIT 2
    """, (ident,))
    if last.empty:
        return {}

    value = float(last["value"].iloc[0])
    prior = float(last["value"].iloc[1]) if len(last) > 1 else None
    return {"lo": float(df["lo"].iloc[0]), "hi": float(df["hi"].iloc[0]),
            "value": value, "date": last["date"].iloc[0],
            "change": None if prior is None else value - prior}
