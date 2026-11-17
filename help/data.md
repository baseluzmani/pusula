# Data

The engine room: the jobs that load data, and the lists and settings every other page reads. Changes here affect figures everywhere.

## Importers

Every data job, when it runs, when it last ran and how it went, with a button to run it now.

### Importers list {#list}

One line per job: which tables it fills, when it runs, and how its last run went. Run now starts it straight away.

## Run history

The latest job runs and how they went.

### Recent runs {#runs}

When each job ran, whether it worked, how many rows it wrote and its message.

## Database

Every table in the database with its row count.

### Connection {#connection}

Which database file the app is using.

### Tables {#tables}

Every table with its number of rows.

## Accounts

Your list of accounts, with owner and wrapper. The Inputs form offers these names, so tidy names here stop typos turning into new accounts.

### Accounts table {#table}

Edit a cell, add a row, then Save. An inactive account is kept for history but no longer offered.

## Instruments

Every tracked instrument: name, type, vehicle, currency and price unit.

These fields decide how a price turns into pounds. A wrong currency or price unit makes the value wrong everywhere.

Terms: price-unit, vehicle, asset-type

### Instruments table {#table}

Edit cells, then Save. "Needs attention" shows rows missing a currency or price unit. Add instrument creates a new row from its Yahoo ticker or FT code.

## Composites

The pension blends: which funds make up each one and in what weights.

Terms: composite

### Composites {#composites}

The upper grid lists the blends. Click one to edit its funds and weights below. The weights should add up to 1.

## Config

Settings you might want to adjust, such as Monitor thresholds, Momentum horizons and the Manual Queue start date.

### Settings {#settings}

Each setting with its value and what it does. Edit a value, then Save.

### Fixed in code {#fixed}

Settings that can only be changed in the code, such as the database location, shown for reference.

## Backfill

Fetch missing price history from Yahoo for a date range.

### Where the gaps are {#gaps}

Instruments whose latest price is not recent. A day or two is usually a market holiday; weeks means something is wrong.

## ETF upload

Drop in holdings files from ETF providers. Each file reports whether it was imported.

### Upload holdings files {#upload}

Drop files here. The start of the file name says which fund it belongs to.

### Import from the folder {#folder}

Import files already copied into the input folder.

## ETF targets

Choose which funds appear in the Day estimate and in the Markets ETF list, and add the instruments their holdings need.

Terms: day-estimate

### ETF targets table {#table}

Add a fund, edit its label and settings, or click one to see its holdings. "Add missing instruments" sets up and loads prices for holdings that are not tracked yet. Delete removes the fund from the list.

### Holdings {#holdings}

The selected fund's holdings and whether each one can be priced.

## Manual prices

Values nothing fetches automatically: the house, from Zoopla, and the mortgage balance, from a statement. Each value carries forward until the next one.

### Manually priced holdings {#holdings}

The holdings priced by hand and their latest value.

### Record a value {#record}

Enter a value with its date.

### History {#history}

Every value entered for the selected holding.
