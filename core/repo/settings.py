"""
Editable application settings.

The dividing line: anything that describes where the machine keeps its files
stays in core/config.py, because changing it from a web form would break the
app that is serving the form. Anything you might reasonably retune - a
threshold, a default date, a tax-year limit - lives here and is editable from
Data -> Config.

Values are stored as text with a declared type, and cast on read. get() falls
back to the default if the key is missing or the stored text will not cast,
so a bad edit degrades to the old behaviour rather than raising.
"""

from __future__ import annotations

import sqlite3

from core import db

_CASTS = {
    "int": int,
    "float": float,
    "str": str,
    "date": str,
}


def get(key: str, default=None):
    df = db.query("SELECT value, value_type FROM app_settings WHERE key = ?",
                  (key,))
    if df.empty:
        return default
    raw = df["value"].iloc[0]
    cast = _CASTS.get(df["value_type"].iloc[0], str)
    if raw is None or raw == "":
        return default
    try:
        return cast(raw)
    except (TypeError, ValueError):
        return default


def all_settings():
    return db.query("""
        SELECT key, value, value_type, description
        FROM app_settings ORDER BY key
    """)


def set_value(key: str, value) -> int:
    return db.execute("""
        UPDATE app_settings SET value = ? WHERE key = ?
    """, (str(value), key))


def set_many(pairs: dict) -> int:
    n = 0
    for k, v in pairs.items():
        n += set_value(k, v)
    return n


def ensure(key: str, value, value_type: str = "str",
           description: str = "") -> int:
    """Create a setting with its default value if it does not exist yet.

    Never overwrites, so an edit made under Data -> Config survives. Lets a
    feature register its own tunables instead of needing a hand-typed INSERT.
    Returns 1 if the row was created, 0 if it was already there.

    A failure (an unexpected table shape, a locked file) is swallowed and
    returns 0: callers read through get() with a default, so a missing row
    only means the setting is not editable yet - nothing breaks.
    """
    try:
        return db.execute("""
            INSERT INTO app_settings (key, value, value_type, description)
            SELECT ?, ?, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM app_settings WHERE key = ?)
        """, (key, str(value), value_type, description, key))
    except sqlite3.Error:
        return 0
