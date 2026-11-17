"""
Tests for Portfolio -> Transactions account / vehicle filters, the
instruments.vehicle column and scripts/seed_vehicle.py. Synthetic database
only; Yahoo lookups are mocked.

    python -m pytest tests/test_txn_account_vehicle.py -q
"""
import sqlite3

import pandas as pd
import pytest

from core import config
from core.repo import portfolio as repo
from core.repo import standing
from scripts import seed_vehicle as sv


@pytest.fixture
def fund_db(tmp_path, monkeypatch):
    path = tmp_path / "funds.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE instruments (fund_id TEXT PRIMARY KEY, name TEXT,
            asset_type TEXT, currency TEXT, price_unit TEXT, category TEXT,
            source TEXT, source_id TEXT, provider TEXT, holdings_id TEXT,
            active INTEGER DEFAULT 1);
        CREATE TABLE prices (fund_id TEXT, date TEXT, close REAL);
        CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT UNIQUE,
            owner TEXT, wrapper TEXT, sort_order INTEGER,
            active INTEGER DEFAULT 1);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, fund_id TEXT,
            account TEXT, trade_date TEXT, type TEXT, quantity REAL,
            price REAL, currency TEXT, fx_rate REAL, price_unit TEXT,
            commission REAL DEFAULT 0);
        CREATE TABLE portfolio_holdings (fund_id TEXT PRIMARY KEY,
            units REAL);
        INSERT INTO accounts (name, owner, wrapper, sort_order, active)
        VALUES ('Ahmet ISA', 'Ahmet', 'ISA', 1, 1),
               ('Ahmet SIPP', 'Ahmet', 'SIPP', 2, 1),
               ('Old Trading', 'Ahmet', 'Trading', 3, 0),
               ('Burcu ISA', 'Burcu', 'ISA', 4, 1);
        INSERT INTO instruments (fund_id, name, asset_type, currency,
            price_unit, category, source, source_id) VALUES
            ('YF:NVDA', 'NVIDIA', 'Tech-AI', 'USD', 'dollar', 'NVIDIA',
             'yahoo', 'NVDA'),
            ('YF:SEMI.L', 'iShares Semis', 'Tech-AI', 'USD', 'dollar',
             'Semis', 'yahoo', 'SEMI.L'),
            ('YF:PHGP.L', 'WisdomTree Physical Gold GBP', 'Gold', 'GBP',
             'pence', 'Gold', 'yahoo', 'PHGP.L'),
            ('COMPOSITE:HSBC_EM', 'HSBC Pension EM', 'Fund', 'GBP',
             'pound', 'EM', 'composite', NULL),
            ('GB00B849FB47:GBP', 'Some FT fund', 'Fund', 'GBP', 'pound',
             'UK', 'ft', 'GB00B849FB47:GBP'),
            ('0P0001EWE2', 'HSBC Sharia', 'Fund', 'GBP', 'pound', 'Sharia',
             'ft', '0P0001EWE2'),
            ('LGUK:LSE:GBX', 'L&G UK', 'UK-Equity', 'GBP', 'pence', 'UK',
             'ft', 'LGUK:LSE:GBX'),
            ('CASH:GBP', 'Cash', 'Cash', 'GBP', 'pound', 'Cash', 'manual',
             NULL),
            ('YF:^VIX', 'VIX', 'Indicator', 'USD', 'point', 'Vol', 'yahoo',
             '^VIX'),
            ('YF:GBPUSD=X', 'GBP/USD', 'FX', 'USD', 'dollar', 'FX', 'yahoo',
             'GBPUSD=X'),
            ('YF:7203.T', 'Toyota', 'Constituent', 'JPY', 'yen', 'Toyota',
             'yahoo', '7203.T'),
            ('YF:BTC-USD', 'Bitcoin', 'Crypto', 'USD', 'dollar', 'Bitcoin',
             'yahoo', 'BTC-USD'),
            ('MYSTERY', 'Unknown thing', 'Other', 'GBP', 'pound', 'X',
             'manual', NULL);
        INSERT INTO transactions (fund_id, account, trade_date, type,
            quantity, price, currency, fx_rate, commission) VALUES
            ('YF:NVDA', 'Ahmet ISA', '2026-10-01', 'BUY', 10, 100, 'USD',
             1.3, 0),
            ('YF:SEMI.L', 'Ahmet ISA', '2026-10-02', 'BUY', 5, 20, 'USD',
             1.3, 0),
            ('YF:PHGP.L', 'Old Trading', '2026-10-03', 'BUY', 2, 3000,
             'GBP', 1, 0),
            ('COMPOSITE:HSBC_EM', 'Ahmet SIPP', '2026-10-04', 'BUY', 100, 1.6,
             'GBP', 1, 0),
            ('YF:NVDA', NULL, '2026-10-05', 'SELL', 1, 110, 'USD', 1.3, 0),
            ('YF:NVDA', 'Legacy Acc', '2026-10-06', 'BUY', 1, 90, 'USD',
             1.3, 0);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    return path


