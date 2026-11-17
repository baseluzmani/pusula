# System

## How data gets in {#flow}

Prices come in automatically through the day: FT for pension and other funds, Yahoo for shares, ETFs, FX and futures. Pension blends (composites) are rebuilt after each price run. Each evening at 22:30 a snapshot freezes the day's values, and at 23:00 the database is backed up.

Trades, cash, the house value and the mortgage balance are entered by hand. Spending comes from bank statements and CSV files you upload.

## Where things live {#where}

- The app runs on the MiniPC at port 8060, as the `pusula` service.
- All data is in one database file: `~/data/funds.db`. Backups go to the `backups` folder next to it.
- The code is in `~/pusula`; job logs are in `~/pusula/logs`.
- These help pages are plain text files in `~/pusula/help`. Edit them and the change shows next time you open help.

## Schedule {#schedule}

Prices refresh every 15 minutes on weekdays from 08:00 to 21:00, with fuller runs morning, lunchtime and evening. US economic data (FRED) loads at 07:30 and UK gilt data (Bank of England) at 07:45. The app restarts itself at 07:15. The table below lists each job as the app knows it; Data → Importers runs any of them now, and Data → Run history shows how they went.

## Restarting and updating {#updating}

After changing code: run the tests, then `sudo systemctl restart pusula`. Never run the app from a terminal window; the service is the one that should be running.
