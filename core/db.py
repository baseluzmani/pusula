"""
The only module in the project that opens a SQLite connection.

Everything else calls query() / execute() from here, or better still calls a
function in core/repo/ which calls these. No page file should import sqlite3.
"""
import sqlite3
from contextlib import contextmanager

import pandas as pd

from core import config


@contextmanager
def get_conn(readonly: bool = False):
    """
    Open a connection, guarantee it gets closed.

    Usage:
        with get_conn() as conn:
            conn.execute("UPDATE ...")

    readonly=True opens the file in read-only mode, so a mistake in a query
    physically cannot modify the database.
    """
    if readonly:
        uri = f"file:{config.DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=30)
    else:
        conn = sqlite3.connect(config.DB_PATH, timeout=30)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params: tuple | dict = ()) -> pd.DataFrame:
    """Run a SELECT, get a DataFrame back. Read-only connection."""
    with get_conn(readonly=True) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def execute(sql: str, params: tuple | dict = ()) -> int:
    """Run an INSERT/UPDATE/DELETE. Returns number of rows affected."""
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.rowcount


def execute_many(sql: str, rows: list) -> int:
    """Run the same statement over many rows in one transaction."""
    with get_conn() as conn:
        cur = conn.executemany(sql, rows)
        return cur.rowcount


# --- Introspection helpers, useful while migrating -----------------------

def list_tables() -> pd.DataFrame:
    return query(
        "SELECT name, "
        "(SELECT COUNT(*) FROM sqlite_master m2 WHERE m2.tbl_name = m1.name "
        " AND m2.type='index') AS index_count "
        "FROM sqlite_master m1 WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )


def table_columns(table: str) -> pd.DataFrame:
    return query(f"PRAGMA table_info({table})")


def row_count(table: str) -> int:
    df = query(f"SELECT COUNT(*) AS n FROM {table}")
    return int(df["n"].iloc[0])


def enable_wal() -> str:
    """
    Run once. Lets readers and one writer work at the same time, which stops
    'database is locked' errors when cron jobs and the dashboard overlap.
    The setting is stored in the file, so it persists.
    """
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