def _set_vehicles(path, mapping):
    standing.ensure_vehicle_column()
    conn = sqlite3.connect(path)
    conn.executemany("UPDATE instruments SET vehicle = ? WHERE fund_id = ?",
                     [(v, f) for f, v in mapping.items()])
    conn.commit()
    conn.close()


# --- schema -------------------------------------------------------------------

def test_vehicle_column_added_once(fund_db):
    standing.ensure_vehicle_column()
    standing.ensure_vehicle_column()
    cols = [r[1] for r in sqlite3.connect(fund_db).execute(
        "PRAGMA table_info(instruments)")]
    assert cols.count("vehicle") == 1


def test_clean_vehicle():
    assert standing.clean_vehicle("etf") == "ETF"
    assert standing.clean_vehicle(" Stock ") == "Stock"
    assert standing.clean_vehicle("") is None
    assert standing.clean_vehicle("Bond") is None
    assert standing.clean_vehicle(float("nan")) is None


def test_save_instruments_keeps_and_cleans_vehicle(fund_db):
    df = standing.instruments()
    rows = df.to_dict("records")
    for r in rows:
        if r["fund_id"] == "YF:NVDA":
            r["vehicle"] = "stock"
        if r["fund_id"] == "YF:SEMI.L":
            r["vehicle"] = "Bond"           # not a vehicle -> blank
    standing.save_instruments(rows)
    got = standing.instruments().set_index("fund_id")["vehicle"]
    assert got["YF:NVDA"] == "Stock"
    assert pd.isna(got["YF:SEMI.L"])


# --- filters ------------------------------------------------------------------

def test_transactions_include_account(fund_db):
    df = repo.transactions_filtered()
    assert "account" in df.columns and "vehicle" in df.columns
    assert len(df) == 6


def test_account_filter(fund_db):
    df = repo.transactions_filtered(accounts=["Ahmet ISA"])
    assert sorted(df["fund_id"]) == ["YF:NVDA", "YF:SEMI.L"]
    df = repo.transactions_filtered(accounts=[repo.NO_ACCOUNT])
    assert list(df["type"]) == ["SELL"]
    df = repo.transactions_filtered(accounts=["Ahmet SIPP", repo.NO_ACCOUNT])
    assert len(df) == 2


def test_vehicle_filter(fund_db):
    _set_vehicles(fund_db, {"YF:NVDA": "Stock", "YF:SEMI.L": "ETF",
                            "YF:PHGP.L": "ETF",
                            "COMPOSITE:HSBC_EM": "Fund"})
    assert set(repo.transactions_filtered(vehicle="Stock")["fund_id"]) == \
        {"YF:NVDA"}
    assert len(repo.transactions_filtered(vehicle="Stock")) == 3
    assert set(repo.transactions_filtered(vehicle="ETF")["fund_id"]) == \
        {"YF:SEMI.L", "YF:PHGP.L"}
    assert len(repo.transactions_filtered(vehicle="ALL")) == 6
    both = repo.transactions_filtered(accounts=["Ahmet ISA"],
                                      vehicle="Stock")
    assert list(both["fund_id"]) == ["YF:NVDA"]


