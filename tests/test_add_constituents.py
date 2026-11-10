"""
Tests for scripts/add_constituents.py helpers and the holdings plumbing the
Day estimate relies on: Yahoo ids through fund holdings, the holdings_id
link, and the ticker-map lookup of the map's first row.

    python -m pytest tests/test_add_constituents.py -q
"""
import sqlite3

import pandas as pd
import pytest

from core import config
from core.repo import etf as etf_repo
from core.repo import funds as funds_repo
from core.repo import fundest as est_repo
from scripts import add_constituents as ac


def test_yahoo_fund_id():
    assert est_repo.yahoo_fund_id("YF:AMD") == "YF:AMD"
    assert est_repo.yahoo_fund_id("AMD") == "YF:AMD"
    assert est_repo.yahoo_fund_id(None) is None
    assert est_repo.yahoo_fund_id(float("nan")) is None


@pytest.mark.parametrize("ft, yahoo", [
    ("7203:TYO", "YF:7203.T"),
    ("285A:TYO", "YF:285A.T"),
    ("NESN:VTX", "YF:NESN.SW"),
    ("ROG:SWX", "YF:ROG.SW"),
    ("BP.:LSE", "YF:BP.L"),
    ("BT.A:LSE", "YF:BT-A.L"),
    ("REL:LSE", "YF:REL.L"),
    ("SAP:GER", "YF:SAP.DE"),
    ("SAPX:GER", "YF:SAP.DE"),
    ("ALVX:GER", "YF:ALV.DE"),
    ("TLXX:GER", "YF:TLX.DE"),
    ("SAN:MCE", "YF:SAN.MC"),
    ("SU:PAR", "YF:SU.PA"),
    ("D05:SES", "YF:D05.SI"),
    ("000810:KSC", "YF:000810.KS"),
    ("NPN:JNB", "YF:NPN.JO"),
    ("ITUB4:SAO", "YF:ITUB4.SA"),
    ("700:HKG", "YF:0700.HK"),
    ("5:HKG", "YF:0005.HK"),
    ("8299:TWO", "YF:8299.TWO"),
    ("TSLA:NSQ", "YF:TSLA"),
    ("BRK.B:NYQ", "YF:BRK-B"),
    ("tsla:nsq", "YF:TSLA"),
])
def test_yahoo_from_ft(ft, yahoo):
    assert etf_repo.yahoo_from_ft(ft) == yahoo


@pytest.mark.parametrize("ft", [None, "", "-", "TSLA", "XYZ:NOWHERE", "-:LSE"])
def test_yahoo_from_ft_none(ft):
    assert etf_repo.yahoo_from_ft(ft) is None


def test_units():
    assert ac._units("GBp") == ("GBP", "pence")
    assert ac._units("GBP") == ("GBP", "pound")
    assert ac._units("USX") == ("USD", "cents")
    assert ac._units("EUR") == ("EUR", "euro")
    assert ac._units("KRW") == ("KRW", "won")
    assert ac._units("") == (None, None)


def test_row_types():
    share = ac._row("YF:ASML.AS", "ASML", {"name": "ASML Holding N.V.",
                    "quote_type": "EQUITY", "currency": "EUR"},
                    "Constituent", {})
    assert share["asset_type"] == "Constituent"
    assert share["category"] == "ASML Holding N.V."
    fut = ac._row("YF:ZC=F", "ZC=F", {"name": "Corn Futures,Dec-2026",
                  "quote_type": "FUTURE", "currency": "USX"},
                  "Constituent", {})
    assert fut["asset_type"] == "Indicator"
    assert fut["name"] == "Corn (front month)"
    cotton = ac._row("YF:CT=F", "CT=F", {"name": "Cotton Mar 26",
                     "quote_type": "", "currency": "USX"}, "Constituent", {})
    assert cotton["asset_type"] == "Indicator"
    assert cotton["name"] == "Cotton (front month)"
    assert (fut["currency"], fut["price_unit"]) == ("USD", "cents")
    fx = ac._row("YF:GBPEUR=X", "GBPEUR=X", {"name": "GBP/EUR",
                 "quote_type": "CURRENCY", "currency": "EUR"},
                 "Constituent", {"asset_type": "FX", "price_unit": "ratio",
                                 "category": "FX"})
    assert (fx["asset_type"], fx["currency"], fx["price_unit"]) == \
        ("FX", "EUR", "ratio")


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
        CREATE TABLE etf_holdings (etf_fund_id TEXT, scraped_date TEXT,
            ticker TEXT, name TEXT, sector TEXT, asset_class TEXT,
            weight_pct REAL, market_value REAL, location TEXT,
            currency TEXT, isin TEXT, sedol TEXT);
        CREATE TABLE pension_holdings (fund_id TEXT, fund_name TEXT,
            scraped_date TEXT, rank INTEGER, name TEXT, ticker TEXT,
            weight_pct REAL);
        CREATE TABLE composites (fund_id TEXT PRIMARY KEY, display_name TEXT,
            asset_type TEXT, account TEXT);
        CREATE TABLE composite_components (composite_fund_id TEXT,
            component_fund_id TEXT, weight REAL);
        CREATE TABLE stock_identifier_map (figi TEXT PRIMARY KEY, name TEXT,
            base_ticker TEXT, exch_code TEXT, bloomberg_code TEXT,
            raw_ticker TEXT, sedol TEXT, isin TEXT, yahoo_id TEXT,
            security_type TEXT, group_figi TEXT, reviewed INTEGER,
            notes TEXT, ft_code TEXT);
        INSERT INTO stock_identifier_map (figi, name, base_ticker,
            bloomberg_code, yahoo_id, group_figi)
        VALUES ('F0', 'NVIDIA', 'NVDA', 'NVDA US', 'YF:NVDA', 'F0'),
               ('F1', 'TSMC', '2330', '2330 TT', '2330.TW', 'F1');
        INSERT INTO instruments (fund_id, name, source, holdings_id)
        VALUES ('WRAP', 'Fid Fil UK HSBC Islamic', 'ft', 'UNDER'),
               ('UNDER', 'HSBC Islamic Global Equity', 'ft', NULL);
        INSERT INTO pension_holdings VALUES
            ('UNDER', 'HSBC Islamic', '2026-11-04', 1, 'NVIDIA Corp',
             'NVDA:NSQ', 8.5),
            ('UNDER', 'HSBC Islamic', '2026-11-04', 2, 'Taiwan Semi',
             '2330:TAI', 2.9),
            ('UNDER', 'HSBC Islamic', '2026-11-04', 3, 'Alphabet Inc Class C',
             NULL, 2.5),
            ('UNDER', 'HSBC Islamic', '2026-11-04', 4, 'Toyota Motor Corp',
             '7203:TYO', 2.0);
        INSERT INTO composites VALUES
            ('COMPOSITE:HSBC_SHARIA', 'HSBC Pension Sharia', 'Fund', 'AB');
        INSERT INTO composite_components VALUES
            ('COMPOSITE:HSBC_SHARIA', 'WRAP', 1.0);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    etf_repo.clear_cache()
    est_repo._ready = False
    yield path
    etf_repo.clear_cache()
    est_repo._ready = False


