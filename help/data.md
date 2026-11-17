# Data

The engine room: the jobs that load data, and the lists and settings everything else reads.

## Importers

Every data job, when it runs and when it last ran, with a button to run it now.

## Run history

The latest job runs and how they went.

## Database

Every table in the database with its row count.

## Accounts

Your list of accounts, with owner and wrapper. The Inputs form offers these names, so tidy names here stop typos turning into new accounts.

## Instruments

Every tracked instrument: name, type, vehicle, currency and price unit. A wrong currency or unit here makes its value wrong everywhere.

## Composites

The pension blends: which funds make up each one and in what weights. The weights should add up to 1.

## Config

Settings you might want to adjust, such as thresholds and start dates.

## Backfill

Fetch missing price history from Yahoo for a date range.

## ETF upload

Drop in holdings files from ETF providers.

## ETF targets

Choose which funds appear in the Day estimate and in the Markets ETF list, and add the instruments their holdings need.

## Manual prices

Values nothing fetches automatically: the house, from Zoopla, and the mortgage balance, from a statement. Each value carries forward until the next one.
