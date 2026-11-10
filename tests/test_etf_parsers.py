"""Tests for the ETF holdings parsers and the importer's file-name handling,
on small synthetic files and an in-memory database - never the real one.

    python -m pytest tests/test_etf_parsers.py -q
"""
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from importers import etf_parsers as p
from importers import etf_holdings as eh

TODAY = date(2026, 10, 27)

# Global X download since Oct 2026: headers on row 1, no date in the file.
GLOBALX_CURRENT = '''Net Assets (%),Ticker,Name,SEDOL,Market Price ($),Shares Held,Market Value ($)
10.47%,MRNA,MODERNA INC,BGSXTS3,$200.10,"54,946","$10,994,695"
3.48%,1548 HK,GENSCRIPT BIOTECH CORP,BD9Q2J2,$4.03,"905,718","$3,649,690"
1.28%,AZN LN,ASTRAZENECA PLC,0989529,$171.65,"7,794","$1,337,879"
0.02%,,CASH,,$1.00,"22,880","$22,880"
-0.02%,,OTHER PAYABLE & RECEIVABLES,,$1.00,"-23,679","-$23,679"
"The information contained herein may not be reproduced, redistributed or used to create any derivative works."
'''

# The older Global X layout: fund name, date line, then headers.
GLOBALX_OLD = '''Global X Genomics & Biotechnology ETF
Holdings Data as of 10/16/2026
% of Net Assets,Ticker,Name,SEDOL,Market Price ($),Shares Held,Market Value ($)
10.10,MRNA,MODERNA INC,BGSXTS3,190.00,"54,946","10,439,740"
0.02,,CASH,,1.00,"22,880","22,880"
'''

GLOBALX_ASOF = '''AS_OF_DATE,TICKER,NAME,SEDOL,ISIN,NET_ASSETS,COUNTRY
2026-10-16,MRNA,MODERNA INC,BGSXTS3,US60770K1079,10.1,US
2026-10-16,,CASH,,,0.02,
'''

# WisdomTree US site download since Oct 2026.
WISDOMTREE_CURRENT = '''Group,Name,Security Ticker,FIGI,Shares,Weight
Information Technology,International Business Machines Corp,IBM UN,BBG000BLNNH6,"80,896",6.75%
Information Technology,Fujitsu Ltd,6702 JT,BBG000BGJXV4,"521,545",4.90%
Information Technology,LAM RESEARCH CORP,US5128073062,BBG000BNFLM9,"9,689",0.73%
Other,Pasqal Holding Sa,PSQL US,BBG024R3XWG9,"226,119",0.41%
Other,US DOLLAR,N/A,CASH-US DOLLAR,"489,139",0.16%
Other,DREYFUS TRSY OBLIG CASH MGMT CL INS,N/A,CASH-DREYFUS TRSY CASH,"210,352",0.07%
Other,EURO,N/A,CASH-EURO,914,0.00%
'''

WISDOMTREE_OLD = '''Date,Fund,Account,Security Description,Security Ticker,Shares,Mkt Value,Weight %
10/16/2026,WQTM,1,International Business Machines Corp,IBM UN,80896,"20,000,000",0.0675
10/16/2026,WQTM,1,Fujitsu Ltd,6702 JT,521545,"14,500,000",0.049
'''

# WisdomTree Europe's thin download: no tickers, not supported.
WISDOMTREE_EU = '''Sector,Name,Weight
Information Technology,Ionq Inc,4.45%
,EURO INCOME A/C,0.11%
'''


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding='utf-8')
    return str(path)


# ── Global X ────────────────────────────────────────────────────────────

def test_globalx_current_layout(tmp_path):
    h, d = p.parse_globalx_csv(write(tmp_path, 'g.csv', GLOBALX_CURRENT))
    assert d is None                              # dated by the importer
    assert [x['ticker'] for x in h] == ['MRNA', '1548 HK', 'AZN LN']
    assert h[0]['weight_pct'] == 10.47
    assert h[0]['market_value'] == 10994695.0
    assert h[0]['sedol'] == 'BGSXTS3'


def test_globalx_old_layouts_unchanged(tmp_path):
    h, d = p.parse_globalx_csv(write(tmp_path, 'g.csv', GLOBALX_OLD))
    assert d == '2026-10-16'
    assert [x['ticker'] for x in h] == ['MRNA']
    assert h[0]['weight_pct'] == 10.10

    h, d = p.parse_globalx_csv(write(tmp_path, 'a.csv', GLOBALX_ASOF))
    assert d == '2026-10-16'
    assert [x['name'] for x in h] == ['MODERNA INC']


