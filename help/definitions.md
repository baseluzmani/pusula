# Definitions

## Value {#value}

What a holding is worth now, in pounds: units times the latest price, converted from its own currency at the latest exchange rate. Prices quoted in pence are divided by 100 first.

## P&L {#pnl}

Profit or loss in pounds. What you got back from sales and dividends, plus what you still hold is worth today, minus everything you paid, commission included.

Realised is the part from sales. Unrealised is the part on what you still hold.

## Net cost per unit {#net-cost}

What each unit you still hold has really cost you: everything paid in, less money back from sales and dividends, divided by the units held. Shown in the holding's own currency. A price above it means you are ahead overall.

## Capital employed {#capital-employed}

The money you had in a holding, at what you paid for it, day by day. Days is how many days you held any; Avg £ is the typical amount over those days; Peak £ is the most at any one time.

## Return on capital (ROCE) {#roce}

P&L divided by the average money you had invested: how hard your money worked while it was in the holding.

## Annualised return {#roce-annualised}

The same return scaled to one year, so a holding kept for months and one kept for years can be compared. Not compounded.

## FX P&L {#fx-pnl}

The part of P&L that came from exchange rates moving, not the price, for holdings priced in another currency. Zero for holdings in pounds.

## Volatility {#volatility}

How much the price swung from day to day while you held it, scaled to a year. Higher means a bumpier ride.

## Max drawdown {#max-drawdown}

The biggest fall from a high point to a later low while you held it.

## Sharpe ratio {#sharpe}

Return above the Bank of England rate, for each unit of volatility. Around 1 or above is good; below 0 means cash would have done better.

## Period returns {#period-returns}

The price change over one day, one week, one month, three months and since 1 January, in the holding's own currency.

## Cost (GBP) {#cost-gbp}

On the Transactions tab: money paid for a buy (shown negative), or received from a sell or dividend (positive), in pounds at the trade's own exchange rate, after commission.

## Line P&L {#line-pnl}

On the Transactions tab, per trade. A buy shows today's value minus what you paid. A sell compares the sale price with today's price: positive means selling was the better choice. A dividend shows the amount received.

## Vehicle {#vehicle}

What kind of thing a holding is: Stock (one company), ETF (gold and crypto ETCs included), Fund (pension and other funds) or Other (cash, the house, crypto coins, indices). Set in Data → Instruments.

## Asset type and category {#asset-type}

Asset type is the broad theme, such as Tech-AI, Gold or Fund. Category is the finer group: the company for a single share, the sector or region for a fund.

## Snapshot {#snapshot}

A frozen record of what everything was worth, taken every evening at 22:30. Comparisons use it as it was, never recalculated.

## FX rate {#fx-rate}

How much of the trade's currency one pound bought on the trade date. It is stored with the trade, so the trade's cost in pounds never changes afterwards.

## Universe {#universe}

The group of instruments the Markets tabs show: your holdings, everything tracked, one asset type, the holdings of one ETF, or a set you pick.

## Rebased return {#rebased}

Every line starts at zero on the same date, so instruments with very different prices can be compared on one chart.

## Momentum score {#momentum-score}

A return divided by how much that instrument normally moves. A score of 2 means a move twice its usual size. Within about half a point of zero is just noise; above 1 is a real move. The trend score uses a long window, the recent score a short one.

## Momentum quadrants {#momentum-quadrants}

Rising: trend up and still rising. Pullback: trend up but dipping lately. Falling: trend down and still falling. Recovering: trend down but bouncing lately.

## Fall from 52-week high {#drawdown-52w}

How far the price is below its highest close of the past year. Also shown against the instrument's normal swings, so a quick 15% fall counts for more than a slow one, and a fall in a calm asset counts for more than the same fall in a jumpy one.

## Moving averages {#moving-averages}

The average closing price of the last 50 or 200 trading days. Being below the 200-day average for a long time is a sign of a weak trend.

## Trend phase {#trend-phase}

