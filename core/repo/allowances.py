"""
Allowances: the figures behind pension carry-forward, the £100k taper and
ISA/JISA usage.

Two stores, deliberately different:

  user_settings['allowances']  a JSON blob, person -> tax year -> figures.
                               Kept as-is because the old dashboard reads and
                               writes the same key, so nothing is stranded if
                               you ever boot it, and because the field set has
                               changed twice already - a blob absorbs that
                               without a migration each time.

  allowance_limits             one row per tax year: pension annual allowance,
                               company-car BIK rate, whether the year is a
                               JISA year and whether it shows in the tables.
                               Relational because these are looked up by year
                               and edited as a grid.

Flat scalars - the ISA and JISA limits, the P11D value, the current tax year -
live in app_settings, via core.repo.settings.
"""

from __future__ import annotations

import json

from core import db
from core.repo import settings


# --- The figures ---------------------------------------------------------

def load() -> dict:
    """person -> tax year -> {salary, bonus, employee_pension, ...}."""
    df = db.query("SELECT value FROM user_settings WHERE key = 'allowances'")
    if df.empty:
        return {}
    try:
        return json.loads(df["value"].iloc[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def save(data: dict) -> int:
    return db.execute("""
        INSERT INTO user_settings (key, value) VALUES ('allowances', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (json.dumps(data),))


# --- Limits --------------------------------------------------------------

def limits():
    return db.query("""
        SELECT tax_year, pension_limit, car_bik_rate, jisa_year, show_in_tables
        FROM allowance_limits ORDER BY tax_year
    """)


def tax_years() -> list[str]:
    df = db.query("""
        SELECT tax_year FROM allowance_limits
        WHERE show_in_tables = 1 ORDER BY tax_year
    """)
    return list(df["tax_year"])


def jisa_years() -> list[str]:
    df = db.query("""
        SELECT tax_year FROM allowance_limits
        WHERE jisa_year = 1 ORDER BY tax_year
    """)
    return list(df["tax_year"])


def pension_limits() -> dict:
    df = db.query("SELECT tax_year, pension_limit FROM allowance_limits")
    return {r["tax_year"]: float(r["pension_limit"] or 0)
            for r in df.to_dict("records")}


def car_bik_rates() -> dict:
    df = db.query("SELECT tax_year, car_bik_rate FROM allowance_limits")
    return {r["tax_year"]: float(r["car_bik_rate"] or 0)
            for r in df.to_dict("records")}


def save_limits(rows) -> int:
    n = 0
    for r in rows:
        year = (r.get("tax_year") or "").strip()
        if not year:
            continue
        n += db.execute("""
            INSERT INTO allowance_limits
                (tax_year, pension_limit, car_bik_rate, jisa_year,
                 show_in_tables)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tax_year) DO UPDATE SET
                pension_limit  = excluded.pension_limit,
                car_bik_rate   = excluded.car_bik_rate,
                jisa_year      = excluded.jisa_year,
                show_in_tables = excluded.show_in_tables
        """, (year, _num(r.get("pension_limit")), _num(r.get("car_bik_rate")),
              int(r.get("jisa_year") or 0), int(r.get("show_in_tables") or 0)))
    return n


# --- Settings passthrough ------------------------------------------------

def current_year() -> str:
    return settings.get("ALLOWANCES_CURRENT_YEAR", "2026/27")


def isa_limit() -> float:
    return settings.get("ALLOWANCES_ISA_LIMIT", 20000)


def jisa_limit() -> float:
    return settings.get("ALLOWANCES_JISA_LIMIT", 9000)


def car_p11d() -> float:
    return settings.get("CAR_P11D", 0)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default
