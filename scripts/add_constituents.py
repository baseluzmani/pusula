#!/usr/bin/env python3
"""
Create the Yahoo instruments the Day estimate needs, in one go.

Adding a ticker on Data -> Instruments is one row at a time. This finds every
symbol the estimated funds need and has no instruments row yet, checks each
one on Yahoo, and inserts them together:

    holdings     every holding of the funds in etf_est_targets (SEMI, the
                 HSBC pension composites) with a Yahoo symbol - from the
                 ticker map, or for a pension holding the map does not know,
                 built from its FT ticker (7203:TYO -> 7203.T). The same
                 holdings the Fund compare tab shows.
    baskets      the futures behind UC14
    series       each target's own price series and residual proxy
    FX           YF:GBP<CCY>=X for every currency the above trade in

Holdings with neither - no ticker-map record and no usable FT ticker, such
as Alphabet Class C, which FT lists without one - are listed so they can be
fixed in ETF -> Ticker map (FT code or Yahoo ID), after which a re-run picks
them up. A symbol built from an FT ticker that Yahoo does not know is listed
under "not found" and never added.

New rows
--------
    fund_id     YF:<symbol>, source yahoo, source_id <symbol>, active 1
    name        Yahoo's long name
    asset_type  Constituent for shares (a type of their own, so they stay
                apart from the stocks you hold: Markets can filter them, and
                one UPDATE can retype or deactivate them), Indicator for
                futures, ETF, Index, Fund by Yahoo's quote type, FX crosses
                as YF:GBPUSD=X is set up
    currency    from Yahoo. Pence-quoted lines (GBp) get GBP + pence; cents-
                quoted futures (USX) get USD + cents
    price_unit  pence / cents as above, else the currency's unit name
    category    company name for shares (the instruments taxonomy)
    provider    left empty - a provider marks an ETF whose holdings are
                imported, and would change how the ETF tab treats the row

Instruments that exist but are inactive are listed; --activate switches them
back on, since an inactive row gets no prices.

Composite components with no holdings
-------------------------------------
A pension line whose FT page lists no holdings - HSBC Pension Sharia's
0P0001EWE2 - shows "No data" in Fund compare and cannot be estimated. Its
holdings can be read from the underlying fund instead: --link sets
instruments.holdings_id, so both Fund compare and the FT scraper use the
underlying fund's holdings. The preview lists such components and the
pension funds whose names look like their underlying.

    --link 0P0001EWE2=<underlying fund_id>      (with --apply)

Usage
-----
    cd ~/pusula
    .venv/bin/python scripts/add_constituents.py              # preview
    .venv/bin/python scripts/add_constituents.py --apply      # back up, insert
    .venv/bin/python scripts/add_constituents.py --apply --activate
    .venv/bin/python scripts/add_constituents.py --apply \
        --link 0P0001EWE2=LU0466842654:USD

    --all-etfs        every ETF with holdings, not only the estimated ones
    --target KEY      only this target (e.g. COMPOSITE:HSBC_EM); the
                      Data -> ETF targets page runs it this way
    --history         after --apply, fetch Yahoo history for the symbols
                      added or activated, and for needed instruments that
                      have no prices yet
    --min-weight 0.2  leave out holdings below this weight, percent
                      (default 0.1 - the long tail of an ETF adds rows to
                      every price import for almost no accuracy)
    --type Stock      asset_type for shares instead of Constituent

Afterwards
----------
    .venv/bin/python -m importers.yahoo_prices    # two years of history
    .venv/bin/python -m importers.live_prices     # today's prices
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd                                            # noqa: E402

from core import config, db, fundest as fe, universe           # noqa: E402
from core.background import FINISHED                           # noqa: E402
from core.repo import etf as etf_repo                          # noqa: E402
from core.repo import funds as funds_repo                      # noqa: E402
from core.repo import fundest as est_repo                      # noqa: E402

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

LOOKUP_SLEEP = 0.25         # seconds between Yahoo calls
FX_TEMPLATE = "YF:GBPUSD=X"

# Yahoo currency code -> (currency, price_unit) when the quote is in a
# minor unit. Yahoo writes pence as "GBp", which is checked before
# upper-casing.
MINOR = {"GBX": ("GBP", "pence"), "USX": ("USD", "cents"),
         "ZAC": ("ZAR", "cents"), "ILA": ("ILS", "agorot")}
UNIT = {"GBP": "pound", "USD": "dollar", "EUR": "euro", "TRY": "lira",
        "JPY": "yen", "KRW": "won", "CNY": "yuan", "CHF": "franc",
        "INR": "rupee", "ZAR": "rand", "BRL": "real", "MXN": "peso",
        "SEK": "krona", "NOK": "krone", "DKK": "krone", "PLN": "zloty",
        "HKD": "dollar", "TWD": "dollar", "SGD": "dollar", "AUD": "dollar",
        "CAD": "dollar", "NZD": "dollar"}
TYPE_FOR = {"FUTURE": "Indicator", "ETF": "ETF", "INDEX": "Index",
            "MUTUALFUND": "Fund", "CRYPTOCURRENCY": "Crypto"}


# --- What is needed ---------------------------------------------------------

def _target_rows(only: str | None) -> list[dict]:
    """Active targets, or just ``only`` (active or not)."""
    if only:
        row = est_repo.target(only)
        if row is None:
            raise SystemExit(f"--target: {only} is not in etf_est_targets")
        return [row]
    return est_repo.targets().to_dict("records")


def _needed(all_etfs: bool, min_weight: float, only: str | None = None
            ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(wanted, unmapped). wanted: fund_id, name, weight_pct, used_by.
    unmapped: holdings with no Yahoo symbol."""
    wanted, unmapped = [], []
    for row in _target_rows(only):
        hold, _ = est_repo.constituents(row)
        for h in hold.itertuples():
            w = float(h.weight_pct or 0)
            fid = h.fund_id if isinstance(h.fund_id, str) else None
            if row["kind"] != "basket" and w < min_weight:
                continue
            if fid:
                wanted.append((fid, h.name, w, row["label"]))
            else:
                unmapped.append((h.name, getattr(h, "ticker", "") or "-",
                                 w, row["label"]))
        for fid in (row.get("actual_fund_id"), row.get("residual_fund_id")):
            if isinstance(fid, str) and fid.startswith("YF:"):
                wanted.append((fid, fid[3:], None, row["label"]))

    if all_etfs:
        for etf in etf_repo.list_etfs():
            df, label, _ = funds_repo.holdings_for(f"ETF:{etf}")
            if df.empty:
                continue
            for h in df.itertuples():
                w = float(h.weight_pct or 0)
                if w < min_weight:
                    continue
                fid = est_repo.yahoo_fund_id(h.yahoo_id)
                if fid:
                    wanted.append((fid, h.name, w, label))
                else:
                    unmapped.append((h.name, h.ticker or "-", w, label))

    w = pd.DataFrame(wanted, columns=["fund_id", "name", "weight_pct",
                                      "used_by"])
    if not w.empty:
        w = (w.groupby("fund_id", sort=False)
              .agg(name=("name", "first"),
                   weight_pct=("weight_pct", "max"),
                   used_by=("used_by",
                            lambda s: ", ".join(sorted(set(s)))[:70]))
              .reset_index())
    u = pd.DataFrame(unmapped, columns=["name", "ticker", "weight_pct",
                                        "used_by"])
    if not u.empty:
        u = (u.groupby("name", sort=False)
              .agg(ticker=("ticker", "first"),
                   weight_pct=("weight_pct", "max"),
                   used_by=("used_by",
                            lambda s: ", ".join(sorted(set(s)))[:70]))
              .reset_index()
              .sort_values("weight_pct", ascending=False))
    return w, u


