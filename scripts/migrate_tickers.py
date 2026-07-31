#!/usr/bin/env python3
"""
Move the ticker plumbing out of config.py and into the instruments table.

Four columns carry what config currently holds:

    source       which importer owns this instrument - yahoo, ft, composite,
                 manual. This is what lets an importer ask "which instruments
                 are mine?" instead of reading a hardcoded list.
    source_id    the identifier that importer needs: a Yahoo ticker (HSBA.L)
                 or an FT id (GB00B6Y7NF43:GBP)
    provider     which parser handles this ETF's holdings CSV - ishares,
                 vaneck, wisdomtree and so on
    holdings_id  the FT holdings identifier, which differs from the price id
                 for some funds

Nothing is read from these columns yet. This step only puts the data in place
so it can be verified against config before anything starts depending on it.

Usage
-----
    python3 migrate_tickers.py --db path/to/funds.db            # preview
    python3 migrate_tickers.py --db path/to/funds.db --apply    # write
"""

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

NEW_COLUMNS = {
    "source": "TEXT",
    "source_id": "TEXT",
    "provider": "TEXT",
    "holdings_id": "TEXT",
}


def load_config(legacy_dir: Path):
    """Load FTScrapper's config.py by path - it is not on the import path."""
    path = legacy_dir / "config.py"
    if not path.exists():
        sys.exit(f"config.py not found at {path}")
    spec = importlib.util.spec_from_file_location("_ft_config", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(legacy_dir))
    spec.loader.exec_module(mod)
    return mod


def planned_rows(cfg) -> dict:
    """
    {fund_id: {source, source_id, provider, holdings_id}} derived from config.

    Yahoo tickers become YF: prefixed ids; FT funds use their identifier
    directly, which is how setup_instruments.py already builds them.
    """
    rows = {}

    for item in getattr(cfg, "YAHOO_TICKERS", []):
        ticker = item[0]
        rows[f"YF:{ticker}"] = {
            "source": "yahoo",
            "source_id": ticker,
            # A fourth element means the ETF also has a holdings CSV importer.
            "provider": item[3] if len(item) > 3 else None,
            "holdings_id": None,
        }

    for fund in getattr(cfg, "FUNDS", []):
        fund_id = fund.get("id")
        if not fund_id:
            continue
        rows[fund_id] = {
            "source": "ft",
            "source_id": fund_id,
            "provider": None,
            "holdings_id": fund.get("holdings_id"),
        }

    for comp in getattr(cfg, "COMPOSITE_FUNDS", []):
        fund_id = comp.get("fund_id")
        if fund_id:
            rows[fund_id] = {"source": "composite", "source_id": None,
                             "provider": None, "holdings_id": None}

    return rows


def existing_columns(conn) -> set:
    return {r[1] for r in conn.execute("PRAGMA table_info(instruments)")}


def add_columns(conn) -> list:
    have = existing_columns(conn)
    added = []
    for name, ctype in NEW_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE instruments ADD COLUMN {name} {ctype}")
            added.append(name)
    conn.commit()
    return added


def preview(conn, cfg):
    plan = planned_rows(cfg)
    in_db = {r[0] for r in conn.execute("SELECT fund_id FROM instruments")}

    matched = sorted(set(plan) & in_db)
    config_only = sorted(set(plan) - in_db)
    db_only = sorted(in_db - set(plan))

    print("Columns")
    print("-" * 72)
    have = existing_columns(conn)
    for name in NEW_COLUMNS:
        print(f"  {name:12s} {'already present' if name in have else 'will be added'}")

    print(f"\nConfig describes {len(plan)} instruments")
    print("-" * 72)
    by_source = {}
    for v in plan.values():
        by_source[v["source"]] = by_source.get(v["source"], 0) + 1
    for src, n in sorted(by_source.items()):
        print(f"  {src:10s} {n:4d}")
    providers = sorted({v["provider"] for v in plan.values() if v["provider"]})
    print(f"  providers: {', '.join(providers)}")

    print(f"\nMatched against instruments: {len(matched)}")
    print("-" * 72)

    if config_only:
        print(f"\nIn config but NOT in instruments: {len(config_only)}")
        print("  These are tracked in config yet have no instrument row, so")
        print("  nothing would carry their source. Worth adding.")
        for fid in config_only[:20]:
            print(f"    {fid}")
        if len(config_only) > 20:
            print(f"    ... and {len(config_only) - 20} more")

    if db_only:
        print(f"\nIn instruments but NOT in config: {len(db_only)}")
        print("  These get source 'manual' - CASH:, ASSET: and anything added")
        print("  by hand. Check the list for surprises.")
        for fid in db_only[:20]:
            print(f"    {fid}")
        if len(db_only) > 20:
            print(f"    ... and {len(db_only) - 20} more")

    print("\nNothing written. Re-run with --apply to migrate.")


def apply(conn, cfg):
    added = add_columns(conn)
    print(f"Columns added: {', '.join(added) if added else 'none, all present'}")

    plan = planned_rows(cfg)
    in_db = {r[0] for r in conn.execute("SELECT fund_id FROM instruments")}

    updated = 0
    for fund_id, vals in plan.items():
        if fund_id not in in_db:
            continue
        conn.execute("""
            UPDATE instruments
            SET source = ?, source_id = ?, provider = ?, holdings_id = ?
            WHERE fund_id = ?
        """, (vals["source"], vals["source_id"], vals["provider"],
              vals["holdings_id"], fund_id))
        updated += 1

    # Anything config does not describe is maintained by hand - cash, fixed
    # assets, instruments added directly. Marking them keeps every row
    # accounted for, so a NULL source later means something is genuinely wrong.
    manual = conn.execute("""
        UPDATE instruments SET source = 'manual'
        WHERE source IS NULL OR source = ''
    """).rowcount
    conn.commit()

    print(f"Updated from config: {updated}")
    print(f"Marked manual:       {manual}")

    orphans = conn.execute(
        "SELECT COUNT(*) FROM instruments WHERE source IS NULL").fetchone()[0]
    print(f"Still without a source: {orphans}"
          + ("  <- investigate" if orphans else "  (good)"))


def verify(conn):
    print("\nBy source")
    print("-" * 72)
    for src, n in conn.execute("""
        SELECT COALESCE(source, '(none)'), COUNT(*) FROM instruments
        GROUP BY 1 ORDER BY 2 DESC
    """):
        print(f"  {src:10s} {n:4d}")

    print("\nYahoo instruments missing a source_id")
    rows = conn.execute("""
        SELECT fund_id FROM instruments
        WHERE source = 'yahoo' AND (source_id IS NULL OR source_id = '')
    """).fetchall()
    print(f"  {len(rows)}" + ("" if rows else "  (good)"))
    for r in rows[:10]:
        print(f"    {r[0]}")

    print("\nETFs with a holdings provider")
    for src, n in conn.execute("""
        SELECT provider, COUNT(*) FROM instruments
        WHERE provider IS NOT NULL GROUP BY provider ORDER BY 2 DESC
    """):
        print(f"  {src:12s} {n:3d}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--legacy-dir", default=str(Path.home() / "FTScrapper"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    cfg = load_config(Path(args.legacy_dir))
    conn = sqlite3.connect(args.db)
    try:
        if args.apply:
            apply(conn, cfg)
            verify(conn)
        else:
            preview(conn, cfg)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
