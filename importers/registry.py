"""
One entry per importer. The Data page builds itself from this list, and cron
jobs call the same functions, so scheduled and manual runs behave identically.

To add an importer:
  1. write importers/<name>.py with a run() function returning (rows, message)
  2. add a dict below
Nothing else needs changing.
"""
from dataclasses import dataclass
from typing import Callable

from importers import etf_holdings as _etf_holdings
from importers import stock_map as _stock_map


@dataclass
class Importer:
    id: str
    label: str
    mode: str                  # "auto" (cron) or "manual" (button / file drop)
    target_tables: list[str]
    description: str
    schedule: str = ""         # human-readable, e.g. "Daily 18:05"
    source: str = ""           # where the data comes from
    run: Callable | None = None   # set when the module is written


REGISTRY: list[Importer] = [
    Importer(
        id="prices",
        label="Market prices",
        mode="auto",
        schedule="Daily 18:05",
        source="yfinance (batched, exponential backoff)",
        target_tables=["mkt_prices"],
        description="Close prices and FX for every held instrument.",
    ),
    Importer(
        id="etf_holdings",
        label="ETF holdings",
        mode="manual",
        source="Provider files dropped in inbox/",
        target_tables=["etf_holdings"],
        description=("Parses iShares, Xtrackers/DWS, GlobalX and others; "
                     "resolves tickers or ISINs to the identifier map."),
        run=_etf_holdings.run,
    ),
    Importer(
        id="stock_map",
        label="Identifier map",
        mode="manual",
        source="OpenFIGI API",
        target_tables=["stock_identifier_map"],
        description="Enriches unresolved tickers and ISINs with FIGI keys.",
        run=_stock_map.run,
    ),
    Importer(
        id="pension_funds",
        label="Pension fund holdings",
        mode="manual",
        source="Provider factsheets and websites",
        target_tables=["fund_holdings"],
        description="Top holdings for pension and OEIC positions.",
    ),
    Importer(
        id="spending",
        label="Bank statements",
        mode="manual",
        source="HSBC CSV export in inbox/",
        target_tables=["spend_transactions"],
        description=("Imports card and current account transactions, applies "
                     "category rules, queues the rest for manual review."),
    ),
]


def by_id(importer_id: str) -> Importer | None:
    return next((i for i in REGISTRY if i.id == importer_id), None)