def test_first_map_row_resolves(fund_db):
    smap, lookups, parents = etf_repo._stock_map()
    gfigi, _, yahoo = etf_repo._resolve("NVDA", "NVIDIA", None, smap,
                                        lookups, parents)
    assert gfigi == "F0" and yahoo == "YF:NVDA"


def test_wrapper_reads_underlying_holdings(fund_db):
    assert funds_repo.holdings_source("WRAP") == "UNDER"
    df, date = funds_repo.fund_holdings("WRAP")
    assert date == "2026-11-04"
    assert dict(zip(df["name"], df["yahoo_id"]))["NVIDIA Corp"] == "YF:NVDA"
    comp, _ = funds_repo.composite_holdings("COMPOSITE:HSBC_SHARIA")
    assert len(comp) == 4
    # Not in the map, priced from its FT ticker.
    assert dict(zip(comp["name"], comp["yahoo_id"]))["Toyota Motor Corp"] \
        == "YF:7203.T"


def test_targets_seed_and_constituents(fund_db):
    rows = est_repo.targets()
    assert list(rows["target"]) == ["ETF:YF:SEMI.L", "COMPOSITE:HSBC_SHARIA",
                                    "BASKET:UC14", "BASKET:AIGA"]
    sharia = est_repo.target("COMPOSITE:HSBC_SHARIA")
    hold, date = est_repo.constituents(sharia)
    ids = dict(zip(hold["name"], hold["fund_id"]))
    assert ids["NVIDIA Corp"] == "YF:NVDA"          # map stores YF:NVDA
    assert ids["Taiwan Semi"] == "YF:2330.TW"       # bare id is prefixed once
    assert pd.isna(ids["Alphabet Inc Class C"])
    uc14, _ = est_repo.constituents(est_repo.target("BASKET:UC14"))
    assert uc14["weight_pct"].sum() == pytest.approx(100.0)
    aiga, _ = est_repo.constituents(est_repo.target("BASKET:AIGA"))
    assert aiga["weight_pct"].sum() == pytest.approx(100.0, abs=0.05)
    assert "YF:KE=F" in set(aiga["fund_id"])

    assert ids["Toyota Motor Corp"] == "YF:7203.T"

    wanted, unmapped = ac._needed(False, 0.0)
    assert {"YF:2330.TW", "YF:7203.T"} <= set(wanted["fund_id"])
    assert set(unmapped["name"]) == {"Alphabet Inc Class C"}
    assert unmapped.iloc[0]["ticker"] == "-"


def test_new_basket_added_to_seeded_tables(fund_db):
    """A database seeded before AIGA existed gets AIGA at start-up; rows
    already there - including edited weights - are left alone."""
    conn = sqlite3.connect(fund_db)
    conn.executescript("""
        CREATE TABLE etf_est_targets (target TEXT PRIMARY KEY, label TEXT,
            kind TEXT, actual_fund_id TEXT, residual_fund_id TEXT,
            hedged INTEGER DEFAULT 0, sort_order INTEGER DEFAULT 50,
            active INTEGER DEFAULT 1);
        CREATE TABLE etf_est_basket (target TEXT, fund_id TEXT, weight REAL,
            PRIMARY KEY (target, fund_id));
        INSERT INTO etf_est_targets (target, label, kind, actual_fund_id,
            sort_order) VALUES ('BASKET:UC14', 'UC14 mine', 'basket',
            'YF:UC15.L', 90);
        INSERT INTO etf_est_basket VALUES ('BASKET:UC14', 'YF:HG=F', 100.0);
    """)
    conn.commit()
    conn.close()
    rows = est_repo.targets()
    assert list(rows["target"]) == ["BASKET:UC14", "BASKET:AIGA"]
    assert rows.iloc[0]["label"] == "UC14 mine"
    assert list(est_repo.basket("BASKET:UC14")["fund_id"]) == ["YF:HG=F"]
    assert len(est_repo.basket("BASKET:AIGA")) == 10
