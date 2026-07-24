# Architecture rules

Paste this at the start of any coding session, with any assistant.

## Folders
- `core/`       shared: config, db, prices, theme. NO Dash imports.
- `core/repo/`  ALL SQL lives here. Functions return DataFrames.
- `ui/`         shared Dash components (cards, tables, headers).
- `pages/`      one file per section. Layout + callbacks. NO SQL.
- `importers/`  data loaders, all listed in importers/registry.py.
- `scripts/`    cron entry points. Thin wrappers around importers.
- `assets/`     CSS. Dash loads this folder automatically.

## Rules
- Use `@callback` from dash. NEVER `@app.callback`.
- DB path comes from `core/config.py` only. Never hardcode a path.
- Pages never import sqlite3. They call `core/repo/` functions.
- Secrets in `.env`, read via `core/config.py`. Never committed.
- Colours and spacing come from `core/theme.py`. No inline hex codes.
- Pattern-matched callback IDs: use `ctx.triggered_id`, never
  `ctx.triggered[0]['prop_id'].split('.')` — IDs contain dots.

## Table prefixes
`pf_` portfolio · `mkt_` market data · `spend_` spending · `etf_` holdings
