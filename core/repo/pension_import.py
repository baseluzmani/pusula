"""
Pension statement import: switches and contributions from one CSV.

The HSBC / WTW "transaction-details" export lists every fund movement split by
contribution type (salary sacrifice, employer match, AVC, employer core). The
same file format carries a monthly contribution (all positive) and a switch
(negatives and positives netting to about zero), so one path handles both:

    1. parse      the CSV into (fund name, value, date) rows
    2. aggregate  net value per fund across contribution types
    3. plan       map each fund to its composite and turn £ into units using
                  Pusula's own composite price on the trade date
    4. write      back up the database, insert the transactions, recalc

Only the £ value is used. The provider's units are on a different scale to
Pusula's composite prices and are never reconciled - see the pension switch
notes. Quantity is always |value| / composite price.

A full exit is the one case where the £ figures do not close a position
cleanly: the provider sells everything at its own price, while Pusula's units
are worth slightly more or less at the proxy price, so a few units are left
behind. "Close fully" sells every unit held and spreads the small £
difference across the buy legs in proportion to their size, so the switch
still nets to the same total.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from collections import Counter, OrderedDict
from datetime import datetime

from core import config, db
from core.repo import entry

# Composite prices that move more than this in a day almost always mean a
# component switched between pounds and pence on its source (Artemis, Nov
# 2026), not a real move.
JUMP_RATIO = 50.0
# A composite price older than this on the trade date is probably stale.
STALE_DAYS = 7

DEFAULT_ACCOUNT = "AB Pension"

DEFAULT_MAP = {
    "Asia Pacific (ex Japan) Equities - passive": "COMPOSITE:HSBC_ASIA_PAC",
    "Emerging Markets Equities - active": "COMPOSITE:HSBC_EM",
    "Shariah Law Equities - passive": "COMPOSITE:HSBC_SHARIA",
    "UK Equities - passive": "COMPOSITE:HSBC_UK_PASSIVE",
    "UK Equities - active": "COMPOSITE:HSBC_UK_ACTIVE",
    "European (ex UK) Equities - passive": "COMPOSITE:HSBC_EUROPE",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS pension_fund_map (
    provider_name TEXT PRIMARY KEY,
    fund_id       TEXT NOT NULL
);
"""


# --- Fund name mapping ------------------------------------------------------

def ensure_schema() -> None:
    """Create the mapping table and seed the known HSBC fund names.

    INSERT OR IGNORE, so a mapping changed by hand is never put back.
    """
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)
        conn.executemany("""
            INSERT OR IGNORE INTO pension_fund_map (provider_name, fund_id)
            VALUES (?, ?)
        """, list(DEFAULT_MAP.items()))


def fund_map() -> dict:
    """{provider fund name: fund_id}"""
    ensure_schema()
    df = db.query("SELECT provider_name, fund_id FROM pension_fund_map")
    return dict(zip(df["provider_name"], df["fund_id"]))


def save_mapping(provider_name: str, fund_id: str) -> None:
    ensure_schema()
    db.execute("""
        INSERT INTO pension_fund_map (provider_name, fund_id) VALUES (?, ?)
        ON CONFLICT(provider_name) DO UPDATE SET fund_id = excluded.fund_id
    """, (provider_name.strip(), fund_id))


def composite_options() -> list:
    """Dropdown options: every composite that can be priced."""
    df = db.query("""
        SELECT c.fund_id, COALESCE(c.display_name, c.fund_id) AS name
        FROM composites c
        WHERE EXISTS (SELECT 1 FROM composite_components cc
                      WHERE cc.composite_fund_id = c.fund_id)
        ORDER BY name
    """)
    return [{"label": r.name, "value": r.fund_id} for r in df.itertuples()]


# --- Parse --------------------------------------------------------------------

def _money(text: str) -> float | None:
    """'"−£30,389.51"' -> -30389.51. None if it is not a number."""
    s = (text or "").strip().strip('"')
    # The export uses U+2212 MINUS SIGN, not a hyphen.
    s = s.replace("−", "-").replace("–", "-")
    s = s.replace("£", "").replace(",", "").replace(" ", "")
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _iso(text: str) -> str | None:
    """'19/11/2026' -> '2026-11-19'."""
    try:
        return datetime.strptime((text or "").strip(), "%d/%m/%Y") \
            .strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_csv(text: str) -> tuple[list[dict], list[str]]:
    """
    Fund rows from the export, and anything that could not be read.

    Group headers ("Salary Sacrifice Contributions (Total Value ...)") have a
    single column and are skipped, as is the column header row.
    """
    rows, problems = [], []
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    for n, rec in enumerate(reader, start=1):
        if not rec or not any(c.strip() for c in rec):
            continue
        if len(rec) < 6:
            continue                                   # group header
        name = rec[0].strip()
        if name.lower() == "fund name":
            continue                                   # column header
        value = _money(rec[4])
        if value is None:
            problems.append(f"Line {n}: could not read value '{rec[4]}'")
            continue
        rows.append({"name": name, "currency": rec[1].strip() or "GBP",
                     "date": _iso(rec[3]), "value": value,
                     "type": rec[5].strip()})
    return rows, problems


def aggregate(rows: list[dict]) -> tuple["OrderedDict[str, float]", str | None]:
    """Net £ per fund, in file order, and the trade date the file implies.

    The date is the most common unit price date in the file - they are all
    the same in practice.
    """
    net: OrderedDict[str, float] = OrderedDict()
    for r in rows:
        net[r["name"]] = round(net.get(r["name"], 0.0) + r["value"], 2)
    dates = Counter(r["date"] for r in rows if r["date"])
    trade_date = dates.most_common(1)[0][0] if dates else None
    return net, trade_date


