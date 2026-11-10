"""
Tests for the Markets "ETF" universe and Data -> ETF targets: members of a
target, adding and editing targets and baskets, and the background-job log
reader. Synthetic database only.

    python -m pytest tests/test_etf_universe.py -q
"""
import sqlite3

import pandas as pd
import pytest

from core import background, config
from core.repo import etf as etf_repo
from core.repo import fundest as est_repo
from scripts import add_constituents as ac
from ui import universe


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
        VALUES ('F0', 'NVIDIA', 'NVDA', 'NVDA US', 'YF:NVDA', 'F0');
        INSERT INTO instruments (fund_id, name, asset_type, currency,
            source, source_id) VALUES
            ('EMFUND', 'HSBC EM fund', 'Fund', 'GBP', 'ft', NULL),
            ('COMPOSITE:HSBC_EM', 'HSBC Pension EM', 'Fund', 'GBP',
             'composite', NULL),
            ('YF:NVDA', 'NVIDIA Corporation', 'Constituent', 'USD',
             'yahoo', 'NVDA'),
            ('YF:7203.T', 'Toyota Motor', 'Constituent', 'JPY', 'yahoo',
             '7203.T'),
            ('OTHER', 'Another pension fund', 'Fund', 'GBP', 'ft', NULL);
        INSERT INTO prices VALUES
            ('YF:NVDA', '2026-11-05', 100), ('YF:NVDA', '2026-11-06', 102),
            ('COMPOSITE:HSBC_EM', '2026-11-06', 1.6);
        INSERT INTO pension_holdings VALUES
            ('EMFUND', 'HSBC EM', '2026-11-04', 1, 'NVIDIA Corp',
             'NVDA:NSQ', 8.5),
            ('EMFUND', 'HSBC EM', '2026-11-04', 2, 'Toyota Motor Corp',
             '7203:TYO', 4.0),
            ('EMFUND', 'HSBC EM', '2026-11-04', 3, 'Samsung Fire',
             '000810:KSC', 1.8),
            ('EMFUND', 'HSBC EM', '2026-11-04', 4, 'Mystery Co', NULL, 1.0),
            ('OTHER', 'Another pension fund', '2026-11-04', 1, 'NVIDIA Corp',
             'NVDA:NSQ', 5.0);
        INSERT INTO composites VALUES
            ('COMPOSITE:HSBC_EM', 'HSBC Pension EM', 'Fund', 'BB');
        INSERT INTO composite_components VALUES
            ('COMPOSITE:HSBC_EM', 'EMFUND', 1.0);
    """)
    conn.commit()
    conn.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    etf_repo.clear_cache()
    est_repo._ready = False
    est_repo._MEMBERS_CACHE.clear()
    yield path
    etf_repo.clear_cache()
    est_repo._ready = False
    est_repo._MEMBERS_CACHE.clear()


def test_members_fund_first_and_statuses(fund_db):
    df, info = est_repo.members("COMPOSITE:HSBC_EM")
    assert df.iloc[0]["is_fund"] and df.iloc[0]["fund_id"] == \
        "COMPOSITE:HSBC_EM"
    assert df.iloc[0]["status"] == est_repo.STATUS_FUND
    status = dict(zip(df["name"], df["status"]))
    assert status["NVIDIA Corp"] == est_repo.STATUS_OK
    assert status["Toyota Motor Corp"] == est_repo.STATUS_NO_PRICES
    assert status["Samsung Fire"] == est_repo.STATUS_MISSING
    assert status["Mystery Co"] == est_repo.STATUS_UNMAPPED
    # Holdings by weight after the fund row.
    assert list(df["weight_pct"].iloc[1:]) == [8.5, 4.0, 1.8, 1.0]
    assert info["holdings"] == 4 and info["priced"] == 1
    assert info["listed"] == pytest.approx(15.3)
    assert info["priced_weight"] == pytest.approx(8.5)
    assert info["holdings_date"] == "2026-11-04"


def test_member_ids_and_universe(fund_db):
    ids = est_repo.member_ids("COMPOSITE:HSBC_EM")
    # Fund first; only rows with an instruments row; no duplicates.
    assert ids == ["COMPOSITE:HSBC_EM", "YF:NVDA", "YF:7203.T"]
    store = {"mode": "etf", "picked": [], "types": [],
             "etf": "COMPOSITE:HSBC_EM"}
    assert universe.resolve_ids(store) == ids
    assert universe.etf_target(store) == "COMPOSITE:HSBC_EM"
    assert universe.etf_target({"mode": "all", "etf": "X"}) is None
    assert universe.resolve_ids({"mode": "etf", "etf": None}) == []


def test_members_cache_follows_database(fund_db):
    est_repo.members("COMPOSITE:HSBC_EM")
    conn = sqlite3.connect(fund_db)
    conn.execute("INSERT INTO prices VALUES ('YF:7203.T', '2026-11-06', "
                 "3000)")
    conn.commit()
    conn.close()
    _, info = est_repo.members("COMPOSITE:HSBC_EM")
    assert info["priced"] == 2


def test_add_holdings_target(fund_db):
    opts = {o["value"] for o in est_repo.candidate_options()}
    assert "FUND:OTHER" in opts
    assert "COMPOSITE:HSBC_EM" not in opts          # seeded already
    assert est_repo.suggested("FUND:OTHER") == {
        "label": "Another pension fund", "actual_fund_id": "OTHER"}
    ok, msg, key = est_repo.add_target("FUND:OTHER")
    assert ok and key == "FUND:OTHER" and "1 holdings" in msg
    row = est_repo.target("FUND:OTHER")
    assert row["label"] == "Another pension fund"
    assert row["actual_fund_id"] == "OTHER" and row["kind"] == "holdings"
    ok, msg, _ = est_repo.add_target("FUND:OTHER")
    assert not ok and "already" in msg
    ok, _, _ = est_repo.add_target("FUND:NOPE")
    assert not ok
    df, info = est_repo.members("FUND:OTHER")
    assert info["priced"] == 1 and df.iloc[0]["fund_id"] == "OTHER"


def test_add_basket_and_weights(fund_db):
    assert est_repo.basket_key(" aiga 2 ") == "BASKET:AIGA2"
    assert est_repo.basket_key("basket:x") == "BASKET:X"
    assert est_repo.basket_key("  ") is None
    ok, _, key = est_repo.add_target("cocoa", "Cocoa ETC", "YF:COCO.L",
                                     kind="basket")
    assert ok and key == "BASKET:COCOA"
    n, msg = est_repo.save_basket(key, [
        {"fund_id": "CC=F", "weight": 70}, {"fund_id": "YF:KC=F",
                                            "weight": "30"},
        {"fund_id": "", "weight": 5}, {"fund_id": "SB=F", "weight": 0},
        {"fund_id": "CC=F", "weight": 10}])
    assert n == 2 and "100.00" in msg
    b = est_repo.basket(key)
    assert dict(zip(b["fund_id"], b["weight"])) == {"YF:CC=F": 70.0,
                                                    "YF:KC=F": 30.0}
    hold, _ = est_repo.constituents(est_repo.target(key))
    assert list(hold["fund_id"]) == ["YF:CC=F", "YF:KC=F"]


def test_save_targets_and_hidden_target(fund_db):
    rows = est_repo.all_targets().to_dict("records")
    em = next(r for r in rows if r["target"] == "COMPOSITE:HSBC_EM")
    em.update(label="EM renamed", active=0, residual_fund_id="YF:EEM",
              hedged="1", sort_order="5")
    assert est_repo.save_targets([em]) == 1
    assert "COMPOSITE:HSBC_EM" not in set(est_repo.targets()["target"])
    row = est_repo.target("COMPOSITE:HSBC_EM")      # hidden still found
    assert row["label"] == "EM renamed" and row["residual_fund_id"] == \
        "YF:EEM" and row["hedged"] == 1
    hidden = est_repo.all_targets()
    assert int(hidden.loc[hidden["target"] == "COMPOSITE:HSBC_EM",
                          "active"].iloc[0]) == 0


def test_needed_for_one_target(fund_db):
    wanted, unmapped = ac._needed(False, 0.0, "COMPOSITE:HSBC_EM")
    assert {"YF:NVDA", "YF:7203.T", "YF:000810.KS"} <= set(wanted["fund_id"])
    assert "YF:ZS=F" not in set(wanted["fund_id"])  # baskets left out
    assert list(unmapped["name"]) == ["Mystery Co"]
    with pytest.raises(SystemExit):
        ac._needed(False, 0.0, "BASKET:NOPE")


def test_background_status(tmp_path, monkeypatch):
    monkeypatch.setattr(background, "LOG_DIR", tmp_path)
    assert background.busy("job") is None
    log = tmp_path / "job_20261110_210000.log"
    log.write_text("Checking 3 symbols\n  [1/3] AAA ok\n")
    s = background.status(str(log))
    assert not s["finished"] and "AAA" in s["text"]
    assert background.busy("job") == str(log)
    log.write_text("x\n== finished ok\n")
    s = background.status(str(log))
    assert s["finished"] and s["ok"]
    assert background.busy("job") is None
    log.write_text("x\n== finished failed\n\n")
    assert not background.status(str(log))["ok"]
    assert background.status(str(tmp_path / "gone.log"))["finished"]