_COMMON = {"fund", "funds", "index", "equity", "global", "class", "share",
           "income", "accumulation", "pension", "world", "growth"}


def _words(text) -> set:
    return {w for w in str(text or "").lower().replace("-", " ").split()
            if len(w) >= 5 and w not in _COMMON}


def _holdingless(only: str | None = None) -> list[dict]:
    """Components of the estimated composites with no holdings to read, and
    pension funds whose names look like their underlying."""
    funds = funds_repo.list_funds()
    names = db.query("SELECT fund_id, name FROM instruments")
    name_of = dict(zip(names["fund_id"], names["name"]))
    out = []
    for row in _target_rows(only):
        if not str(row["target"]).startswith("COMPOSITE:"):
            continue
        comp = universe.composite(row["target"]) or {}
        for c in comp.get("components", []):
            fid = c["fund_id"]
            if funds_repo.effective_date(fid):
                continue
            words = _words(name_of.get(fid))
            cands = [f"{r.fund_id}  ({r.fund_name})" for r in funds.itertuples()
                     if r.fund_id != fid and words & _words(r.fund_name)]
            out.append({"composite": row["label"], "component": fid,
                        "name": name_of.get(fid, ""), "candidates": cands})
    return out


def _parse_links(values) -> list[tuple[str, str]]:
    links = []
    for v in values or []:
        left, sep, right = v.partition("=")
        if not sep or not left.strip() or not right.strip():
            raise SystemExit(f"--link expects FUND_ID=HOLDINGS_FUND_ID, "
                             f"got {v!r}")
        links.append((left.strip(), right.strip()))
    return links


