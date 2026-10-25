"""Tests for core.statements - the HSBC credit-card PDF reader.

The PDF layer is exercised by hand against real statements; these tests cover
everything after it on made-up lines, so no statement is committed.

    python -m pytest tests/test_statements.py -q
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import statements as st                 # noqa: E402


@pytest.mark.parametrize("raw, export", [
    (")))TESCO STORES 6500      ELTHAM",
     "TESCO STORES 6500      ELTHAM        GBR"),
    ("IAPKFC                    ELTHAM        ENG",
     "KFC                    ELTHAM        ENG"),
    ("AMAZON* WB9Y63SC5      LONDON        LND",
     "AMAZON* WB9Y63SC5      LONDON        LND"),
    ("IAPRoyal Borough of Green 02089215212",
     "Royal Borough of Green 02089215212   GBR"),
    ("PAYMENT - THANK YOU", "PAYMENT - THANK YOU"),
    ("NON-STERLING TRANSACTION FEE", "NON-STERLING TRANSACTION FEE"),
])
def test_export_description(raw, export):
    assert st.export_description(raw) == export


def test_rows_use_the_received_date_and_sign_credits():
    debit = st.row_from_line("08Aug2607Aug26", ")))TESCO STORES 6500      "
                             "ELTHAM", "12.35")
    credit = st.row_from_line("20Aug2620Aug26", "PAYMENT - THANK YOU",
                              "3,492.00CR")
    assert debit == {"date": "2026-08-08", "amount": -12.35,
                     "description": "TESCO STORES 6500      ELTHAM        GBR"}
    assert (credit["date"], credit["amount"]) == ("2026-08-20", 3492.0)
    with pytest.raises(ValueError):
        st.row_from_line("400.00TRY@64.1025", "", "6.24")


SUMMARY = """Account Summary
Credit Limit £ 9,000.00
PreviousBalance 1,234.56
Debits 2,000.10
Credits 1,234.56
New Balance 2,000.10
Statement Date 07 September 2026 Cardnumber Sheet number1 of4"""


def test_summary_from_text_ignores_missing_spaces():
    assert st.summary_from_text(SUMMARY) == {
        "previous_balance": 1234.56, "debits": 2000.10, "credits": 1234.56,
        "new_balance": 2000.10, "statement_date": "2026-09-07"}


def test_statement_period_and_adds_up():
    rows = pd.DataFrame({"date": ["2026-08-08", "2026-08-20", "2026-09-07"],
                         "description": ["A", "PAYMENT - THANK YOU", "B"],
                         "amount": [-1500.0, 1234.56, -500.10]})
    s = st.Statement(rows=rows, statement_date="2026-09-07", debits=2000.10,
                     credits=1234.56)
    assert s.period == ("2026-08-08", "2026-09-07")
    assert s.adds_up
    assert not st.Statement(rows=rows.iloc[:2], statement_date="2026-09-07",
                            debits=2000.10, credits=1234.56).adds_up
    # Month ends roll the way HSBC's statement day does.
    assert st.Statement(rows=rows, statement_date="2026-03-07").period == \
        ("2026-02-08", "2026-03-07")


def test_reconcile_reports_the_difference():
    stored = pd.DataFrame({"amount": [-1500.0, 1234.56, -500.10, -20.0]})
    r = st.reconcile(stored, debits=2000.10, credits=1234.56)
    assert (r["ok"], r["debits_diff"], r["credits_diff"], r["rows"]) == \
        (False, 20.0, 0.0, 4)
    assert st.reconcile(stored.iloc[:3], 2000.10, 1234.56)["ok"]


def test_a_non_pdf_is_refused():
    pytest.importorskip("pdfplumber")
    with pytest.raises(Exception):
        st.parse_hsbc_credit_pdf(b"24/10/2026,Temu.com,-20.85\n")
