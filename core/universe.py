"""
The instrument universe: what you track, what it's called, and how composites
are built.

This is DATA, not settings. core/config.py holds settings (paths, ports,
thresholds); this holds the list of things you own and follow. The old
config.py mixed the two, which is why it grew to 390 lines.

During the parallel run this module READS the legacy config.py rather than
copying it, for the same reason both apps share one database: two copies of
the same list will drift. At cutover the definitions move in here and the
loader is deleted.
"""
import importlib.util
from functools import lru_cache

from core import config


@lru_cache(maxsize=1)
def _legacy():
    path = config.LEGACY_DIR / "config.py"
    if not path.exists():
        raise FileNotFoundError(
            f"Legacy config not found at {path}. Set LEGACY_DIR in core/config.py.")
    spec = importlib.util.spec_from_file_location("_legacy_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reload() -> None:
    """Pick up edits to the legacy config without restarting the service."""
    _legacy.cache_clear()


# --- Accessors -----------------------------------------------------------

def ft_funds() -> list[dict]:
    """Funds price-scraped from FT Markets."""
    return getattr(_legacy(), "FUNDS", [])


def yahoo_tickers() -> list[tuple]:
    """(ticker, display_name, asset_type[, provider])"""
    return getattr(_legacy(), "YAHOO_TICKERS", [])


def composites() -> list[dict]:
    """Virtual funds blended from real ones. Weights sum to 1.0."""
    return getattr(_legacy(), "COMPOSITE_FUNDS", [])


def fund_id_map() -> dict:
    """Ticker prefix -> canonical fund_id, e.g. SPGP -> YF:SPGP.L"""
    return getattr(_legacy(), "FUND_ID_MAP", {})


def etf_provider_map() -> dict:
    """Ticker prefix -> parser name, e.g. SPGP -> ishares"""
    return getattr(_legacy(), "ETF_PROVIDER_MAP", {})


def holding_accounts() -> dict:
    return getattr(_legacy(), "HOLDING_ACCOUNTS", {})


# --- Derived -------------------------------------------------------------

def etf_names() -> dict[str, str]:
    """fund_id -> display label, for ETFs with a holdings provider."""
    out = {}
    for t in yahoo_tickers():
        if t[2] != "ETF":
            continue
        ticker = t[0]
        short = ticker.replace(".L", "").replace(".IS", "")
        out[f"YF:{ticker}"] = f"{short} - {t[1]}"
    return out


def etf_providers() -> dict[str, str]:
    """fund_id -> provider name, or '-' if price-tracked only."""
    return {f"YF:{t[0]}": (t[3] if len(t) == 4 else "-")
            for t in yahoo_tickers() if t[2] == "ETF"}


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
