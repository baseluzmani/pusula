"""
Instrument lists for the import scripts.

These were literal lists in FTScrapper's config.py. The data now lives in
instruments - source, source_id, provider, holdings_id - and this module is
the single place that turns those columns back into the shapes the importers
expect.

Shapes are deliberately identical to the old config globals, so a script
switching over changes only where the list comes from, never what it does
with it:

    yahoo_tickers()     [(ticker, name, asset_type)] or with a 4th element,
                        the provider, for ETFs that also import holdings
    ft_funds()          [{"name":..., "id":..., "holdings_id":...}]
    fund_id_map()       {"SEMI": "YF:SEMI.L", ...}   file prefix -> fund_id
    etf_provider_map()  {"SEMI": "ishares", ...}     file prefix -> parser

Inactive instruments are excluded everywhere. Setting active = 0 in the
Instruments tab is therefore how you stop tracking something without losing
its history - which is what closing a position should do.
"""

from __future__ import annotations

from core import db

# Suffixes stripped to get the CSV filename prefix a provider exports under.
_SUFFIXES = (".L", ".IS")


def _text(value):
    """None for missing values. Pandas yields NaN for SQL NULL, and NaN is
    truthy - so `if provider:` passes for a null provider and every ticker
    would gain a bogus fourth element."""
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.lower() not in ("nan", "none") else None


def yahoo_tickers() -> list:
    """Everything the Yahoo importers should fetch."""
    df = db.query("""
        SELECT source_id, name, asset_type, provider
        FROM instruments
        WHERE source = 'yahoo' AND COALESCE(active, 1) = 1
          AND source_id IS NOT NULL AND source_id != ''
        ORDER BY source_id
    """)
    if df.empty:
        return []
    out = []
    for r in df.itertuples():
        base = (r.source_id, _text(r.name) or r.source_id,
                _text(r.asset_type) or "")
        # The fourth element marks an ETF whose holdings CSV is also parsed.
        provider = _text(r.provider)
        out.append(base + (provider,) if provider else base)
    return out


def ft_funds() -> list:
    """Funds scraped from FT Markets."""
    df = db.query("""
        SELECT name, source_id, holdings_id
        FROM instruments
        WHERE source = 'ft' AND COALESCE(active, 1) = 1
          AND source_id IS NOT NULL AND source_id != ''
        ORDER BY name
    """)
    if df.empty:
        return []
    return [{"name": _text(r.name) or r.source_id, "id": r.source_id,
             "holdings_id": _text(r.holdings_id) or r.source_id}
            for r in df.itertuples()]


def _prefix(source_id: str) -> str:
    out = source_id
    for suffix in _SUFFIXES:
        out = out.replace(suffix, "")
    return out


def _etf_rows():
    df = db.query("""
        SELECT source_id, fund_id, provider
        FROM instruments
        WHERE source = 'yahoo' AND provider IS NOT NULL AND provider != ''
          AND COALESCE(active, 1) = 1
    """)
    return df.itertuples() if not df.empty else []


def fund_id_map() -> dict:
    """CSV filename prefix to fund_id, for the holdings importer.

    Because this is built from instruments, adding a new ETF row is all it
    takes for its holdings file to resolve - no alias needed.
    """
    return {_prefix(r.source_id): r.fund_id for r in _etf_rows()}


def etf_provider_map() -> dict:
    """CSV filename prefix to provider, for parser selection."""
    return {_prefix(r.source_id): _text(r.provider) for r in _etf_rows()}