# ── WisdomTree ──────────────────────────────────────────────────────────

def test_wisdomtree_current_layout(tmp_path):
    h, d = p.parse_wisdomtree_csv(write(tmp_path, 'w.csv', WISDOMTREE_CURRENT))
    assert d is None
    names = [x['name'] for x in h]
    assert names == ['International Business Machines Corp', 'Fujitsu Ltd',
                     'LAM RESEARCH CORP', 'Pasqal Holding Sa']   # cash gone
    ibm = h[0]
    assert ibm['ticker'] == 'IBM UN'
    assert ibm['sector'] == 'Information Technology'
    assert ibm['weight_pct'] == 6.75
    lam = h[2]                                    # ISIN in the ticker column
    assert lam['ticker'] == '' and lam['isin'] == 'US5128073062'


def test_wisdomtree_old_layout_unchanged(tmp_path):
    h, d = p.parse_wisdomtree_csv(write(tmp_path, 'w.csv', WISDOMTREE_OLD))
    assert d == '2026-10-16'
    assert [x['ticker'] for x in h] == ['IBM UN', '6702 JT']
    assert abs(h[0]['weight_pct'] - 6.75) < 1e-9


def test_wisdomtree_eu_file_is_not_read(tmp_path):
    h, _ = p.parse_wisdomtree_csv(write(tmp_path, 'w.csv', WISDOMTREE_EU))
    assert h == []


# ── File names ──────────────────────────────────────────────────────────

MAP = {'GNOM': 'YF:GNOM', 'WQTM': 'YF:WQTM.L', 'AINF': 'YF:AINF.L'}


def test_match_prefix():
    assert eh.match_prefix('AINF_20260610', MAP) == ('AINF', 'YF:AINF.L')
    assert eh.match_prefix('GNOM-holdings-Oct-23-2026', MAP) == \
        ('GNOM', 'YF:GNOM')
    assert eh.match_prefix('WQTM_2710_fund-holdings-58730141', MAP) == \
        ('WQTM', 'YF:WQTM.L')
    assert eh.match_prefix('XXXX-holdings', MAP) == ('XXXX-HOLDINGS', None)


def test_date_from_name():
    f = eh.date_from_name
    assert f('GNOM-holdings-Oct-23-2026.csv', TODAY) == '2026-10-23'
    assert f('AINF_20261016.csv', TODAY) == '2026-10-16'
    assert f('X_2026-10-16.csv', TODAY) == '2026-10-16'
    assert f('WQTM_2710_fund-holdings-58730141.csv', TODAY) is None
    assert f('GNOM-holdings-Oct-28-2026.csv', TODAY) is None     # future
    assert f('GNOM-holdings-Oct-23-2024.csv', TODAY) is None     # too old


# ── import_file end to end ──────────────────────────────────────────────

def _conn():
    conn = sqlite3.connect(':memory:')
    eh.create_tables(conn)
    return conn


def test_import_globalx_dated_from_name(tmp_path):
    day = date.today() - timedelta(days=4)
    name = f"GNOM-holdings-{day.strftime('%b-%d-%Y')}.csv"
    conn = _conn()
    r = eh.import_file(conn, Path(write(tmp_path, name, GLOBALX_CURRENT)),
                       MAP, {'GNOM': 'globalx'})
    assert r['status'] == 'imported' and r['rows'] == 3
    assert r['fund_id'] == 'YF:GNOM'
    assert r['date'] == day.strftime('%Y-%m-%d')
    assert 'date from the file name' in r['message']


def test_import_wisdomtree_dated_today(tmp_path):
    conn = _conn()
    r = eh.import_file(
        conn, Path(write(tmp_path, 'WQTM_2710_fund-holdings-58730141.csv',
                         WISDOMTREE_CURRENT)),
        MAP, {'WQTM': 'wisdomtree'})
    assert r['status'] == 'imported' and r['rows'] == 4
    assert r['date'] == date.today().strftime('%Y-%m-%d')
    assert 'used today' in r['message']
    isin = conn.execute("SELECT isin FROM etf_holdings "
                        "WHERE name = 'LAM RESEARCH CORP'").fetchone()[0]
    assert isin == 'US5128073062'
