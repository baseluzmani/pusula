"""Tests for core.spending (pure), core.repo.spending and the one-off
migration - on scratch databases, never the real one.

    python -m pytest tests/test_spending.py -q
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import config, spending as sp          # noqa: E402

RULES = [("TFL TRAVEL", "Transport", "TfL"),
         ("TESCO", "Groceries", "Tesco"),
         ("TFL ", "Transport", "Other")]


def frame(rows):
    return pd.DataFrame(rows, columns=["date", "description", "amount"])


# --- parsing --------------------------------------------------------------------

def test_parse_bank_csv_reads_the_hsbc_export():
    raw = ('24/10/2026,Temu.com               London        GBR,-20.85\r\n'
           '20/10/2026,PAYMENT - THANK YOU,"3,439.00"\r\n'
           'not a date,junk,1\r\n'
           '\r\n').encode()
    df = sp.parse_bank_csv(raw)
    assert df.to_dict("records") == [
        {"date": "2026-10-24",
         "description": "Temu.com               London        GBR",
         "amount": -20.85},
        {"date": "2026-10-20", "description": "PAYMENT - THANK YOU",
         "amount": 3439.0}]


def test_parse_bank_csv_falls_back_to_latin_1():
    raw = "01/09/2026,CAF\xc9 NERO,-3.10\n".encode("latin-1")
    assert sp.parse_bank_csv(raw)["description"].iloc[0] == "CAF\xc9 NERO"


# --- classification ---------------------------------------------------------------

def test_manual_queue_lines_wait_for_review():
    for d in ["AMAZON* NV8092JQ4      LONDON        LND",
              "CASH RB SCOT OCT09 TESCO EL CO @08:56 ATM",
              "PAYPAL PAYMENT DD"]:
        m = sp.classify(d, RULES)
        assert (m.status, m.rule, m.category) == ("pending", sp.MANUAL, None)


def test_first_rule_in_order_wins():
    m = sp.classify("TFL TRAVEL CH          TFL.GOV.UK/CP GBR", RULES)
    assert (m.category, m.subcategory, m.status, m.rule) == \
        ("Transport", "TfL", "confirmed", "TFL TRAVEL")


def test_no_rule_is_pending_not_uncategorised():
    m = sp.classify("KFC                    ELTHAM        ENG", RULES)
    assert (m.category, m.status, m.rule) == (None, "pending", None)


def test_paypal_merchant_lines_can_be_remembered_bare_paypal_cannot():
    assert sp.remembers("PAYPAL *SHEIN          35314369001   LUX")
    assert not sp.remembers("PAYPAL PAYMENT")
    assert not sp.remembers("AMZNMktplace*BT0933RB4 LONDON        GBR")


@pytest.mark.parametrize("desc, kw", [
    ("TESCO STORES 6500      ELTHAM        GBR", "TESCO STORES"),
    ("MCDONALDS 606          LONDON        GBR", "MCDONALDS"),
    ("POUNDLAND LTD - 1884   ELTHAM        GBR", "POUNDLAND LTD"),
    ("SumUp  *Eltham Park So Greenwich     GBR", "SUMUP  *ELTHAM PARK SO"),
    ("KFC                    ELTHAM        ENG", "KFC"),
    ("JD WETHERSPOON Watford VIS", "JD WETHERSPOON WATFORD"),
    ("HSBC PREMIER543458 543458******2649 BP", "HSBC PREMIER543458"),
])
def test_suggest_keyword(desc, kw):
    assert sp.suggest_keyword(desc) == kw
    assert kw in desc.upper()          # the rule would match its own row


def test_keyword_problem():
    rows = ["KFC                    ELTHAM        ENG",
            "IAP KFC ELTHAM"]
    assert sp.keyword_problem(rows, "kfc") is None
    assert "does not appear" in sp.keyword_problem(rows, "KFC ELTHAM")
    assert "vary per purchase" in sp.keyword_problem(
        ["AMAZON UK* J90ME0ES5   LONDON        LND"], "AMAZON")
    assert sp.keyword_problem(rows, "  ") == "The keyword is empty."


# --- comparing a file with stored rows ------------------------------------------

def test_identical_rows_are_counted_not_collapsed():
    stored = frame([("2026-10-19", "NYX*Shell", -1.5)])
    new = frame([("2026-10-19", "NYX*Shell", -1.5),
                 ("2026-10-19", "NYX*Shell", -1.5)])
    out = sp.compare_with_existing(new, stored)
    assert out["state"].tolist() == ["exists", "new"]


def test_a_date_shifted_file_is_flagged_near():
    # Stored by posting date; the same purchases dated a day earlier.
    stored = frame([("2026-07-08", "HSBC - Neal Street", -2.5),
                    ("2026-07-09", "M&S LONDON", -39.55)])
    new = frame([("2026-07-07", "HSBC - Neal Street", -2.5),
                 ("2026-07-08", "M&S LONDON", -39.55),
                 ("2026-07-08", "NEW SHOP", -4.0),
                 ("2026-07-10", "NEWER SHOP", -1.0)])
    out = sp.compare_with_existing(new, stored)
    assert out["state"].tolist() == ["near", "near", "new", "new"]
    assert out["near_date"].tolist()[:2] == ["2026-07-08", "2026-07-09"]


def test_daily_repeats_in_a_correct_file_are_not_flagged():
    days = [f"2026-10-{d:02d}" for d in range(14, 18)]
    stored = frame([(d, "TFL TRAVEL CH", -5.8) for d in days[:2]])
    new = frame([(d, "TFL TRAVEL CH", -5.8) for d in days])
    out = sp.compare_with_existing(new, stored)
    assert out["state"].tolist() == ["exists", "exists", "new", "new"]


def test_stored_rows_outside_the_file_range_are_ignored():
    stored = frame([("2026-10-24", "TFL TRAVEL CH", -5.8)])
    new = frame([("2026-10-25", "TFL TRAVEL CH", -5.8)])
    assert sp.compare_with_existing(new, stored)["state"].tolist() == ["new"]


def test_guess_card_picks_the_biggest_overlap():
    new = frame([("2026-10-19", "TESCO", -9.4), ("2026-10-20", "M&S", -64.7)])
    stored = pd.DataFrame([
        ("Debit Card", "2026-10-19", "TESCO", -9.4),
        ("Credit Card", "2026-10-19", "TESCO", -9.4),
        ("Credit Card", "2026-10-20", "M&S", -64.7)],
        columns=["card", "date", "description", "amount"])
    assert sp.guess_card(new, stored) == ("Credit Card", 2)
    assert sp.guess_card(new, stored.iloc[0:0]) == (None, 0)


def test_month_table_nets_refunds_inside_a_category():
    df = pd.DataFrame({"date": ["2026-01-05", "2026-01-20", "2026-03-01"],
                       "category": ["Shopping"] * 3,
                       "amount": [-100.0, 30.0, -50.0]})
    t = sp.month_table(df, ["category"]).iloc[0]
    assert (t["Jan"], t["Feb"], t["Mar"], t["Total"]) == (-70, 0, -50, -120)


# --- repo, on a scratch database -----------------------------------------------

@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "scratch.db")
    from core.repo import spending as r
    r.ensure_tables()
    r.add_card("Credit Card")
    r.add_category("Groceries")
    r.add_subcategory("Groceries", "Tesco")
    return r


def rows_(*items, status="confirmed"):
    return [{"date": d, "description": desc, "amount": a,
             "category": "Groceries", "subcategory": "Tesco",
             "status": status} for d, desc, a in items]


def test_import_dedupes_by_count_and_reimport_adds_nothing(repo):
    batch = rows_(("2026-10-19", "TESCO", -9.4), ("2026-10-19", "TESCO", -9.4),
                  ("2026-10-20", "TESCO", -1.0))
    first = repo.import_rows("Credit Card", "a.csv", batch, 3)
    again = repo.import_rows("Credit Card", "a.csv", batch, 3)
    assert (first["inserted"], first["skipped"]) == (3, 0)
    assert (again["inserted"], again["skipped"]) == (0, 3)
    assert repo.counts()["total"] == 3
    assert repo.imports()["inserted"].tolist() == [0, 3]


def test_new_rules_go_to_the_end(repo):
    repo.save_rule("TESCO", "Groceries", "Tesco", priority=5)
    repo.save_rule("kfc", "Groceries", "Tesco")
    assert repo.rules()[["keyword", "priority"]].values.tolist() == \
        [["TESCO", 5], ["KFC", 6]]
    # Saving an existing keyword without a priority keeps its place.
    repo.save_rule("TESCO", "Groceries", "Tesco")
    assert repo.rules()["priority"].tolist() == [5, 6]


def test_update_rule_refuses_a_keyword_clash(repo):
    repo.save_rule("TESCO", "Groceries", "Tesco")
    repo.save_rule("KFC", "Groceries", "Tesco")
    kfc = int(repo.rules().set_index("keyword").loc["KFC", "id"])
    assert "already uses" in repo.update_rule(kfc, "tesco", "Groceries",
                                              "Tesco", None)
    assert repo.update_rule(kfc, "KFC ELTHAM", "Groceries", "Tesco",
                            None) is None
    assert "KFC ELTHAM" in repo.rules()["keyword"].tolist()


def test_rename_card_moves_its_history(repo):
    repo.import_rows("Credit Card", "a.csv",
                     rows_(("2026-10-19", "TESCO", -9.4)), 1)
    assert repo.rename_card("Credit Card", "HSBC Credit") is None
    assert repo.transactions()["card"].tolist() == ["HSBC Credit"]
    assert repo.last_import_card() == "HSBC Credit"
    assert repo.add_card("hsbc credit") == "hsbc credit already exists."


def test_assign_and_queue(repo):
    repo.import_rows("Credit Card", "a.csv",
                     rows_(("2026-10-19", "AMAZON", -5.0), status="pending"),
                     1)
    tid = int(repo.pending()["id"].iloc[0])
    assert repo.assign([tid], "Groceries", "Tesco") == 1
    assert repo.counts()["pending"] == 0
    repo.set_status([tid], "pending")
    assert repo.counts()["pending"] == 1


def test_remap(repo):
    repo.import_rows("Credit Card", "a.csv",
                     rows_(("2026-10-19", "TESCO", -9.4)), 1)
    assert repo.remap("Groceries", None, "Shopping", "Home") == 1
    assert repo.transactions()[["category", "subcategory"]].values.tolist() \
        == [["Shopping", "Home"]]


# --- migration --------------------------------------------------------------------

OLD_SCHEMA = """
CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT,
    source TEXT, description TEXT, amount REAL, category TEXT,
    subcategory TEXT, status TEXT, import_batch TEXT, created_at TEXT,
    is_credit INTEGER DEFAULT 0);
