"""
The instrument universe: what you track, what it's called, and how composites
are built.

This module used to load FTScrapper's config.py by file path. That stopped
working correctly when the ticker lists moved into `instruments`: FUNDS,
YAHOO_TICKERS, FUND_ID_MAP and ETF_PROVIDER_MAP no longer existed in that
file, and every accessor here fell through to its getattr default and
returned an empty list or dict. Silently - which is why the ETF tab lost its
labels and providers without anything raising.

It is now a thin façade over the repos that hold the real data:

    core.repo.tickers     instruments -> ticker lists, id maps
    core.repo.composites  composites / composite_components

The façade is kept rather than having callers import the repos directly,
because the derived helpers below - etf_names, label, composite - are used
across several pages and belong somewhere shared.
"""

from core.repo import composites as _composites
from core.repo import tickers as _tickers


def reload() -> None:
    """No-op. Kept so existing callers do not break: the definitions come
    from the database now, so every read is already current and there is no
    cached module to clear."""
    return None


# --- Accessors -----------------------------------------------------------

def ft_funds() -> list[dict]:
    """Funds price-scraped from FT Markets."""
    return _tickers.ft_funds()


def yahoo_tickers() -> list[tuple]:
    """(ticker, display_name, asset_type[, provider])"""
    return _tickers.yahoo_tickers()


def composites() -> list[dict]:
    """Virtual funds blended from real ones. Weights sum to 1.0."""
    return _composites.definitions()


def fund_id_map() -> dict:
    """Ticker prefix -> canonical fund_id, e.g. SPGP -> YF:SPGP.L"""
    return _tickers.fund_id_map()


def etf_provider_map() -> dict:
    """Ticker prefix -> parser name, e.g. SPGP -> ishares"""
    return _tickers.etf_provider_map()


def holding_accounts() -> dict:
    return _composites.holding_accounts()


# --- Derived -------------------------------------------------------------

def _is_etf(row) -> bool:
    """An ETF for labelling purposes.

    The old test was asset_type == 'ETF' alone. A row now also counts if it
    carries a holdings provider: provider is what actually drives the ETF
    tab, and an instrument with a parser configured but a looser asset_type
    would otherwise vanish from the labels while still being imported.
    """
    asset_type = (row[2] or "").strip().upper() if len(row) > 2 else ""
    has_provider = len(row) > 3 and bool(row[3])
    return asset_type == "ETF" or has_provider


def etf_names() -> dict[str, str]:
    """fund_id -> display label, for ETFs with a holdings provider."""
    out = {}
    for t in yahoo_tickers():
        if not _is_etf(t):
            continue
        ticker = t[0]
        short = ticker.replace(".L", "").replace(".IS", "")
        out[f"YF:{ticker}"] = f"{short} - {t[1]}"
    return out


def etf_providers() -> dict[str, str]:
    """fund_id -> provider name, or '-' if price-tracked only."""
    return {f"YF:{t[0]}": (t[3] if len(t) == 4 and t[3] else "-")
            for t in yahoo_tickers() if _is_etf(t)}


def composite(fund_id: str) -> dict | None:
    """Find a composite by fund_id, with or without the COMPOSITE: prefix."""
    bare = fund_id.replace("COMPOSITE:", "")
    for c in composites():
        if c["fund_id"].replace("COMPOSITE:", "") == bare:
            return c
    return None


def label(fund_id: str) -> str:
    """Human-readable name for any fund_id we know about."""
    names = etf_names()
    if fund_id in names:
        return names[fund_id]
    comp = composite(fund_id) if fund_id.startswith("COMPOSITE:") else None
    if comp:
        return comp["display_name"]
    for f in ft_funds():
        if f["id"] == fund_id:
            return f["name"]
    return fund_id