Uptrend: the 50-day average is above the 200-day and the price is above the 50-day. Weakening: the averages are still in an uptrend but the price has dropped below the 50-day. Downtrend: the reverse of an uptrend. Improving: a downtrend where the price has climbed back above the 50-day.

## Next cross {#next-cross}

How many trading days until the 50-day average crosses the 200-day if the price stays where it is. For information only; it does not change the status.

## Monitor status {#monitor-status}

Review: the price has reached your review level, or the fall from the high is large. Watch: close to your review level, a sizeable fall, or a long time below the 200-day average. OK: none of these. The thresholds are in Data → Config.

## Your levels {#levels}

The buy, sell and review prices you set per instrument on the Monitor tab, in its own quote units (pence for a London listing). A level is "near" when the price is close to it and "hit" once it has been reached.

## Yield curve {#yield-curve}

Government borrowing rates for different lengths of time, from a few months to 30 years. Normally longer loans pay more.

## Curve inversion {#curve-inversion}

When short-term rates are higher than long-term rates. It has often come before a recession.

## Real yield {#real-yield}

The interest rate after inflation, from inflation-linked government bonds.

## Breakeven inflation {#breakeven}

The inflation the bond market expects: the normal rate minus the real rate.

## Credit spread {#credit-spread}

The extra interest risky companies pay over the government. It widens when lenders get nervous.

## VIX {#vix}

The market's expected swings in US shares over the next month, from option prices. Often called the fear gauge; above 20 is usually nervous.

## VIX term structure {#vix-term}

One-month expected swings divided by three-month. Above 1 means fear right now is higher than further out.

## MOVE {#move}

The bond market's version of VIX: expected swings in US government bond yields.

## Fear & Greed {#fear-greed}

CNN's 0 to 100 score of market mood from seven signals. Below 25 is extreme fear, above 75 extreme greed.

## Gold / copper ratio {#gold-copper}

Copper does well when the economy grows, gold when people are worried. More copper against gold tends to go with higher interest rates.

## Miners vs gold {#miners-gold}

Gold-mining shares against gold itself. Miners usually move more than gold, so them rising faster backs up gold's move.

## Weight {#weight}

How much of a fund one holding makes up, as a percentage of the fund.

## Overlap {#overlap}

How much two funds hold in common: for each company both own, the smaller of its two weights, added up.

## Day estimate {#day-estimate}

A guess at today's move for a fund that has not priced yet: its holdings' moves today, converted to pounds and weighted by their share of the fund.

## Estimate error {#estimate-error}

How far the estimate has typically been from the fund's real move over recent weeks. The smaller, the more you can trust today's estimate.

## Booked day {#booked-day}

Whether the fund's price shows a market move on the same day or the next day. Pension funds usually show it the next day.

## Priced share {#priced-share}

How much of the fund the estimate could actually price. Listed is the share of the fund in the holdings on file, Priced the part with a price, Traded the part that has a price for that day.

## Split {#split}

One bank line divided across several categories, such as a cash withdrawal for cleaning and a tutor. Totals count each part in its own category.

## Pending {#pending}

A bank line with no category yet. It waits in the Manual Queue.

## Rule {#rule}

A keyword, and sometimes an amount, that gives matching bank lines a category automatically when they are imported.

## Provisional {#provisional}

A line from a CSV export whose statement has not been uploaded yet. It is checked once the statement arrives.

## Reconciled {#reconciled}

A statement whose stored lines add up to its totals, whose opening balance is the previous statement's closing balance, and with no stray lines in its period. "Break" means one of those checks failed; "Missing" means a statement was never uploaded.

## Carry forward {#carry-forward}

Pension allowance left unused in the previous three tax years, which can be used this year once this year's allowance is used up.

## Taper {#taper}

The rule that cuts the pension allowance for people on high incomes.

## Price unit {#price-unit}

Whether a price is quoted in pounds, pence, dollars or index points. A London price in pence is divided by 100 to get pounds.

## Composite {#composite}

A pension fund made up of several underlying funds in fixed proportions. Its price is worked out from theirs.
