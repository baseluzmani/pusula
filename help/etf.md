# ETF

What funds and ETFs hold, how that changes over time, how two funds overlap, and an estimate of today's move for funds that price late.

## Holdings

What a fund owns on a chosen date. A company held under several tickers counts once.

Terms: weight

### Portfolio composition {#composition}

Every holding on that date with its weight in the fund.

### Weight trend - top holdings {#weight-heatmap}

How the weights of the biggest holdings have moved across the dates on file.

### Weight trend - line {#weight-line}

The same weights as lines over time.

## Changes

What the manager bought, sold, added to and trimmed between two dates. Very small changes are left out.

Terms: weight

### New positions {#new}

Companies the fund holds now but did not on the earlier date.

### Removed positions {#removed}

Companies the fund held on the earlier date and no longer holds.

### Increased {#increased}

Holdings whose weight went up.

### Decreased {#decreased}

Holdings whose weight went down.

## Compare

Where two funds own the same companies, and how much of your money really overlaps.

Terms: weight, overlap

### Top 10 {#top10}

Each fund's ten biggest holdings and how much of the fund they make up.

### Only in one fund {#only}

Companies held by one fund but not the other.

### Common holdings {#common}

Companies both funds hold, with each fund's weight. The overlap counts the smaller of the two weights.

### Overlap heatmap {#overlap-heatmap}

The biggest common holdings, coloured by weight in each fund.

## Fund compare

Any two sets of holdings side by side: an ETF, a pension fund, or a pension blend.

Terms: weight

### Two sides {#sides}

Each side lists what that fund holds on the chosen date, or the nearest earlier date on file.

## Day estimate

An estimate of today's move for funds that price late, such as the pension funds, worked out from what they hold and the latest prices. A backtest shows how far to trust it.

Funds are added and removed in Data → ETF targets.

Terms: day-estimate, estimate-error, booked-day, priced-share

### Indicative moves {#moves}

One row per fund: how much of it could be priced, the estimated move before and after currency effects, the fund's own move once it is known, and how close past estimates were.

### Fund holdings {#holdings}

The holdings behind the selected fund's estimate: each one's weight, its move in its own currency, the currency effect, and what it added to the estimate. Lines without a price are marked.

### Estimate against the fund's own price {#chart}

Past estimates against the fund's actual daily moves, lined up on the day the fund books them.

## Ticker map

The table that links each holding's tickers and codes to one company record, so the same company is recognised across funds. Most other ETF screens depend on it.

### Identifier records {#records}

Every company record with its tickers, codes and Yahoo symbol. Filter to the ones still to review or missing a Yahoo symbol.

### Unmapped fund holdings {#unmapped}

Holdings in fund files that no record matches yet.

### Edit mapping {#edit}

Change the record you picked, for example to add its Yahoo symbol.

## Sources

Where each fund's holdings file comes from.

### Holdings file sources {#sources}

Each fund with its provider, when its file was last imported, how many dates are on file, and where to download the next one.
