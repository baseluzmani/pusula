# Architecture rules

- `core/`      shared: config, db, prices, theme. NO Dash imports.
- `core/repo/` ALL SQL lives here. Returns DataFrames.
- `pages/`     Dash layouts + callbacks. NO SQL.
- `importers/` data loaders, registered in importers/registry.py.
- `scripts/`   cron entry points.

- Use `@callback` from dash. NEVER `@app.callback`.
- DB path comes from core/config.py only. Never hardcode.
- Secrets in .env, read via os.environ. Never committed.
- Table prefixes: pf_ (portfolio), mkt_ (market), spend_, etf_.