def test_account_options(fund_db):
    opts = repo.transaction_account_options()
    labels = [o["label"] for o in opts]
    # accounts table in order (inactive included), then ledger-only names,
    # then (no account)
    assert labels == ["Ahmet ISA", "Ahmet SIPP", "Old Trading", "Burcu ISA",
                      "Legacy Acc", "(no account)"]
    assert opts[-1]["value"] == repo.NO_ACCOUNT


def test_fund_options_narrow(fund_db):
    _set_vehicles(fund_db, {"YF:NVDA": "Stock", "YF:SEMI.L": "ETF"})
    all_ids = {o["value"] for o in repo.transaction_fund_options()}
    assert all_ids == {"YF:NVDA", "YF:SEMI.L", "YF:PHGP.L",
                       "COMPOSITE:HSBC_EM"}
    isa = {o["value"] for o in repo.transaction_fund_options(["Ahmet ISA"])}
    assert isa == {"YF:NVDA", "YF:SEMI.L"}
    etf = {o["value"] for o in
           repo.transaction_fund_options(["Ahmet ISA"], "ETF")}
    assert etf == {"YF:SEMI.L"}


def test_vehicle_options_not_set(fund_db):
    labels = [o["label"] for o in repo.transaction_vehicle_options()]
    assert labels == ["All", "Stocks", "ETFs", "Funds", "Other", "Not set"]
    _set_vehicles(fund_db, {"YF:NVDA": "Stock", "YF:SEMI.L": "ETF",
                            "YF:PHGP.L": "ETF",
                            "COMPOSITE:HSBC_EM": "Fund"})
    labels = [o["label"] for o in repo.transaction_vehicle_options()]
    assert "Not set" not in labels
    assert len(repo.transactions_filtered(vehicle=repo.NO_VEHICLE)) == 0


# --- seed script ------------------------------------------------------------

FAKE_YAHOO = {
    "NVDA": {"name": "NVIDIA Corporation", "quote_type": "EQUITY"},
    "SEMI.L": {"name": "iShares MSCI Global Semiconductors UCITS ETF",
               "quote_type": "ETF"},
    "PHGP.L": {"name": "WisdomTree Physical Gold - GBP Daily Hedged",
               "quote_type": "EQUITY"},
    "LGUK.L": {"name": "L&G UK Equity UCITS ETF", "quote_type": "ETF"},
    "BTC-USD": {"name": "Bitcoin USD", "quote_type": "CRYPTOCURRENCY"},
}


def test_rules_without_yahoo():
    def r(fid, atype="", source="", source_id=None):
        return sv.rule({"fund_id": fid, "asset_type": atype,
                        "source": source, "source_id": source_id})
    assert r("CASH:GBP")[0] == "Other"
    assert r("ASSET:HOUSE")[0] == "Other"
    assert r("COMPOSITE:HSBC_EM")[0] == "Fund"
    assert r("GB00B849FB47:GBP")[0] == "Fund"
    assert r("0P0001EWE2")[0] == "Fund"
    assert r("YF:^VIX", "Indicator")[0] == "Other"
    assert r("YF:GBPUSD=X", "Crypto")[0] == "Other"
    assert r("YF:CL=F", "Commodity")[0] == "Other"
    assert r("YF:7203.T", "Constituent")[0] == "Stock"
    assert r("YF:NVDA", "Tech-AI", "yahoo", "NVDA") == (None, "", "NVDA")
    assert r("LGUK:LSE:GBX", "UK-Equity")[2] == "LGUK.L"
    assert r("MYSTERY", "Other")[:1] == (None,)
    assert r("MYSTERY", "Other")[2] is None