def _existing() -> pd.DataFrame:
    return db.query("SELECT fund_id, COALESCE(active, 1) AS active, "
                    "asset_type, price_unit, category FROM instruments")


# --- Yahoo ------------------------------------------------------------------

def _lookup(symbol: str) -> dict | None:
    """Name, quote type and currency from Yahoo, or None if Yahoo has no
    price for the symbol."""
    import yfinance as yf
    for attempt in range(2):
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:                                      # noqa: BLE001
            info = {}
        if info.get("regularMarketPrice") is not None or \
                info.get("previousClose") is not None:
            return {"name": info.get("longName") or info.get("shortName"),
                    "quote_type": str(info.get("quoteType") or "").upper(),
                    "currency": str(info.get("currency") or "")}
        time.sleep(1.5 * (attempt + 1))
    return None


def _units(currency: str) -> tuple[str | None, str | None]:
    code = (currency or "").strip()
    if code == "GBp":
        return "GBP", "pence"
    up = code.upper()
    if up in MINOR:
        return MINOR[up]
    if not up:
        return None, None
    return up, UNIT.get(up, up.lower())


def _row(fund_id: str, fallback_name: str, found: dict, share_type: str,
         fx_style: dict) -> dict:
    symbol = fund_id[3:]
    currency, unit = _units(found["currency"])
    qt = found["quote_type"]
    name = found["name"] or fallback_name or symbol
    if qt == "CURRENCY" or symbol.endswith("=X"):
        return {"fund_id": fund_id, "name": name,
                "asset_type": fx_style.get("asset_type") or "FX",
                "currency": currency,
                "price_unit": fx_style.get("price_unit") or unit,
                "category": fx_style.get("category") or "FX",
                "source_id": symbol}
    # Yahoo does not always say FUTURE for a front-month contract (CT=F came
    # back without it); the =F suffix is reliable.
    if symbol.endswith("=F"):
        qt = "FUTURE"
    asset_type = TYPE_FOR.get(qt, share_type)
    if qt == "FUTURE":
        name = est_repo.FUTURE_NAMES.get(fund_id, name)
    category = name if asset_type in (share_type, "Stock") else \
        ("Commodity" if asset_type == "Indicator" else name)
    return {"fund_id": fund_id, "name": name, "asset_type": asset_type,
            "currency": currency, "price_unit": unit, "category": category,
            "source_id": symbol}


# --- Main ---------------------------------------------------------------------

