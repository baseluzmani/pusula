"""
Run logging. Every importer records one row here when it finishes, so the
Data page can answer "is my data current?" without reading log files.
"""
from datetime import datetime

import pandas as pd

from core import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    importer_id   TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    status        TEXT,               -- running | ok | failed
    rows_affected INTEGER DEFAULT 0,
    message       TEXT
);
CREATE INDEX IF NOT EXISTS ix_job_runs_importer
    ON job_runs (importer_id, started_at DESC);
"""


def ensure_schema() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def start(importer_id: str) -> int:
    ensure_schema()
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO job_runs (importer_id, started_at, status) "
            "VALUES (?, ?, 'running')",
            (importer_id, datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def finish(run_id: int, status: str, rows: int = 0, message: str = "") -> None:
    db.execute(
        "UPDATE job_runs SET finished_at = ?, status = ?, "
        "rows_affected = ?, message = ? WHERE id = ?",
        (datetime.now().isoformat(timespec="seconds"),
         status, rows, message[:500], run_id),
    )


def run_importer(importer) -> tuple[str, str]:
    """Execute an importer with logging and error capture."""
    if importer.run is None:
        return "failed", "Not implemented yet"
    run_id = start(importer.id)
    try:
        rows, message = importer.run()
        finish(run_id, "ok", rows, message)
        return "ok", f"{rows:,} rows. {message}"
    except Exception as exc:                      # noqa: BLE001
        finish(run_id, "failed", 0, str(exc))
        return "failed", str(exc)


def last_runs() -> pd.DataFrame:
    """Most recent run per importer."""
    ensure_schema()
    return db.query(
        "SELECT importer_id, started_at, finished_at, status, "
        "       rows_affected, message "
        "FROM job_runs r WHERE started_at = ("
        "  SELECT MAX(started_at) FROM job_runs r2 "
        "  WHERE r2.importer_id = r.importer_id)"
    )


def history(limit: int = 50) -> pd.DataFrame:
    ensure_schema()
    return db.query(
        "SELECT importer_id, started_at, status, rows_affected, message "
        "FROM job_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    )
