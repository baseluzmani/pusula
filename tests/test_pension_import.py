"""
Pension statement import: parsing the provider CSV, netting it per fund,
turning £ into composite units, Close fully, and the write. Synthetic
database; the CSV texts are the real export format (Nov 2026).

    python -m pytest tests/test_pension_import.py -q
"""
import sqlite3

import pytest

from core import config
from core.repo import pension_import as pen

SWITCH_CSV = """Fund Name,Fund currency,Units,Unit price date,Value,Contribution Type
Salary Sacrifice Contributions
European (ex UK) Equities - passive,GBP,"6,650.33",19/11/2026,"£27,425.98",Employee Salary Sacrifice
UK Equities - active,GBP,"3,397.33",19/11/2026,"£12,906.45",Employee Salary Sacrifice
Shariah Law Equities - passive,GBP,-197.58,19/11/2026,"−£2,764.14",Employee Salary Sacrifice
Emerging Markets Equities - active,GBP,"-2,321.72",19/11/2026,"−£7,178.76",Employee Salary Sacrifice
UK Equities - passive,GBP,"-8,606.49",19/11/2026,"−£30,389.51",Employee Salary Sacrifice
Employer Match Contributions
European (ex UK) Equities - passive,GBP,"5,578.36",19/11/2026,"£23,005.16",Employer Additional Salary Sacrifice
UK Equities - active,GBP,"2,849.67",19/11/2026,"£10,825.89",Employer Additional Salary Sacrifice
Shariah Law Equities - passive,GBP,-170.63,19/11/2026,"−£2,387.11",Employer Additional Salary Sacrifice
Emerging Markets Equities - active,GBP,"-1,895.97",19/11/2026,"−£5,862.34",Employer Additional Salary Sacrifice
UK Equities - passive,GBP,"-7,244.86",19/11/2026,"−£25,581.60",Employer Additional Salary Sacrifice
Lump Sum AVCs
European (ex UK) Equities - passive,GBP,495.79,19/11/2026,"£2,044.63",Lump Sum AVC net pay
UK Equities - active,GBP,253.3,19/11/2026,£962.31,Lump Sum AVC net pay
Shariah Law Equities - passive,GBP,-16.61,19/11/2026,−£232.37,Lump Sum AVC net pay
Emerging Markets Equities - active,GBP,-157.81,19/11/2026,−£487.95,Lump Sum AVC net pay
UK Equities - passive,GBP,-647.58,19/11/2026,"−£2,286.60",Lump Sum AVC net pay
Employer Core Contributions
European (ex UK) Equities - passive,GBP,"7,462.67",19/11/2026,"£30,776.03",Employer contribution
UK Equities - active,GBP,"3,812.3",19/11/2026,"£14,482.94",Employer contribution
Shariah Law Equities - passive,GBP,-228.3,19/11/2026,"−£3,193.93",Employer contribution
Emerging Markets Equities - active,GBP,"-2,535.9",19/11/2026,"−£7,841.00",Employer contribution
UK Equities - passive,GBP,"-9,692.46",19/11/2026,"−£34,224.08",Employer contribution
"""

CONTRIB_CSV = """Fund Name,Fund currency,Units,Unit price date,Value,Contribution Type
Salary Sacrifice Contributions     (  Total Value in GBP :                   2116.67)
Asia Pacific (ex Japan) Equities - passive,GBP,119.59,29/10/2026,£529.17,Employee Salary Sacrifice
Emerging Markets Equities - active,GBP,162.72,29/10/2026,£529.17,Employee Salary Sacrifice
Shariah Law Equities - passive,GBP,35.42,29/10/2026,£529.17,Employee Salary Sacrifice
UK Equities - active,GBP,137.55,29/10/2026,£529.16,Employee Salary Sacrifice
Employer Match Contributions     (  Total Value in GBP :                    740.83)
Asia Pacific (ex Japan) Equities - passive,GBP,41.86,29/10/2026,£185.21,Employer Additional Salary Sacrifice
Emerging Markets Equities - active,GBP,56.95,29/10/2026,£185.21,Employer Additional Salary Sacrifice
Shariah Law Equities - passive,GBP,12.4,29/10/2026,£185.21,Employer Additional Salary Sacrifice
UK Equities - active,GBP,48.14,29/10/2026,£185.20,Employer Additional Salary Sacrifice
Employer Core Contributions     (  Total Value in GBP :                    976.75)
Asia Pacific (ex Japan) Equities - passive,GBP,55.18,29/10/2026,£244.19,Employer contribution
Emerging Markets Equities - active,GBP,75.09,29/10/2026,£244.19,Employer contribution
Shariah Law Equities - passive,GBP,16.34,29/10/2026,£244.19,Employer contribution
UK Equities - active,GBP,63.47,29/10/2026,£244.18,Employer contribution
"""