def _backup() -> Path:
    target_dir = Path(config.DB_PATH).parent / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"funds_before_constituents_{datetime.now():%Y%m%d_%H%M%S}.db"
    src = sqlite3.connect(config.DB_PATH)
    dst = sqlite3.connect(target)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--activate", action="store_true",
                    help="switch inactive instruments the estimate needs "
                         "back on")
    ap.add_argument("--all-etfs", action="store_true")
    ap.add_argument("--target", default=None,
                    help="only this etf_est_targets key")
    ap.add_argument("--history", action="store_true",
                    help="with --apply, fetch Yahoo history for what was "
                         "added or activated")
    ap.add_argument("--min-weight", type=float, default=0.1,
                    help="leave out holdings below this weight, percent "
                         "(default 0.1; 0 for all)")
    ap.add_argument("--type", default="Constituent",
                    help="asset_type for shares (default Constituent)")
    ap.add_argument("--link", action="append", metavar="FUND=UNDERLYING",
                    help="read FUND's holdings from UNDERLYING (sets "
                         "instruments.holdings_id); repeatable")
    args = ap.parse_args(argv)
    links = _parse_links(args.link)

    pd.set_option("display.width", 160)
    pd.set_option("display.max_rows", 500)
    pd.set_option("display.max_colwidth", 48)

    # Links first: once written, the linked funds' holdings count as needed
    # in this same run.
    backup = None
    if links:
        have_ids = set(_existing()["fund_id"])
        known_funds = set(funds_repo.list_funds()["fund_id"])
        for fund, under in links:
            problems = []
            if fund not in have_ids:
                problems.append(f"{fund} is not in instruments")
            if under not in known_funds:
                problems.append(f"{under} has no holdings in "
                                "pension_holdings")
            if problems:
                raise SystemExit("--link: " + "; ".join(problems))
            print(f"Link: {fund} reads its holdings from {under}")
        if args.apply:
            backup = _backup()
            print(f"Backup: {backup}")
            db.execute_many("UPDATE instruments SET holdings_id = ? "
                            "WHERE fund_id = ?", [(u, f) for f, u in links])
            print(f"Linked {len(links)}.\n")
        else:
            print("(Its holdings are added to the list below once the link "
                  "is written with --apply.)\n")

    if args.target and args.all_etfs:
        raise SystemExit("--target and --all-etfs do not go together")
    wanted, unmapped = _needed(args.all_etfs, args.min_weight, args.target)
    existing = _existing()
    have = set(existing["fund_id"])
    inactive = set(existing.loc[existing["active"] == 0, "fund_id"])

    new = wanted[~wanted["fund_id"].isin(have)] if not wanted.empty \
        else wanted
    off = wanted[wanted["fund_id"].isin(inactive)] if not wanted.empty \
        else wanted
    print(f"Needed by {args.target or 'the Day estimate'}: "
          f"{len(wanted)} symbols, "
          f"{len(wanted) - len(new)} already in instruments.")

    fx_style = {}
    tmpl = existing[existing["fund_id"] == FX_TEMPLATE]
    if not tmpl.empty:
        fx_style = {k: tmpl[k].iloc[0] for k in
                    ("asset_type", "price_unit", "category")}

    queue = new.to_dict("records") if not new.empty else []
    # Currencies of needed symbols that already exist need their cross too.
    if not wanted.empty:
        marks = ",".join("?" * len(wanted))
        ccy = db.query(f"SELECT DISTINCT currency FROM instruments "
                       f"WHERE fund_id IN ({marks}) AND currency IS NOT NULL",
                       tuple(wanted["fund_id"]))
        for c in ccy["currency"]:
            cross = fe.fx_fund_id(c)
            if cross and cross not in have:
                queue.append({"fund_id": cross, "name": cross[3:],
                              "weight_pct": None, "used_by": f"FX for {c}"})

    rows, not_found, seen = [], [], set()
    print(f"\nChecking {len(queue)} new symbol(s) on Yahoo...", flush=True)
    done = 0
    while queue:
        item = queue.pop(0)
        if item["fund_id"] in seen:
            continue
        seen.add(item["fund_id"])
        done += 1
        symbol = item["fund_id"][3:]
        found = _lookup(symbol)
        time.sleep(LOOKUP_SLEEP)
        total = done + len([q for q in queue if q["fund_id"] not in seen])
        print(f"  [{done}/{total}] {symbol:<14} "
              + (f"ok - {found['name']}" if found else "not found"),
              flush=True)
        if not found:
            not_found.append(item)
            continue
        row = _row(item["fund_id"], item["name"], found, args.type, fx_style)
        row.update({"weight_pct": item["weight_pct"],
                    "used_by": item["used_by"]})
        rows.append(row)
        # A new currency brings its GBP cross with it.
        cross = fe.fx_fund_id(row["currency"])
        if cross and cross not in have and cross not in seen:
            queue.append({"fund_id": cross, "name": cross[3:],
                          "weight_pct": None,
                          "used_by": f"FX for {row['currency']}"})

    add = pd.DataFrame(rows)
    if not add.empty:
        print(f"\nTo add ({len(add)}):")
        print(add[["fund_id", "name", "asset_type", "currency", "price_unit",
                   "weight_pct", "used_by"]].to_string(index=False))
    else:
        print("\nNothing to add.")

    if not_found:
        print(f"\nNot found on Yahoo ({len(not_found)}) - nothing added for "
              "these. Check the Yahoo ID in ETF -> Ticker map, or the FT "
              "ticker it was built from (ETF -> Ticker map -> Unmapped fund "
              "holdings):")
        print(pd.DataFrame(not_found)[["fund_id", "name", "weight_pct",
                                       "used_by"]].to_string(index=False))
    if not off.empty:
        print(f"\nIn instruments but inactive ({len(off)}) - no prices are "
              "fetched for these" + ("" if args.activate
                                     else "; --activate switches them on")
              + ":")
        print(off[["fund_id", "name", "weight_pct", "used_by"]]
              .to_string(index=False))
    if not unmapped.empty:
        print(f"\nNo Yahoo symbol ({len(unmapped)}) - not in the ticker map "
              "and no FT ticker that converts (ticker shown as the holdings "
              "give it; '-' = none):")
        print(unmapped.to_string(index=False))

    linked = {f for f, _ in links}
    gaps = [g for g in _holdingless(args.target)
            if g["component"] not in linked]
    if gaps:
        print(f"\nComposite components with no holdings ({len(gaps)}) - "
              "link each to its underlying fund with --link:")
        for g in gaps:
            print(f"  {g['composite']}: {g['component']}  {g['name']}")
            for c in g["candidates"][:5]:
                print(f"      looks like  {c}")

    if not args.apply:
        print("\nPreview only. Run with --apply to write.")
        return 0
    if add.empty and (off.empty or not args.activate):
        print("\nNothing else to write." if links else "\nNothing written.")
        if args.history:
            _history([], wanted)
        return 0

    if backup is None:
        backup = _backup()
        print(f"\nBackup: {backup}")
    with db.get_conn() as conn:
        for r in add.to_dict("records"):
            conn.execute("""
                INSERT OR IGNORE INTO instruments
                    (fund_id, name, asset_type, currency, price_unit,
                     category, source, source_id, active)
                VALUES (?, ?, ?, ?, ?, ?, 'yahoo', ?, 1)
            """, (r["fund_id"], r["name"], r["asset_type"], r["currency"],
                  r["price_unit"], r["category"], r["source_id"]))
        if args.activate and not off.empty:
            conn.executemany("UPDATE instruments SET active = 1 "
                             "WHERE fund_id = ?",
                             [(f,) for f in off["fund_id"]])
    print(f"Added {len(add)} instrument(s)"
          + (f", activated {len(off)}" if args.activate and not off.empty
             else "") + ".")
    if args.history:
        fresh = list(add["source_id"]) if not add.empty else []
        if args.activate and not off.empty:
            fresh += [f[3:] for f in off["fund_id"]]
        _history(fresh, wanted)
        return 0
    print("\nNext:\n  .venv/bin/python -m importers.yahoo_prices\n"
          "  .venv/bin/python -m importers.live_prices")
    return 0


