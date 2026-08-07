"""
Manually priced instruments.

Some holdings have no feed: a house is valued from Zoopla once a month, a
mortgage balance is read off a statement. They still need a row in prices for
every date, because everything downstream - snapshots, charts, the value
columns - looks a price up by date and would otherwise find nothing.

So an entry is expanded: writing 587,100 on 15 June fills every date from then
until the next entry, or until today if there is none. Back-date an entry and
it overwrites forward from that date to the next real one, leaving later
entries alone.

Filling on write rather than on read is the uglier of the two options - it
stores thousands of duplicate rows - but it keeps every existing consumer of
price_map working without knowing this instrument is special. The alternative
would mean teaching each of them to forward-fill.

Real entries are tracked in manual_prices so the two can be told apart. Rows
in prices are derived and can be rebuilt from it at any time.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from core import db

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS manual_prices (
    fund_id  TEXT NOT NULL,
    date     TEXT NOT NULL,
    value    REAL NOT NULL,
    note     TEXT,
    PRIMARY KEY (fund_id, date)
)
"""


def ensure_table():
    db.execute(TABLE_SQL)


def instruments():
    """Instruments priced by hand."""
    return db.query("""
        SELECT i.fund_id, i.name, i.category, h.units,
               (SELECT COUNT(*) FROM manual_prices m
                 WHERE m.fund_id = i.fund_id) AS entries,
               (SELECT MAX(m.date) FROM manual_prices m
                 WHERE m.fund_id = i.fund_id) AS last_entry
        FROM instruments i
        LEFT JOIN portfolio_holdings h ON h.fund_id = i.fund_id
        WHERE i.source = 'manual'
        ORDER BY i.fund_id
    """)


def entries(fund_id: str):
    return db.query("""
        SELECT date, value, note FROM manual_prices
        WHERE fund_id = ? ORDER BY date DESC
    """, (fund_id,))


def latest(fund_id: str):
    df = db.query("""
        SELECT date, value FROM manual_prices
        WHERE fund_id = ? ORDER BY date DESC LIMIT 1
    """, (fund_id,))
    if df.empty:
        return None, None
    return df["date"].iloc[0], float(df["value"].iloc[0])


def _next_entry_after(conn, fund_id, from_date):
    row = conn.execute("""
        SELECT MIN(date) FROM manual_prices
        WHERE fund_id = ? AND date > ?
    """, (fund_id, from_date)).fetchone()
    return row[0] if row and row[0] else None


def _fill(conn, fund_id, start, end, value):
    """Write one row per day from start to end inclusive."""
    d = datetime.strptime(start, "%Y-%m-%d").date()
    last = datetime.strptime(end, "%Y-%m-%d").date()
    while d <= last:
        iso = d.isoformat()
        conn.execute("""
            INSERT OR REPLACE INTO prices
                (fund_id, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        """, (fund_id, iso, value, value, value, value))
        d += timedelta(days=1)


def set_price(fund_id: str, on_date: str, value: float, note: str = None) -> int:
    """
    Record a value and fill prices forward from it.

    The fill stops at the next entry rather than running to today, so adding a
    back-dated figure corrects the window it belongs to without wiping the
    entries that come after it.
    """
    ensure_table()
    from core.db import get_conn

    with get_conn() as conn:
        conn.execute("""
            INSERT INTO manual_prices (fund_id, date, value, note)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(fund_id, date) DO UPDATE SET
                value = excluded.value, note = excluded.note
        """, (fund_id, on_date, float(value), note))

        nxt = _next_entry_after(conn, fund_id, on_date)
        end = ((datetime.strptime(nxt, "%Y-%m-%d").date() - timedelta(days=1))
               .isoformat() if nxt else date.today().isoformat())
        if end < on_date:
            end = on_date
        _fill(conn, fund_id, on_date, end, float(value))

    return 1


def delete_entry(fund_id: str, on_date: str) -> int:
    """Remove an entry and re-fill the gap from the one before it."""
    ensure_table()
    from core.db import get_conn

    with get_conn() as conn:
        conn.execute("DELETE FROM manual_prices WHERE fund_id = ? AND date = ?",
                     (fund_id, on_date))
        prev = conn.execute("""
            SELECT date, value FROM manual_prices
            WHERE fund_id = ? AND date < ? ORDER BY date DESC LIMIT 1
        """, (fund_id, on_date)).fetchone()

        nxt = _next_entry_after(conn, fund_id, on_date)
        end = ((datetime.strptime(nxt, "%Y-%m-%d").date() - timedelta(days=1))
               .isoformat() if nxt else date.today().isoformat())

        if prev:
            _fill(conn, fund_id, on_date, end, float(prev[1]))
        else:
            # Nothing earlier to carry forward, so the window has no value.
            conn.execute("""
                DELETE FROM prices
                WHERE fund_id = ? AND date >= ? AND date <= ?
            """, (fund_id, on_date, end))
    return 1


def rebuild(fund_id: str) -> int:
    """Regenerate every derived price row for one instrument.

    Used after editing entries directly, and as the repair if the two ever
    drift apart.
    """
    ensure_table()
    from core.db import get_conn

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT date, value FROM manual_prices
            WHERE fund_id = ? ORDER BY date
        """, (fund_id,)).fetchall()
        if not rows:
            return 0
        conn.execute("DELETE FROM prices WHERE fund_id = ?", (fund_id,))
        for i, (d, value) in enumerate(rows):
            if i + 1 < len(rows):
                end = (datetime.strptime(rows[i + 1][0], "%Y-%m-%d").date()
                       - timedelta(days=1)).isoformat()
            else:
                end = date.today().isoformat()
            if end < d:
                end = d
            _fill(conn, fund_id, d, end, float(value))
    return len(rows)


def extend_to_today() -> dict:
    """Carry every manual instrument's last value up to today.

    Called by the daily importers: without it a manually priced holding stops
    having a price the day after its last entry, and quietly drops out of the
    valuation until someone notices.
    """
    ensure_table()
    out = {}
    df = db.query("""
        SELECT DISTINCT fund_id FROM manual_prices
    """)
    today = date.today().isoformat()
    from core.db import get_conn

    with get_conn() as conn:
        for fund_id in df["fund_id"] if not df.empty else []:
            row = conn.execute("""
                SELECT date, value FROM manual_prices
                WHERE fund_id = ? ORDER BY date DESC LIMIT 1
            """, (fund_id,)).fetchone()
            if not row:
                continue
            last = conn.execute("""
                SELECT MAX(date) FROM prices WHERE fund_id = ?
            """, (fund_id,)).fetchone()[0]
            start = ((datetime.strptime(last, "%Y-%m-%d").date()
                      + timedelta(days=1)).isoformat() if last else row[0])
            if start > today:
                continue
            _fill(conn, fund_id, start, today, float(row[1]))
            out[fund_id] = f"{start} to {today}"
    return out