CREATE TABLE mapping_rules (id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT UNIQUE, category TEXT, subcategory TEXT, priority INTEGER);
CREATE TABLE manual_overrides (id INTEGER PRIMARY KEY, description TEXT,
    category TEXT, subcategory TEXT);
CREATE TABLE categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT,
    sort_order INTEGER);
CREATE TABLE subcategories (id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT, name TEXT);
"""


def test_migration_signs_amounts_and_merges_burcu_card(tmp_path):
    src = tmp_path / "spending.db"
    c = sqlite3.connect(src)
    c.executescript(OLD_SCHEMA)
    c.executemany(
        "INSERT INTO transactions (date, source, description, amount, "
        "category, subcategory, status, import_batch, created_at, is_credit) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-01-01', ?)",
        [("2025-12-30", "Burcu Card", "O2 DD", 20.0, "Household",
          "Utilities", "confirmed", "legacy_import", 0),
         ("2026-01-02", "Burcu Debit", "REFUND", 5.0, "Income", "Refund",
          "confirmed", "Burcu Debit_burcu_debit_1.csv", 1),
         ("2026-01-03", "Credit Card", "AMAZON", 9.99, None, None,
          "pending", "Credit Card_credit_1.csv", 0)])
    c.execute("INSERT INTO mapping_rules (keyword, category, subcategory, "
              "priority) VALUES ('O2 DD', 'Household', 'Utilities', 3)")
    c.execute("INSERT INTO categories (name, sort_order) "
              "VALUES ('Household', 0)")
    c.commit()
    c.close()

    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    sqlite3.connect(home / "data" / "funds.db").close()
    run = lambda *a: subprocess.run(                          # noqa: E731
        [sys.executable, str(ROOT / "scripts" / "migrate_spending.py"),
         "--source", str(src), *a], capture_output=True, text=True,
        env={"HOME": str(home), "PATH": ""})

    dry = run()
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "Nothing written" in dry.stdout
    assert run("--apply").returncode == 0

    t = sqlite3.connect(home / "data" / "funds.db")
    got = t.execute("SELECT card, description, amount, status FROM "
                    "spend_transactions ORDER BY date").fetchall()
    assert got == [("Burcu Debit", "O2 DD", -20.0, "confirmed"),
                   ("Burcu Debit", "REFUND", 5.0, "confirmed"),
                   ("Credit Card", "AMAZON", -9.99, "pending")]
    assert [r[0] for r in t.execute(
        "SELECT name FROM spend_cards ORDER BY sort_order")] == \
        ["Credit Card", "Burcu Debit"]
    assert t.execute("SELECT filename FROM spend_imports ORDER BY id"
                     ).fetchall()[1:] == [("burcu_debit_1.csv",),
                                          ("credit_1.csv",)]
    assert run("--apply").returncode == 1          # refuses a second copy


# --- pass 2: coverage and rule preview --------------------------------------------

def test_gaps_finds_quiet_stretches():
    dates = ["2026-09-05", "2026-09-07", "2026-10-08", "2026-10-09",
             "2026-10-09"]
    assert sp.gaps(dates, 10) == [("2026-09-08", "2026-10-07", 30)]
    assert sp.gaps(dates, 40) == []
    assert sp.gaps([], 10) == []


RULE_TABLE = pd.DataFrame([
    (1, "UBER", "Transport", "Taxi", 43),
    (2, "UBER   *EATS", "Food & Drink", "Restaurant", 88),
    (3, "TESCO", "Groceries", "Tesco", 50)],
    columns=["id", "keyword", "category", "subcategory", "priority"])

TXNS = pd.DataFrame([
    (10, "UBER   *EATS           HELP.UBER.COM ENG", "Transport", "Taxi",
     "confirmed"),
    (11, "UBER   *EATS           HELP.UBER.COM ENG", "Food & Drink",
     "Restaurant", "confirmed"),
    (12, "UBER   *TRIP           HELP.UBER.COM ENG", "Transport", "Taxi",
     "confirmed"),
    (13, "KFC                    ELTHAM        ENG", None, None, "pending"),
    (14, "AMAZON UBER GIFT", None, None, "pending")],
    columns=["id", "description", "category", "subcategory", "status"])


def test_preview_shows_an_earlier_rule_winning():
    # Editing the Eats rule in place: UBER (43) still comes first.
    res = sp.preview_rule(TXNS, RULE_TABLE, "UBER   *EATS", "Food & Drink",
                          "Restaurant", rule_id=2)
    assert res["matched"].empty
    assert res["beaten"] == {"UBER": 2}


def test_preview_with_a_priority_that_wins():
    res = sp.preview_rule(TXNS, RULE_TABLE, "UBER   *EATS", "Food & Drink",
                          "Restaurant", priority=42, rule_id=2)
    m = res["matched"].set_index("id")["state"].to_dict()
    assert m == {10: "differs", 11: "same"}


def test_preview_new_rule_goes_last_and_never_takes_manual_lines():
    res = sp.preview_rule(TXNS, RULE_TABLE, "kfc", "Food & Drink",
                          "Restaurant")
    assert res["matched"].set_index("id")["state"].to_dict() == \
        {13: "pending"}
    gift = sp.preview_rule(TXNS, RULE_TABLE, "GIFT", "Shopping", "Home")
    assert gift["matched"].empty and gift["beaten"] == {sp.MANUAL: 1}
