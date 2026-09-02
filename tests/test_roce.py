"""Regression tests for core.roce against three hand-checked ledgers.

    python -m pytest tests/test_roce.py -s
    python tests/test_roce.py
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.roce import compute_position, aggregate

ASOF = pd.Timestamp("2026-09-02")


def mk(rows):
    df = pd.DataFrame(rows, columns=["date", "qty", "cash_flow"])
    df["date"] = pd.to_datetime(df["date"])
    return df


WHEAT = mk([
    ("2025-07-28",  250,  -3548), ("2025-08-13",  120, -1594),
    ("2025-08-27",  130,  -1755), ("2025-09-08",  120, -1588),
    ("2025-09-19",  250,  -3348), ("2025-09-29",  150, -1990),
    ("2025-10-07",  180,  -2363), ("2025-10-13",  200, -2556),
    ("2025-10-24",  200,  -2648), ("2025-10-29",  200, -2756),
    ("2025-11-04",  200,  -2887), ("2025-11-06", -200,  2810),
    ("2025-11-26", -250,   3417), ("2025-12-15",  250, -3306),
    ("2025-12-31",  150,  -1912), ("2026-01-27",  200, -2566),
    ("2026-03-11", -200,   2948), ("2026-03-25", -200,  2876),
    ("2026-05-07",  250,  -3695), ("2026-05-15",  150, -2370),
    ("2026-06-03", -350,   5088), ("2026-06-08", -150,  2139),
    ("2026-06-15", -200,   2804), ("2026-06-15", -450,  6316),
    ("2026-06-25", 1000, -14589), ("2026-06-25", -2000, 29178),
])

XTRACK = mk([
    ("2026-06-18",  430, -14845), ("2026-06-22",  470, -16222),
    ("2026-07-08",  200,  -7020), ("2026-07-14", -470,  16788),
    ("2026-07-17",  370, -13182), ("2026-08-04",  200,  -7265),
    ("2026-08-07", -200,   7325), ("2026-08-25", -370,  13368),
])

COPPER = mk([
    ("2025-11-20",  800,  -4665), ("2025-11-26",  600,  -3575),
    ("2025-11-28",  400,  -2420), ("2026-01-08",  700,  -4919),
    ("2026-01-16",  700,  -5313), ("2026-02-09",  450,  -3564),
    ("2026-03-03", -800,   6422), ("2026-03-03", -600,   4803),
    ("2026-03-03", -400,   3202), ("2026-03-03", -700,   5619),
    ("2026-03-03", -700,   5607), ("2026-03-03", -450,   3605),
    ("2026-03-10", 1200,  -9272), ("2026-03-17", -1200,  8807),
    ("2026-05-07",   50,   -404), ("2026-05-07", 4950, -40145),
    ("2026-05-11", 2500, -20625), ("2026-05-15",  500,  -4025),
    ("2026-05-26",  500,  -4133), ("2026-06-03",  400,  -3501),
    ("2026-06-10",  500,  -3900), ("2026-06-22",  600,  -5006),
    ("2026-06-25",  500,  -3840), ("2026-07-15", -1600, 12257),
    ("2026-07-20", -1900,  13781), ("2026-07-20", -1500, 10896),
    ("2026-07-31", -1500,  11778), ("2026-08-26", 1177, -10898),
])

CASES = [
    ("Wheat", WHEAT, 18.54),
    ("Xtrackers Financials", XTRACK, 36.38),
    ("Copper Miners", COPPER, 8.81),
]

def test_engine():
    results = []
    for name, df, px in CASES:
        p = compute_position(df, latest_price=px, asof=ASOF, instrument=name, name=name)
        results.append(p)
        net_cash = df["cash_flow"].sum()
        recon = p.qty * px + net_cash
        print(f"\n{name}  [{p.status}]")
        print(f"  qty {p.qty:>10,.0f}   book £{p.book_cost:>12,.0f}   wac {p.wac:>7.3f}")
        print(f"  realised   £{p.realised:>10,.0f}")
        print(f"  unrealised £{p.unrealised:>10,.0f}")
        print(f"  total P&L  £{p.total_pnl:>10,.0f}   (ledger recon £{recon:,.0f})")
        print(f"  days deployed {p.days_deployed:>5,}   capital-days £{p.capital_days:>14,.0f}")
        print(f"  avg capital  £{p.avg_capital:>10,.0f}   peak £{p.peak_capital:,.0f}")
        print(f"  ROCE {p.roce_total:>7.2%}   annualised {p.roce_annualised:>7.2%}")
        ident = p.roce_total * 365 / p.days_deployed
        print(f"  identity check: {ident:.6%} vs {p.roce_annualised:.6%}  "
              f"{'OK' if abs(ident - p.roce_annualised) < 1e-9 else 'FAIL'}")
        assert abs(recon - p.total_pnl) < 0.01, "ledger reconciliation failed"

    b = aggregate(results)
    print("\n" + "=" * 60)
    print("BASKET (all three)")
    print(f"  total P&L    £{b.total_pnl:,.0f}")
    print(f"  capital-days £{b.capital_days:,.0f}   (sum of parts "
          f"£{sum(p.capital_days for p in results):,.0f})")
    print(f"  days deployed {b.days_deployed:,}  avg £{b.avg_capital:,.0f}  peak £{b.peak_capital:,.0f}")
    print(f"  ROCE {b.roce_total:.2%}   annualised {b.roce_annualised:.2%}")
    w = sum((p.capital_days / b.capital_days) * p.roce_annualised for p in results)
    print(f"  weighted contributions {w:.6%} vs {b.roce_annualised:.6%}  "
          f"{'OK' if abs(w - b.roce_annualised) < 1e-9 else 'FAIL'}")
    for p in sorted(results, key=lambda x: -x.capital_days):
        print(f"    {p.instrument:<24} {p.capital_days / b.capital_days:>6.1%} of capital-days"
              f"   {p.roce_annualised:>+7.1%}")


if __name__ == "__main__":
    test_engine()
