#!/usr/bin/env python3
"""
Fill in instruments.vehicle: Stock / ETF / Fund / Other.

Vehicle says what kind of security a row is, separate from its thematic
asset_type (Tech-AI, Defence, Gold ... mix shares, ETFs and pension funds).
It drives the Vehicle filter on Portfolio -> Transactions and is edited on
Data -> Instruments.

Rules, first match wins
-----------------------
    CASH: / ASSET: / LIABILITY: / CALC: / BASKET: / ETF:     Other
    COMPOSITE:                                                Fund
    ISIN-shaped id (GB00B849FB47:GBP), Morningstar id (0P...) Fund
    asset_type Index, FX or Indicator                         Other
    asset_type Constituent (shares added for the Day
    estimate from Yahoo's quote type)                         Stock
    Yahoo symbol ending =X (FX), =F (future), or ^ (index)    Other
    any other Yahoo symbol, or an FT ticker such as
    LGUK:LSE:GBX (looked up as LGUK.L)                        Yahoo's quote type:
        EQUITY -> Stock, ETF -> ETF, MUTUALFUND -> Fund,
        CRYPTOCURRENCY / INDEX / CURRENCY / FUTURE -> Other

Gold ETCs and crypto ETPs count as ETF. Yahoo calls some London ETCs EQUITY,
so an EQUITY whose name looks like an exchange-traded product (ETC, ETP, ETF,
UCITS, Physical, an ETF issuer such as WisdomTree or iShares) is put down as
ETF, marked "by name" in the preview so it can be checked.

Rows nothing decides are listed under "Set by hand" - fill those in on
Data -> Instruments.

Only blank vehicles are filled, so a value set by hand is never changed;
--overwrite re-classifies every row. Re-run any time new instruments arrive.

Usage
-----
    cd ~/pusula
    .venv/bin/python scripts/seed_vehicle.py              # preview
    .venv/bin/python scripts/seed_vehicle.py --apply      # back up, write

    --traded      only instruments that have been traded or are held
    --no-yahoo    skip the Yahoo lookups (those rows go to "Set by hand")
    --overwrite   re-classify rows that already have a vehicle
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core import config, db
from core.repo import etf as etf_repo
from core.repo import standing

OTHER_PREFIXES = ("CASH:", "ASSET:", "LIABILITY:", "CALC:", "BASKET:",
                  "ETF:")
OTHER_TYPES = {"index", "fx", "indicator"}
ISIN = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9](:[A-Z]{3})?$")
MORNINGSTAR = re.compile(r"^0P[0-9A-Z]{8}(:[A-Z]{3})?$")

QUOTE_TYPE = {"EQUITY": "Stock", "ETF": "ETF", "MUTUALFUND": "Fund",
              "CRYPTOCURRENCY": "Other", "INDEX": "Other",
              "CURRENCY": "Other", "FUTURE": "Other"}

# Names that mark an exchange-traded product even when Yahoo says EQUITY.
ETP_NAME = re.compile(
    r"\b(ETC|ETP|ETN|ETF|UCITS|Physical|Bullion|WisdomTree|iShares|"
    r"Xtrackers|Amundi|SPDR|Vanguard|VanEck|Global X|HANetf|Lyxor|"
    r"CoinShares|21Shares|Bitwise|Invesco Physical|L&G|Legal & General "
    r"UCITS)\b", re.I)


# --- Classification -----------------------------------------------------------

def rule(row: dict) -> tuple[str | None, str, str | None]:
    """(vehicle, reason, yahoo_symbol) for one instruments row, without
    asking Yahoo. vehicle None + a symbol = look it up; vehicle None and no
    symbol = nothing decides it."""
    fid = str(row.get("fund_id") or "")
    up = fid.upper()
    atype = str(row.get("asset_type") or "").strip().lower()

    if up.startswith(OTHER_PREFIXES):
        return "Other", f"{fid.split(':')[0]}: row", None
    if up.startswith("COMPOSITE:"):
        return "Fund", "pension composite", None
    if ISIN.match(up):
        return "Fund", "ISIN id (FT fund)", None
    if MORNINGSTAR.match(up):
        return "Fund", "Morningstar id (FT fund)", None
    if atype in OTHER_TYPES:
        return "Other", f"type {row.get('asset_type')}", None
    if atype == "constituent":
        return "Stock", "type Constituent", None

    symbol = yahoo_symbol(row)
    if symbol:
        if symbol.endswith("=X"):
            return "Other", "FX cross", None
        if symbol.endswith("=F"):
            return "Other", "future", None
        if symbol.startswith("^"):
            return "Other", "index", None
        return None, "", symbol
    return None, "no rule and no Yahoo symbol", None


def yahoo_symbol(row: dict) -> str | None:
    """The Yahoo symbol to look the row up by, or None."""
    fid = str(row.get("fund_id") or "")
    if fid.upper().startswith("YF:"):
        src = str(row.get("source_id") or "").strip()
        return src if (src and str(row.get("source") or "") == "yahoo") \
            else fid[3:]
    # FT ticker form: SYMBOL:EXCHANGE or SYMBOL:EXCHANGE:CURRENCY.
    parts = fid.split(":")
    if len(parts) in (2, 3):
        built = etf_repo.yahoo_from_ft(":".join(parts[:2]))
        if built:
            return built[3:]
    return None


def from_quote(found: dict | None) -> tuple[str | None, str]:
    """(vehicle, reason) from a Yahoo lookup result."""
    if not found:
        return None, "Yahoo has no quote"
    qt = found.get("quote_type") or ""
    vehicle = QUOTE_TYPE.get(qt)
    if vehicle is None:
        return None, f"Yahoo quote type '{qt or '?'}'"
    if vehicle == "Stock" and ETP_NAME.search(found.get("name") or ""):
        return "ETF", "Yahoo EQUITY, ETF by name"
    return vehicle, f"Yahoo {qt}"


def lookup(symbol: str) -> dict | None:
    """Name and quote type from Yahoo, or None."""
    import yfinance as yf
    for attempt in range(2):
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:                                      # noqa: BLE001
            info = {}
        if info.get("quoteType"):
            return {"name": info.get("longName") or info.get("shortName"),
                    "quote_type": str(info.get("quoteType")).upper()}
        time.sleep(1.5 * (attempt + 1))
    return None


def classify(rows: pd.DataFrame, use_yahoo: bool = True,
             lookup_fn=None) -> pd.DataFrame:
    """Columns fund_id, name, asset_type, current, vehicle, reason, symbol.
    vehicle is None for rows nothing decided."""
    lookup_fn = lookup_fn or lookup
    out = []
    pending = []
    for r in rows.to_dict("records"):
        vehicle, reason, symbol = rule(r)
        rec = {"fund_id": r["fund_id"], "name": r.get("name"),
               "asset_type": r.get("asset_type"),
               "current": standing.clean_vehicle(r.get("vehicle")),
               "vehicle": vehicle,
               "reason": reason, "symbol": symbol}
        out.append(rec)
        if vehicle is None and symbol:
            if use_yahoo:
                pending.append(rec)
            else:
                rec["reason"] = "Yahoo lookup skipped (--no-yahoo)"

    for n, rec in enumerate(pending, 1):
        found = lookup_fn(rec["symbol"])
        rec["vehicle"], rec["reason"] = from_quote(found)
        print(f"  [{n}/{len(pending)}] {rec['symbol']}: "
              f"{rec['vehicle'] or '-'} ({rec['reason']})", flush=True)
    return pd.DataFrame(out, columns=["fund_id", "name", "asset_type",
                                      "current", "vehicle", "reason",
                                      "symbol"])


# --- Database -----------------------------------------------------------------

def candidates(traded_only: bool = False,
               overwrite: bool = False) -> pd.DataFrame:
    standing.ensure_vehicle_column()
    sql = ["SELECT fund_id, name, asset_type, vehicle, source, source_id "
           "FROM instruments WHERE 1 = 1"]
    if not overwrite:
        sql.append("AND (vehicle IS NULL OR TRIM(vehicle) = '')")
    if traded_only:
        sql.append("""
            AND (fund_id IN (SELECT DISTINCT fund_id FROM transactions)
                 OR fund_id IN (SELECT fund_id FROM portfolio_holdings
                                WHERE units > 0))
        """)
    sql.append("ORDER BY fund_id")
    return db.query("\n".join(sql))


def write(result: pd.DataFrame) -> int:
    rows = [(r.vehicle, r.fund_id) for r in result.itertuples()
            if r.vehicle and r.vehicle != r.current]
    if not rows:
        return 0
    with db.get_conn() as conn:
        conn.executemany("UPDATE instruments SET vehicle = ? "
                         "WHERE fund_id = ?", rows)
    return len(rows)


def _backup() -> Path:
    target_dir = Path(config.DB_PATH).parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / \
        f"funds_before_vehicle_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


# --- Report -------------------------------------------------------------------

def report(result: pd.DataFrame) -> None:
    decided = result[result["vehicle"].notna()]
    open_ = result[result["vehicle"].isna()]
    for vehicle in standing.VEHICLES:
        part = decided[decided["vehicle"] == vehicle]
        if part.empty:
            continue
        print(f"\n{vehicle} ({len(part)})")
        for r in part.itertuples():
            change = f"  [was {r.current}]" if r.current else ""
            print(f"  {r.fund_id:<28} {str(r.name or '')[:40]:<40} "
                  f"{r.reason}{change}")
    if not open_.empty:
        print(f"\nSet by hand on Data -> Instruments ({len(open_)})")
        for r in open_.itertuples():
            print(f"  {r.fund_id:<28} {str(r.name or '')[:40]:<40} "
                  f"{r.reason}")
    by_name = decided[decided["reason"].str.contains("by name")]
    if not by_name.empty:
        print(f"\nCheck: {len(by_name)} put down as ETF by name although "
              "Yahoo says EQUITY (listed above).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true",
                    help="back up funds.db and write the vehicles")
    ap.add_argument("--traded", action="store_true",
                    help="only instruments traded or held")
    ap.add_argument("--no-yahoo", action="store_true",
                    help="skip Yahoo lookups")
    ap.add_argument("--overwrite", action="store_true",
                    help="re-classify rows that already have a vehicle")
    args = ap.parse_args(argv)

    rows = candidates(args.traded, args.overwrite)
    if rows.empty:
        print("Nothing to do: every instrument in scope has a vehicle.")
        return 0
    print(f"{len(rows)} instruments to classify.")
    result = classify(rows, use_yahoo=not args.no_yahoo)
    report(result)

    decided = int(result["vehicle"].notna().sum())
    if not args.apply:
        print(f"\nPreview only: {decided} would be set. "
              "Run with --apply to write.")
        return 0
    backup = _backup()
    print(f"\nBackup: {backup}")
    n = write(result)
    print(f"Set vehicle on {n} instruments.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