# --- Plan ---------------------------------------------------------------------

def _price(fund_id: str, on_date: str):
    """(date, close) on or before a date, and the close the day before it."""
    df = db.query("""
        SELECT date, close FROM prices
        WHERE fund_id = ? AND date <= ? AND close > 0
        ORDER BY date DESC LIMIT 2
    """, (fund_id, on_date))
    if df.empty:
        return None, None, None
    prev = float(df["close"].iloc[1]) if len(df) > 1 else None
    return str(df["date"].iloc[0]), float(df["close"].iloc[0]), prev


def plan(net: dict, trade_date: str, account: str | None,
         close_fully: list | set = ()) -> dict:
    """
    What would be written, without writing anything.

    Returns {"lines": [...], "net": £, "blocking": [...], "trade_date": ...}.
    Each line: name, fund_id, value (CSV £), value_used (after close-fully),
    price, price_date, quantity, type, held, close, warnings.
    A line with no mapping or no price blocks the import.
    """
    mapping = fund_map()
    close_fully = set(close_fully or ())
    lines, blocking = [], []

    for name, value in net.items():
        if abs(value) < 0.005:
            continue
        fund_id = mapping.get(name)
        line = {"name": name, "fund_id": fund_id, "value": value,
                "value_used": value, "price": None, "price_date": None,
                "quantity": None, "type": "BUY" if value > 0 else "SELL",
                "held": None, "close": False, "warnings": []}
        lines.append(line)

        if not fund_id:
            blocking.append(f"'{name}' is not mapped to a composite")
            continue

        pdate, price, prev = _price(fund_id, trade_date)
        if price is None:
            blocking.append(f"No price for {fund_id} on or before "
                            f"{trade_date}")
            continue
        line["price"], line["price_date"] = price, pdate

        if prev and (price / prev > JUMP_RATIO or prev / price > JUMP_RATIO):
            line["warnings"].append(
                f"Price jumped {prev:,.4g} → {price:,.4g} on {pdate} - "
                f"check a component for a pounds/pence switch")
        age = (datetime.strptime(trade_date, "%Y-%m-%d")
               - datetime.strptime(pdate, "%Y-%m-%d")).days
        if age > STALE_DAYS:
            line["warnings"].append(f"Price is {age} days old ({pdate})")

        line["held"] = entry.units_held(fund_id, account)

    # Close fully: sell every unit held, and move the £ difference between
    # the provider's figure and Pusula's value onto the buy legs.
    adjustment = 0.0
    for line in lines:
        if (line["type"] == "SELL" and line["fund_id"] in close_fully
                and line["price"] and line["held"]):
            full = -line["held"] * line["price"]
            adjustment += full - line["value"]           # extra £ sold
            line["value_used"] = full
            line["close"] = True

    buys = [l for l in lines if l["type"] == "BUY" and l["price"]]
    buy_total = sum(l["value"] for l in buys)
    if adjustment and buys and buy_total:
        for l in buys:
            l["value_used"] = l["value"] - adjustment * l["value"] / buy_total

    for line in lines:
        if not line["price"]:
            continue
        line["quantity"] = abs(line["value_used"]) / line["price"]
        if (line["type"] == "SELL" and line["held"] is not None
                and line["quantity"] > line["held"] * 1.001 + 1e-9):
            line["warnings"].append(
                f"Sells {line['quantity']:,.4f} units but only "
                f"{line['held']:,.4f} are held")
        # Within 2% of the whole holding either way: almost certainly a full
        # exit that the price difference will leave a sliver of, or oversell.
        if (line["type"] == "SELL" and not line["close"] and line["held"]
                and 1e-9 < abs(line["held"] - line["quantity"])
                < line["held"] * 0.02):
            line["warnings"].append(
                "Within 2% of the whole holding - "
                "tick Close fully if the provider sold it all")

    return {"lines": lines, "blocking": blocking, "trade_date": trade_date,
            "net": round(sum(l["value_used"] for l in lines), 2),
            "account": account}


# --- Write --------------------------------------------------------------------

def backup() -> str:
    """Copy of the database taken with SQLite's backup API. Kept in the
    nightly backup folder under its own prefix, so the nightly rotation does
    not delete it."""
    target_dir = config.DB_PATH.parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / \
        f"pre_pension_import_{datetime.now():%Y%m%d_%H%M%S}.db"
    src, dst = sqlite3.connect(config.DB_PATH), sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return str(target)


def write(p: dict) -> dict:
    """Insert the planned transactions and recalc every fund touched.

    Refuses a plan with blocking problems. Snapshot history is not rebuilt
    here - the page starts that in the background.
    """
    if p["blocking"]:
        raise ValueError("; ".join(p["blocking"]))
    lines = [l for l in p["lines"] if l["quantity"]]
    if not lines:
        raise ValueError("Nothing to import")

    path = backup()
    with db.get_conn() as conn:
        conn.executemany("""
            INSERT INTO transactions
                (fund_id, account, trade_date, type, quantity, price,
                 currency, fx_rate, commission)
            VALUES (?, ?, ?, ?, ?, ?, 'GBP', 1.0, 0)
        """, [(l["fund_id"], p["account"] or "", p["trade_date"], l["type"],
               float(l["quantity"]), float(l["price"])) for l in lines])

    units = {}
    for fund_id in dict.fromkeys(l["fund_id"] for l in lines):
        units[fund_id] = entry.recalc_holding(fund_id)
    return {"backup": path, "written": len(lines), "units": units}