COMPOSITES = {
    "COMPOSITE:HSBC_EUROPE": 7.04,
    "COMPOSITE:HSBC_UK_ACTIVE": 3.23,
    "COMPOSITE:HSBC_SHARIA": 6.15,
    "COMPOSITE:HSBC_EM": 3.17,
    "COMPOSITE:HSBC_UK_PASSIVE": 198.0,
    "COMPOSITE:HSBC_ASIA_PAC": 10.92,
}


@pytest.fixture
def fund_db(tmp_path, monkeypatch):
    path = tmp_path / "funds.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE instruments (fund_id TEXT PRIMARY KEY, name TEXT,
            asset_type TEXT, currency TEXT, price_unit TEXT);
        CREATE TABLE prices (fund_id TEXT, date TEXT, open REAL, high REAL,
            low REAL, close REAL, volume REAL);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, fund_id TEXT,
            account TEXT, trade_date TEXT, type TEXT, quantity REAL,
            price REAL, currency TEXT, fx_rate REAL, commission REAL);
        CREATE TABLE portfolio_holdings (fund_id TEXT PRIMARY KEY,
            units REAL, updated_at TEXT);
        CREATE TABLE composites (fund_id TEXT PRIMARY KEY,
            display_name TEXT, asset_type TEXT, account TEXT);
        CREATE TABLE composite_components (composite_fund_id TEXT,
            component_fund_id TEXT, weight REAL);
    """)
    for fid, px in COMPOSITES.items():
        conn.execute("INSERT INTO instruments VALUES (?, ?, 'Fund', 'GBP', "
                     "'pound')", (fid, fid.split(":")[1]))
        conn.execute("INSERT INTO composites VALUES (?, ?, 'Fund', "
                     "'AB Pension')", (fid, fid.split(":")[1]))
        conn.execute("INSERT INTO composite_components VALUES (?, 'X', 1)",
                     (fid,))
        for d, p in (("2026-10-28", px), ("2026-11-18", px)):
            conn.execute("INSERT INTO prices (fund_id, date, close) "
                         "VALUES (?, ?, ?)", (fid, d, p))
    # Holdings worth roughly what the provider holds, so the sells are covered
    for fid, units in (("COMPOSITE:HSBC_UK_PASSIVE", 467.0),
                       ("COMPOSITE:HSBC_SHARIA", 30000.0),
                       ("COMPOSITE:HSBC_EM", 33000.0)):
        conn.execute("INSERT INTO transactions (fund_id, account, trade_date,"
                     " type, quantity, price, currency, fx_rate, commission)"
                     " VALUES (?, 'AB Pension', '2026-04-24', 'BUY', ?, 1, "
                     "'GBP', 1, 0)", (fid, units))
        conn.execute("INSERT INTO portfolio_holdings VALUES (?, ?, 'x')",
                     (fid, units))
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def test_money_handles_unicode_minus_and_commas():
    assert pen._money('"−£30,389.51"') == -30389.51
    assert pen._money("£962.31") == 962.31
    assert pen._money("abc") is None


def test_parse_skips_headers_and_nets_a_switch():
    rows, problems = pen.parse_csv(SWITCH_CSV)
    assert problems == [] and len(rows) == 20
    net, trade_date = pen.aggregate(rows)
    assert trade_date == "2026-11-19"
    assert net["European (ex UK) Equities - passive"] == 83251.80
    assert net["UK Equities - active"] == 39177.59
    assert net["UK Equities - passive"] == -92481.79
    assert net["Emerging Markets Equities - active"] == -21370.05
    assert net["Shariah Law Equities - passive"] == -8577.55
    assert round(sum(net.values()), 2) == 0


def test_parse_contribution_with_totals_in_headers():
    rows, _ = pen.parse_csv(CONTRIB_CSV)
    net, trade_date = pen.aggregate(rows)
    assert trade_date == "2026-10-29" and len(net) == 4
    assert round(sum(net.values()), 2) == 3834.25
    assert all(v > 0 for v in net.values())


def test_plan_converts_pounds_at_composite_price(fund_db):
    net, d = pen.aggregate(pen.parse_csv(SWITCH_CSV)[0])
    p = pen.plan(net, d, "AB Pension")
    assert p["blocking"] == [] and p["net"] == 0
    by = {l["fund_id"]: l for l in p["lines"]}
    europe = by["COMPOSITE:HSBC_EUROPE"]
    assert europe["type"] == "BUY" and europe["price_date"] == "2026-11-18"
    assert europe["quantity"] == pytest.approx(83251.80 / 7.04)
    assert by["COMPOSITE:HSBC_UK_PASSIVE"]["type"] == "SELL"


def test_close_fully_sells_everything_and_keeps_the_net(fund_db):
    net, d = pen.aggregate(pen.parse_csv(SWITCH_CSV)[0])
    p = pen.plan(net, d, "AB Pension", ["COMPOSITE:HSBC_UK_PASSIVE"])
    by = {l["fund_id"]: l for l in p["lines"]}
    ukp = by["COMPOSITE:HSBC_UK_PASSIVE"]
    assert ukp["close"] and ukp["quantity"] == pytest.approx(467.0)
    # 467 x 198 = 92,466 - the £15.79 not sold comes off the buys
    assert p["net"] == pytest.approx(0, abs=0.01)
    buys = by["COMPOSITE:HSBC_EUROPE"]["value_used"] + \
        by["COMPOSITE:HSBC_UK_ACTIVE"]["value_used"]
    assert buys == pytest.approx(467.0 * 198.0 + 21370.05 + 8577.55)
    assert by["COMPOSITE:HSBC_EUROPE"]["value_used"] < 83251.80


def test_small_remainder_suggests_close_fully(fund_db):
    net, d = pen.aggregate(pen.parse_csv(SWITCH_CSV)[0])
    p = pen.plan(net, d, "AB Pension")
    ukp = [l for l in p["lines"]
           if l["fund_id"] == "COMPOSITE:HSBC_UK_PASSIVE"][0]
    assert any("Close fully" in w for w in ukp["warnings"])


def test_unmapped_fund_blocks_until_mapped(fund_db):
    text = CONTRIB_CSV.replace("UK Equities - active", "Japan Equities - passive")
    net, d = pen.aggregate(pen.parse_csv(text)[0])
    p = pen.plan(net, d, "AB Pension")
    assert any("Japan" in b for b in p["blocking"])
    with pytest.raises(ValueError):
        pen.write(p)
    pen.save_mapping("Japan Equities - passive", "COMPOSITE:HSBC_EUROPE")
    assert pen.plan(net, d, "AB Pension")["blocking"] == []


def test_price_jump_is_flagged(fund_db):
    conn = sqlite3.connect(fund_db)
    conn.execute("INSERT INTO prices (fund_id, date, close) VALUES "
                 "('COMPOSITE:HSBC_UK_ACTIVE', '2026-11-19', 207.7)")
    conn.commit()
    conn.close()
    net, d = pen.aggregate(pen.parse_csv(SWITCH_CSV)[0])
    line = [l for l in pen.plan(net, d, "AB Pension")["lines"]
            if l["fund_id"] == "COMPOSITE:HSBC_UK_ACTIVE"][0]
    assert any("pounds/pence" in w for w in line["warnings"])


def test_write_inserts_recalcs_and_backs_up(fund_db):
    net, d = pen.aggregate(pen.parse_csv(CONTRIB_CSV)[0])
    result = pen.write(pen.plan(net, d, "AB Pension"))
    assert result["written"] == 4
    assert (fund_db.parent / "backups").exists()
    conn = sqlite3.connect(fund_db)
    n = conn.execute("SELECT COUNT(*) FROM transactions WHERE trade_date = "
                     "'2026-10-29'").fetchone()[0]
    units = conn.execute("SELECT units FROM portfolio_holdings WHERE "
                         "fund_id = 'COMPOSITE:HSBC_UK_ACTIVE'").fetchone()[0]
    conn.close()
    assert n == 4
    assert units == pytest.approx(958.54 / 3.23, rel=1e-6)
