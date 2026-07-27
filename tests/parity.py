"""
Parity check: new core.finance vs legacy data.py, on the live database.
Runs both P&L engines over the same transactions and compares per-fund to
the penny. Commission is 0 everywhere (no column yet), so it's like-for-like.
"""
import sys, importlib.util
from pathlib import Path
import pandas as pd

sys.path.insert(0, str(Path.home()/"pusula"))
FTS = Path.home()/"FTScrapper"
sys.path.insert(0, str(FTS))

# --- load legacy data.py as a module ---
spec = importlib.util.spec_from_file_location("legacy_data", FTS/"data.py")
legacy = importlib.util.module_from_spec(spec)
sys.modules["legacy_data"] = legacy
spec.loader.exec_module(legacy)

from core import finance as fin

# Build the same inputs the legacy dashboard uses
df = legacy.load_data()                    # raw prices
instruments = legacy.load_instruments()
df_combined = legacy.build_df_combined(df)
gbpusd = legacy.get_gbpusd(df)
fx_rates = legacy.get_fx_rates(df)

# Legacy result
legacy_pnl = legacy.calc_pnl(df_combined, instruments, gbpusd, fx_rates)
legacy_pnl = legacy_pnl.set_index("fund_id")

# New result: replicate the per-fund loop using core.finance
import sqlite3
conn = sqlite3.connect(legacy.DB_PATH)
txns = pd.read_sql_query("""
    SELECT t.fund_id, t.account, t.trade_date, t.type, t.quantity, t.price,
           t.currency, t.fx_rate, i.name, i.price_unit, i.category
    FROM transactions t LEFT JOIN instruments i ON t.fund_id=i.fund_id
    ORDER BY t.fund_id, t.trade_date
""", conn)
conn.close()

price_map = fin.latest_price_map(df_combined)
import config as cfg
comp_defs = {c["fund_id"]: c for c in getattr(cfg, "COMPOSITE_FUNDS", [])}

new_rows = {}
for fund_id, g in txns.groupby("fund_id"):
    inst = instruments.get(fund_id, {})
    punit = inst.get("price_unit", "pound")
    curr = inst.get("currency", "GBP")

    # current price gbp - mirror calc_pnl's branching
    # need qty first to know if position open; run position_pnl in two passes:
    # pass 1 with current=None to get qty, then compute price, then final.
    # Simpler: compute qty via a light pass.
    tmp = fin.position_pnl(g, None)
    if tmp["qty"] <= 0:
        cur = None
    elif fund_id.startswith("COMPOSITE:"):
        cd = comp_defs.get(fund_id)
        cur = fin.composite_price_gbp(fund_id, cd["components"], price_map.get,
                                      instruments, gbpusd, fx_rates) if cd else None
    elif fund_id.startswith(("CASH:", "ASSET:")):
        cur = 1.0
    else:
        cur = fin.to_gbp(price_map.get(fund_id), punit, curr, gbpusd, fx_rates)

    res = fin.position_pnl(g, cur)
    if res["pnl"] is None:
        continue
    new_rows[fund_id] = res

# Compare
fields = [("PnL","pnl"),("Realised","realised"),("Dividends","dividends"),
          ("Cost Basis","cost_basis"),("Current Value","current_value"),("Qty","qty")]
mismatch = 0; checked = 0
for fid in legacy_pnl.index:
    if fid not in new_rows:
        print(f"  MISSING in new: {fid}"); mismatch += 1; continue
    checked += 1
    L = legacy_pnl.loc[fid]; N = new_rows[fid]
    for lname, nname in fields:
        lv = L[lname]; nv = N[nname]
        lv = None if pd.isna(lv) else lv
        if lv is None and nv is None: continue
        if lv is None or nv is None:
            print(f"  {fid} {lname}: legacy={lv} new={nv}"); mismatch += 1; continue
        if abs(float(lv)-float(nv)) > 0.01:
            print(f"  {fid} {lname}: legacy={float(lv):.2f} new={float(nv):.2f} diff={float(lv)-float(nv):+.2f}")
            mismatch += 1

extra = set(new_rows) - set(legacy_pnl.index)
for fid in extra:
    print(f"  EXTRA in new: {fid}"); mismatch += 1

print(f"\nfunds checked: {checked}")
print(f"total value legacy: {legacy_pnl['PnL'].sum():,.2f}")
print(f"total value new:    {sum(r['pnl'] for r in new_rows.values()):,.2f}")
print("RESULT:", "PARITY OK - all match to the penny" if mismatch==0 else f"{mismatch} MISMATCHES")
