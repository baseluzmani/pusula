# database.py
# Handles all database operations.
# Uses four tables:
#   instruments        — static info per fund (name, type, currency, unit, category)
#   prices             — daily OHLCV price data, linked by fund_id
#   pension_holdings   — top 10 holdings scraped from FT, per fund per date
#   pension_allocations— user-defined allocation % per fund (for composite view)

import sqlite3
import os


def get_connection():
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect("data/funds.db")
    conn.row_factory = sqlite3.Row
    return conn


def create_table(conn):
    """Create prices and instruments tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS instruments (
            fund_id     TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            asset_type  TEXT,
            currency    TEXT,
            price_unit  TEXT,
            category    TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id     TEXT NOT NULL,
            date        TEXT NOT NULL,
            open        REAL,
            high        REAL,
            low         REAL,
            close       REAL,
            volume      INTEGER,
            UNIQUE(fund_id, date)
        )
    """)
    conn.commit()


def create_holdings_table(conn):
    """Create pension_holdings and pension_allocations tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pension_holdings (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            fund_id      TEXT NOT NULL,
            fund_name    TEXT NOT NULL,
            scraped_date TEXT NOT NULL,
            rank         INTEGER,
            name         TEXT NOT NULL,
            ticker       TEXT,
            weight_pct   REAL,
            UNIQUE(fund_id, scraped_date, rank)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_pension_fund_date
            ON pension_holdings(fund_id, scraped_date)
    """)
    # User-defined allocation per fund — what % of the pension pot each fund represents
    # Populated manually via the dashboard Pension tab
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pension_allocations (
            fund_id     TEXT PRIMARY KEY,
            allocation  REAL NOT NULL DEFAULT 0.0
        )
    """)
    conn.commit()


def get_latest_date(conn, fund_id):
    """Return the most recent date for a fund, or None if no data exists."""
    row = conn.execute(
        "SELECT MAX(date) as latest FROM prices WHERE fund_id = ?",
        (fund_id,)
    ).fetchone()
    return row["latest"] if row["latest"] else None


def get_latest_holdings_date(conn, fund_id):
    """Return the most recent holdings scrape date for a fund."""
    row = conn.execute(
        "SELECT MAX(scraped_date) as latest FROM pension_holdings WHERE fund_id = ?",
        (fund_id,)
    ).fetchone()
    return row["latest"] if row and row["latest"] else None


def fund_exists(conn, fund_id):
    """Return True if any price data exists for this fund_id."""
    row = conn.execute(
        "SELECT COUNT(*) FROM prices WHERE fund_id = ?",
        (fund_id,)
    ).fetchone()
    return row[0] > 0


def save_prices(conn, fund_id, fund_name, rows, asset_type=None):
    """Insert new price rows — skips duplicates silently.
    Also upserts instrument record if not already present.
    """
    existing = conn.execute(
        "SELECT fund_id FROM instruments WHERE fund_id = ?", (fund_id,)
    ).fetchone()
    if not existing:
        conn.execute("""
            INSERT OR IGNORE INTO instruments (fund_id, name, asset_type)
            VALUES (?, ?, ?)
        """, (fund_id, fund_name, asset_type))

    saved_count = 0
    for row in rows:
        result = conn.execute("""
            INSERT OR IGNORE INTO prices
                (fund_id, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            fund_id, row["date"],
            row["open"], row["high"], row["low"], row["close"], row["volume"],
        ))
        saved_count += result.rowcount
    conn.commit()
    return saved_count


def save_holdings(conn, fund_id, fund_name, holdings):
    """Insert top 10 holdings for a fund for today's date.
    Replaces existing rows for the same fund_id + scraped_date.
    Returns number of rows saved.
    """
    if not holdings:
        return 0

    scraped_date = holdings[0]["scraped_date"]

    # Delete existing rows for this fund+date before re-inserting
    # (ensures a fresh scrape always replaces stale data for same date)
    conn.execute("""
        DELETE FROM pension_holdings
        WHERE fund_id = ? AND scraped_date = ?
    """, (fund_id, scraped_date))

    saved_count = 0
    for h in holdings:
        result = conn.execute("""
            INSERT OR REPLACE INTO pension_holdings
                (fund_id, fund_name, scraped_date, rank, name, ticker, weight_pct)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            fund_id, fund_name,
            h["scraped_date"], h["rank"], h["name"],
            h.get("ticker", ""), h.get("weight_pct"),
        ))
        saved_count += result.rowcount
    conn.commit()
    return saved_count


def get_holdings(conn, fund_id, date=None):
    """Return holdings for a fund. If date is None, uses the latest available."""
    if date is None:
        row = conn.execute(
            "SELECT MAX(scraped_date) FROM pension_holdings WHERE fund_id = ?",
            (fund_id,)
        ).fetchone()
        date = row[0] if row and row[0] else None
    if not date:
        return []
    rows = conn.execute("""
        SELECT rank, name, ticker, weight_pct, scraped_date
        FROM pension_holdings
        WHERE fund_id = ? AND scraped_date = ?
        ORDER BY rank
    """, (fund_id, date)).fetchall()
    return [dict(r) for r in rows]


def get_all_holdings_latest(conn):
    """Return latest holdings for all funds as a flat list of dicts."""
    rows = conn.execute("""
        SELECT p.*
        FROM pension_holdings p
        INNER JOIN (
            SELECT fund_id, MAX(scraped_date) as latest
            FROM pension_holdings
            GROUP BY fund_id
        ) latest ON p.fund_id = latest.fund_id AND p.scraped_date = latest.latest
        ORDER BY p.fund_id, p.rank
    """).fetchall()
    return [dict(r) for r in rows]


def get_allocations(conn):
    """Return dict of fund_id → allocation %."""
    rows = conn.execute("SELECT fund_id, allocation FROM pension_allocations").fetchall()
    return {r["fund_id"]: r["allocation"] for r in rows}


def save_allocation(conn, fund_id, allocation):
    """Upsert allocation % for a fund."""
    conn.execute("""
        INSERT INTO pension_allocations (fund_id, allocation)
        VALUES (?, ?)
        ON CONFLICT(fund_id) DO UPDATE SET allocation = excluded.allocation
    """, (fund_id, allocation))
    conn.commit()


def update_fund_name(conn, fund_id, fund_name):
    """Update the display name in instruments table."""
    conn.execute(
        "UPDATE instruments SET name = ? WHERE fund_id = ?",
        (fund_name, fund_id),
    )
    conn.commit()


def update_asset_type(conn, fund_id, asset_type):
    """Update the asset type in instruments table."""
    conn.execute(
        "UPDATE instruments SET asset_type = ? WHERE fund_id = ?",
        (asset_type, fund_id),
    )
    conn.commit()