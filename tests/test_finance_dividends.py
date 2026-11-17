"""
core.finance.position_pnl counts a dividend once (Nov 2026 fix). The legacy
method also took it off the cost basis, which raised unrealised P&L by the
same amount, so the P&L and Accounts tabs showed every dividend twice.

    python -m pytest tests/test_finance_dividends.py -q
"""
import pandas as pd
import pytest

from core import finance as fin


def _tx(*rows):
    return pd.DataFrame([dict(type=t, quantity=q, price=p, currency=ccy,
                              fx_rate=fx, price_unit="pound", commission=c)
                         for t, q, p, ccy, fx, c in rows])


def test_dividend_counted_once_on_open_position():
    r = fin.position_pnl(_tx(("BUY", 10, 100, "GBP", 1, 0),
                             ("DIVIDEND", 50, 0, "GBP", 1, 0)), 100.0)
    assert r["cost_basis"] == pytest.approx(1000)
    assert r["unrealised"] == pytest.approx(0)
    assert r["dividends"] == pytest.approx(50)
    assert r["pnl"] == pytest.approx(50)


def test_dividend_counted_once_after_sale():
    # Paid 1,000; dividend 50; sold for 1,100 -> 150 made in all.
    r = fin.position_pnl(_tx(("BUY", 10, 100, "GBP", 1, 0),
                             ("DIVIDEND", 50, 0, "GBP", 1, 0),
                             ("SELL", 10, 110, "GBP", 1, 0)), None)
    assert r["realised"] == pytest.approx(100)
    assert r["pnl"] == pytest.approx(150)


def test_pnl_is_cash_in_plus_value_minus_cash_out():
    # Buy 10 @ 100 + 5 commission, dividend 20, sell 4 @ 120 - 5 commission,
    # mark 130 on the 6 left.
    r = fin.position_pnl(_tx(("BUY", 10, 100, "GBP", 1, 5),
                             ("DIVIDEND", 20, 0, "GBP", 1, 0),
                             ("SELL", 4, 120, "GBP", 1, 5)), 130.0)
    cash_out = 10 * 100 + 5
    cash_in = 20 + 4 * 120 - 5
    assert r["pnl"] == pytest.approx(cash_in + 6 * 130 - cash_out)


def test_foreign_dividend_converted_at_its_rate():
    r = fin.position_pnl(_tx(("BUY", 10, 100, "USD", 1.25, 0),
                             ("DIVIDEND", 25, 0, "USD", 1.25, 0)), 80.0)
    assert r["dividends"] == pytest.approx(20)
    assert r["cost_basis"] == pytest.approx(800)
    assert r["pnl"] == pytest.approx(20)
