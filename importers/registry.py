"""
One entry per importer. The Data page builds itself from this list, and cron
calls the same functions, so a scheduled run and a Run Now button do exactly
the same thing.

To add an importer:
  1. write importers/<name>.py with a run() function
  2. add an Importer below, wrapping run() with an adapter if it returns a
     dict rather than (rows, message)
Nothing else needs changing.
"""

from dataclasses import dataclass
from typing import Callable

from importers import (backfill, backup, composites, ft_funds, live_prices,
                       snapshot, yahoo_prices)

try:
    from importers import etf_holdings as _etf_holdings
except Exception:                                              # noqa: BLE001
    _etf_holdings = None
try:
    from importers import stock_map as _stock_map
except Exception:                                              # noqa: BLE001
    _stock_map = None


@dataclass
class Importer:
    id: str
    label: str
    mode: str                  # "auto" (cron) or "manual" (button / file drop)
    target_tables: list[str]
    description: str
    schedule: str = ""         # human-readable, e.g. "Daily 22:30"
    source: str = ""           # where the data comes from
    run: Callable | None = None


# --- Adapters -------------------------------------------------------------
# The importers return a summary dict, which is more useful to a caller than a
# bare tuple. jobs.run_importer wants (rows, message), so each is wrapped once
# here rather than every importer being bent to fit the logger.

def _ft_funds():
    r = ft_funds.run()
    return r.get("funds", 0), r.get("message", "")


def _ft_funds_prices_only():
    r = ft_funds.run(do_holdings=False)
    return r.get("funds", 0), r.get("message", "")


def _yahoo_prices():
    r = yahoo_prices.run()
    return r.get("saved", 0), r.get("message", "")


def _live_prices():
    r = live_prices.run()
    return r.get("updated", 0), r.get("message", "")


def _composites():
    r = composites.run()
    return r.get("saved", 0), r.get("message", "")


def _snapshot():
    r = snapshot.run()
    return r.get("holdings", 0), r.get("message", "")


def _backup():
    r = backup.run()
    return r.get("kept", 0), r.get("message", "")


REGISTRY: list[Importer] = [
    Importer(
        id="ft_funds",
        label="FT funds",
        mode="auto",
        schedule="07:00 full, 12:50 and 22:15 prices only",
        source="markets.ft.com (session cookie in .env)",
        target_tables=["prices", "pension_holdings"],
        description=("Scrapes price history and top holdings for every "
                     "instrument with source 'ft'. Follows feeder funds "
                     "through to the fund that holds the securities."),
        run=_ft_funds,
    ),
    Importer(
        id="ft_funds_prices",
        label="FT funds - prices only",
        mode="manual",
        source="markets.ft.com",
        target_tables=["prices"],
        description=("The same scrape without the holdings pass. Quicker, and "
                     "what the midday and evening cron runs use."),
        run=_ft_funds_prices_only,
    ),
    Importer(
        id="yahoo_prices",
        label="Yahoo prices",
        mode="auto",
        schedule="07:02, 12:52, 22:17",
        source="yfinance (batched by start date, exponential backoff)",
        target_tables=["prices"],
        description=("Daily history for every instrument with source 'yahoo'. "
                     "Always reimports the last few days, because Yahoo "
                     "revises recent figures after the fact."),
        run=_yahoo_prices,
    ),
    Importer(
        id="live_prices",
        label="Yahoo live prices",
        mode="auto",
        schedule="Every 15 min, 08:00-21:00 weekdays",
        source="yfinance (intraday bars, batched)",
        target_tables=["prices"],
        description=("Latest available price for every tracked ticker, stored "
                     "against the session it belongs to."),
        run=_live_prices,
    ),
    Importer(
        id="composites",
        label="Composite prices",
        mode="auto",
        schedule="Chained after each price import",
        source="Component prices already in the database",
        target_tables=["prices"],
        description=("Rebuilds the blended series for pension composites, "
                     "rebased to 100 at the first date all components share."),
        run=_composites,
    ),
    Importer(
        id="snapshot",
        label="Portfolio snapshot",
        mode="auto",
        schedule="Daily 22:30",
        source="Current holdings, cash and prices",
        target_tables=["portfolio_snapshots", "snapshot_holdings",
                       "snapshot_cash", "networth_history"],
        description=("Freezes today's valuation so the comparison views have "
                     "something to measure against."),
        run=_snapshot,
    ),
    Importer(
        id="backup",
        label="Database backup",
        mode="auto",
        schedule="Daily 23:00",
        source="funds.db",
        target_tables=[],
        description=("SQLite backup API rather than a file copy, which "
                     "matters with WAL enabled. Keeps the last 7."),
        run=_backup,
    ),
    Importer(
        id="backfill",
        label="Backfill prices",
        mode="manual",
        source="yfinance (batched over a date range)",
        target_tables=["prices"],
        description=("Fills gaps for a chosen date range. Has its own tab, "
                     "since it needs a range and a scope rather than just a "
                     "button."),
    ),
    Importer(
        id="etf_holdings",
        label="ETF holdings",
        mode="manual",
        source="Provider files dropped in the import folder",
        target_tables=["etf_holdings"],
        description=("Parses iShares, VanEck, WisdomTree, UBS, Fidelity, "
                     "HANetf, Xtrackers and GlobalX exports. Still the legacy "
                     "script - not yet moved into Pusula."),
        run=(lambda: _etf_holdings.run()) if _etf_holdings else None,
    ),
    Importer(
        id="stock_map",
        label="Identifier map",
        mode="manual",
        source="OpenFIGI API",
        target_tables=["stock_identifier_map"],
        description=("Enriches unresolved tickers and ISINs with FIGI keys. "
                     "Still the legacy script."),
        run=(lambda: _stock_map.run()) if _stock_map else None,
    ),
]


def by_id(importer_id: str) -> Importer | None:
    return next((i for i in REGISTRY if i.id == importer_id), None)
