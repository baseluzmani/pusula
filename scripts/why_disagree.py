#!/usr/bin/env python3
"""
Why do the P&L tab and the P&L ROCI tab disagree about one instrument?

    cd ~/pusula && source .venv/bin/activate
    python scripts/why_disagree.py SEMI.L

Both engines are run over the same ledger and the same mark, and every input
is printed side by side. The last section applies the invariant that settles
which one is wrong:

    total P&L  ==  qty * mark  +  net cash flow

That identity holds whatever cost method you use and whatever order the
transactions come in - it is just "what I hold is worth X, and I have paid out
Y net to get here". An engine that violates it has a bug; an engine that
satisfies it is arithmetically sound whether or not you like its conventions.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import finance as fin, roce, valuation
from core.repo import portfolio as repo

pd.set_option("display.width", 200)
pd.set_option("display.max_columns", 50)


def resolve(needle: str, instruments: dict) -> str:
    """Accept a fund_id, a bare ticker or part of a name.

    fund_ids are namespaced (YF:SEMI.L, COMPOSITE:HSBC_EM, GB00...:GBP), so the
    ticker shown on screen is rarely the key. Exact match wins; otherwise fall
    back to a substring search over ids and names.
    """
    if needle in instruments:
        return needle

    n = needle.lower()
    hits = [k for k, v in instruments.items()
            if n in k.lower() or n in (v.get("name") or "").lower()]

    if not hits:
        print(f"\nNothing matches {needle!r}. Closest ids containing any part:")
        for k in list(instruments)[:0]:
            pass
        stem = n.split(":")[-1].split(".")[0]
        near = [k for k in instruments if stem and stem in k.lower()][:10]
        for k in near:
            print("   ", k, "|", instruments[k].get("name"))
        sys.exit(1)

    if len(hits) > 1:
        print(f"\n{needle!r} matches {len(hits)} instruments - be more specific:")
        for k in hits[:15]:
            print("   ", k, "|", instruments[k].get("name"))
        sys.exit(1)

    if hits[0] != needle:
        print(f"\nresolved {needle!r} -> {hits[0]!r}")
    return hits[0]


def main(fund_id: str) -> None:
    print("=" * 74)
    print(f"  {fund_id}")
    print("=" * 74)

    # ---- shared inputs ---------------------------------------------------
    prices = repo.prices()
    instruments = repo.instruments()
    rates = fin.fx_rates(prices)
    price_map = fin.latest_price_map(prices)

    fund_id = resolve(fund_id, instruments)
    inst = instruments.get(fund_id, {})
    print(f"\ninstrument: currency={inst.get('currency')!r}  "
          f"price_unit={inst.get('price_unit')!r}  "
          f"name={inst.get('name')!r}")

    # Which price row is actually being used, and from when?
    rows = prices[prices["fund_id"] == fund_id].sort_values("date")
    if rows.empty:
        print("\nNo price rows at all for this fund_id. That is the problem.")
        return
    last = rows.iloc[-1]
    print(f"\nlatest price row: date={last['date'].date()}  close={last['close']}")
    print(f"  previous row:   date={rows.iloc[-2]['date'].date()}  "
          f"close={rows.iloc[-2]['close']}" if len(rows) > 1 else "")
    print(f"live GBPUSD: {rates['USD']:.4f}   GBPTRY: {rates['TRY']:.4f}")

    mark = valuation.holding_price_gbp(fund_id, instruments, price_map,
                                       rates["USD"], rates)
    print(f"mark in GBP: {mark}")

    # ---- the ledger, as each engine sees it ------------------------------
    txns = repo.transactions()
    mine = txns[txns["fund_id"] == fund_id].copy()
    print(f"\ntransactions for this fund: {len(mine)}")
    print(mine["type"].value_counts().to_string())

    non_trade = mine[~mine["type"].str.upper().isin(["BUY", "SELL"])]
    if not non_trade.empty:
        print(f"\n*** {len(non_trade)} row(s) the ROCI engine does NOT see "
              f"(it filters to BUY/SELL only):")
        cols = [c for c in ["trade_date", "type", "quantity", "price",
                            "currency", "fx_rate", "commission"]
                if c in non_trade.columns]
        print(non_trade[cols].to_string(index=False))

    # ---- engine 1: the P&L tab -------------------------------------------
    pl = fin.position_pnl(mine.sort_values("trade_date"), mark)
    print("\n--- P&L tab (core.finance.position_pnl) ---")
    for k in ["qty", "avg_cost", "cost_basis", "current_value",
              "realised", "dividends", "unrealised", "pnl"]:
        v = pl[k]
        print(f"  {k:<14} {v:,.2f}" if isinstance(v, float) else f"  {k:<14} {v}")

    # ---- engine 2: the ROCI tab ------------------------------------------
    ledger = roce.load_trades()
    mine_roce = ledger[ledger["instrument"] == fund_id]
    if mine_roce.empty:
        print("\nROCI engine sees no BUY/SELL rows for this fund.")
        return
    p = roce.compute_position(mine_roce, mark, pd.Timestamp.today().normalize(),
                              fund_id, inst.get("name", fund_id))
    print("\n--- P&L ROCI tab (core.roce) ---")
    print(f"  qty            {p.qty:,.2f}")
    print(f"  wac            {p.wac:,.4f}")
    print(f"  book_cost      {p.book_cost:,.2f}")
    print(f"  realised       {p.realised:,.2f}")
    print(f"  unrealised     {p.unrealised:,.2f}")
    print(f"  total_pnl      {p.total_pnl:,.2f}")
    print(f"  status         {p.status}")

    # ---- the invariant ---------------------------------------------------
    net_cash = float(mine_roce["cash_flow"].sum())
    truth = p.qty * (mark or 0) + net_cash
    print("\n--- invariant: total P&L == qty * mark + net cash flow ---")
    print(f"  qty * mark     {p.qty:,.0f} x {mark:,.4f} = {p.qty * mark:,.2f}")
    print(f"  net cash flow  {net_cash:,.2f}   (BUY/SELL only, commission included)")
    print(f"  => true total  {truth:,.2f}")
    print()
    print(f"  P&L tab says   {pl['pnl']:,.2f}   "
          f"({'OK' if abs(pl['pnl'] - truth) < 1 else f'off by {pl['pnl'] - truth:+,.2f}'})")
    print(f"  ROCI says      {p.total_pnl:,.2f}   "
          f"({'OK' if abs(p.total_pnl - truth) < 1 else f'off by {p.total_pnl - truth:+,.2f}'})")
    print()
    print(f"  difference between the two tabs: "
          f"{pl['pnl'] - p.total_pnl:+,.2f}")
    if not non_trade.empty:
        print("  (non-BUY/SELL rows above are excluded from 'net cash flow',")
        print("   so the invariant judges the trading result only)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "SEMI.L")