def test_from_quote():
    assert sv.from_quote({"name": "NVIDIA", "quote_type": "EQUITY"}) == \
        ("Stock", "Yahoo EQUITY")
    assert sv.from_quote({"name": "WisdomTree Physical Gold",
                          "quote_type": "EQUITY"})[0] == "ETF"
    assert sv.from_quote({"name": "x", "quote_type": "MUTUALFUND"})[0] == \
        "Fund"
    assert sv.from_quote({"name": "x", "quote_type": "WEIRD"})[0] is None
    assert sv.from_quote(None)[0] is None


def test_seed_preview_and_apply(fund_db, monkeypatch, capsys):
    monkeypatch.setattr(sv, "lookup", lambda s: FAKE_YAHOO.get(s))
    # A hand-set value must survive a normal run.
    _set_vehicles(fund_db, {"YF:SEMI.L": "Fund"})

    assert sv.main([]) == 0                     # preview writes nothing
    vals = standing.instruments().set_index("fund_id")["vehicle"]
    assert pd.isna(vals["YF:NVDA"])
    out = capsys.readouterr().out
    assert "Set by hand" in out and "MYSTERY" in out

    assert sv.main(["--apply"]) == 0
    vals = standing.instruments().set_index("fund_id")["vehicle"]
    assert vals["YF:NVDA"] == "Stock"
    assert vals["YF:SEMI.L"] == "Fund"          # kept (not --overwrite)
    assert vals["YF:PHGP.L"] == "ETF"           # gold ETC by name
    assert vals["LGUK:LSE:GBX"] == "ETF"
    assert vals["YF:BTC-USD"] == "Other"
    assert vals["COMPOSITE:HSBC_EM"] == "Fund"
    assert vals["GB00B849FB47:GBP"] == "Fund"
    assert vals["0P0001EWE2"] == "Fund"
    assert vals["CASH:GBP"] == "Other"
    assert vals["YF:^VIX"] == "Other"
    assert vals["YF:7203.T"] == "Stock"
    assert pd.isna(vals["MYSTERY"])
    backups = list((fund_db.parent / "backups").glob("funds_before_vehicle_*"))
    assert len(backups) == 1

    assert sv.main(["--apply", "--overwrite"]) == 0
    vals = standing.instruments().set_index("fund_id")["vehicle"]
    assert vals["YF:SEMI.L"] == "ETF"


def test_seed_traded_only_no_yahoo(fund_db, monkeypatch):
    def boom(_):
        raise AssertionError("Yahoo must not be called")
    monkeypatch.setattr(sv, "lookup", boom)
    rows = sv.candidates(traded_only=True)
    assert set(rows["fund_id"]) == {"YF:NVDA", "YF:SEMI.L", "YF:PHGP.L",
                                    "COMPOSITE:HSBC_EM"}
    result = sv.classify(rows, use_yahoo=False)
    got = result.set_index("fund_id")["vehicle"]
    assert got["COMPOSITE:HSBC_EM"] == "Fund"
    assert pd.isna(got["YF:NVDA"])


def test_page_table_renders_blank_account(fund_db):
    """A trade with no account (NaN from pandas) renders as a dash."""
    from pages import portfolio_txns as page
    conn = sqlite3.connect(fund_db)
    conn.execute("INSERT INTO prices VALUES ('YF:NVDA', '2026-10-30', 120)")
    conn.execute("INSERT INTO prices VALUES ('YF:GBPUSD=X', '2026-10-30', "
                 "1.3)")
    conn.commit()
    conn.close()
    table = page._table(None, "2026-01-01", None, "ALL", None, "ALL")
    text = str(table)
    assert "Ahmet ISA" in text and "Legacy Acc" in text
    assert "TOTAL" in text
