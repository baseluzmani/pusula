# Pusula — future developments

Deferred items, captured so they aren't lost. Not yet scheduled.

## Markets section
- **Macro tab** — FRED macroeconomic series (own animal, not instrument
  returns). Port after the three core Markets tabs settle.
- **Instruments table clean-up** — reconcile `asset_type` vs `category`
  taxonomies; both exist and are used inconsistently. Decide one grouping
  key, backfill, and drop the redundant column path.
- **Config tab clean-up** — review the config/settings surface once the
  Markets pages are stable; fold any per-page config into the shared layer.

## Portfolio section (not yet migrated — port 8050)
- Biggest / highest-risk migration; do near-last.
- Reuses `core/repo/market.py` (already built) + a new `core/repo/portfolio.py`.
- Wire cron scripts (runner.py, yahoofinanceimporter.py, live_prices.py,
  snapshot.py) into the importer registry.

## Analytics pages not being migrated (decide fate later)
- correlation, return_clusters, overlap_detector, risk_decomposition,
  portfolio_simulator, data_quality.
- Note: `calculations/correlation.py` has `build_return_series` defined
  twice (second overrides first) — fix if/when correlation is ported.
- Correlation, if ported, needs weekly (Fri-Fri) returns to fix the
  cross-timezone spurious-low-correlation issue. Calendar-period returns
  used elsewhere are fine and unaffected.

## Housekeeping
- Retire port 8051 (portfolio-analytics) after Markets migration.
- Two virtualenvs exist under portfolio-analytics (`venv` + `.venv`,
  ~784MB). Remove both once 8051 is retired.
- Spending dashboard (port 8052) — fresh build, no existing DB tables.