def _history(fresh: list[str], wanted: pd.DataFrame) -> None:
    """Yahoo history for ``fresh`` symbols plus any needed Yahoo instrument
    that has no prices yet, so the new rows have returns at once instead of
    after the nightly import."""
    symbols = list(fresh)
    if not wanted.empty:
        ids = list(wanted["fund_id"])
        marks = ",".join("?" * len(ids))
        bare = db.query(f"""
            SELECT i.source_id FROM instruments i
            WHERE i.fund_id IN ({marks}) AND i.source = 'yahoo'
              AND COALESCE(i.active, 1) = 1 AND i.source_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM prices p
                              WHERE p.fund_id = i.fund_id)
        """, tuple(ids))
        symbols += list(bare["source_id"])
    symbols = list(dict.fromkeys(s for s in symbols if s))
    if not symbols:
        print("\nHistory: nothing to fetch.")
        return
    print(f"\nFetching Yahoo history for {len(symbols)} symbol(s)...",
          flush=True)
    from importers import yahoo_prices
    result = yahoo_prices.run(tickers=symbols)
    print(f"History: {result.get('message', 'done')}", flush=True)


if __name__ == "__main__":
    # The last line is read by Data -> ETF targets to know the run is over.
    code = 1
    try:
        code = main()
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, flush=True)
        code = exc.code if isinstance(exc.code, int) else 1
    except Exception:                                          # noqa: BLE001
        import traceback
        traceback.print_exc()
    print(f"{FINISHED} {'ok' if not code else 'failed'}", flush=True)
    sys.exit(code)
