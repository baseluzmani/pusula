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
    # Spacing does not count: the fixed-width line has 'KFC ELTHAM' too.
    assert sp.keyword_problem(rows, "KFC ELTHAM") is None
    assert "does not appear" in sp.keyword_problem(rows, "KFC LONDON")
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



# --- pass 3: several files, other currencies ---------------------------------------

def test_combine_files_counts_an_overlap_once_and_keeps_true_repeats():
    a = frame([("2026-09-01", "COFFEE", -3.0), ("2026-09-01", "COFFEE", -3.0),
               ("2026-09-02", "TESCO", -9.0)])
    b = frame([("2026-09-01", "COFFEE", -3.0), ("2026-09-02", "TESCO", -9.0),
               ("2026-09-03", "TFL", -2.8)])
    got = sp.combine_files([a, b])
    assert got.values.tolist() == [["2026-09-01", "COFFEE", -3.0],
                                   ["2026-09-01", "COFFEE", -3.0],
                                   ["2026-09-02", "TESCO", -9.0],
                                   ["2026-09-03", "TFL", -2.8]]
    assert sp.combine_files([]).empty


RATES = pd.DataFrame({"date": ["2026-09-01", "2026-09-04", "2026-09-08"],
                      "rate": [67.0, 68.0, 68.5]})


def test_convert_to_gbp_takes_the_rate_on_or_before_each_day():
    df = pd.DataFrame({"date": ["2026-09-05", "2026-09-01", "2026-09-06"],
                       "description": ["A", "B", "C"],
                       "amount": [-680.0, -10.0, 68.5]})
    got = sp.convert_to_gbp(df, RATES)
    assert got["gbp"].tolist() == [-10.0, -0.15, 1.01]
    assert got["rate_date"].tolist() == ["2026-09-04", "2026-09-01",
                                         "2026-09-04"]
    assert got["amount"].tolist() == df["amount"].tolist()      # untouched


def test_convert_to_gbp_keeps_a_pound_figure_the_bank_noted():
    df = pd.DataFrame({"date": ["2026-09-09", "2026-09-09"],
                       "description": ["UBER", "SHOP"],
                       "amount": [-1192.24, -100.0],
                       "note": ["17.52 GBP", None]})
    assert sp.convert_to_gbp(df, RATES)["gbp"].tolist() == [-17.52, -1.46]


def test_convert_to_gbp_refuses_rows_older_than_the_rates():
    df = pd.DataFrame({"date": ["2026-08-31"], "description": ["A"],
                       "amount": [-1.0]})
    with pytest.raises(ValueError, match="2026-08-31"):
        sp.convert_to_gbp(df, RATES)
    with pytest.raises(ValueError):
        sp.convert_to_gbp(df, RATES.iloc[0:0])


def lira_rows(rate):
    return [{"date": "2026-09-05", "description": "MIGROS", "amount":
             round(-680.0 / rate, 2), "amount_orig": -680.0, "currency": "TRY",
             "category": "Groceries", "subcategory": "Tesco",
             "status": "confirmed"}]


def test_lira_rows_dedupe_on_the_amount_the_statement_printed(repo):
    repo.add_card("Turkey Card", "TRY")
    assert repo.card_currency("Turkey Card") == "TRY"
    assert repo.cards_in("TRY") == ["Turkey Card"]
    first = repo.import_rows("Turkey Card", "e.pdf", lira_rows(68.0), 1)
    # The same statement again after the rates were revised: still a repeat.
    again = repo.import_rows("Turkey Card", "e.pdf", lira_rows(67.5), 1)
    assert (first["inserted"], again["inserted"], again["skipped"]) == (1, 0, 1)
    got = repo.transactions().iloc[0]
    assert (got["amount"], got["amount_orig"], got["currency"]) == \
        (-10.0, -680.0, "TRY")
    seen = repo.existing_between("2026-09-01", "2026-09-30", "Turkey Card")
    assert seen["amount"].tolist() == [-680.0]       # compared in lira
    # Pound cards are unchanged: no original amount, no currency.
    repo.import_rows("Credit Card", "a.csv",
                     rows_(("2026-09-05", "TESCO", -4.0)), 1)
    uk = repo.transactions()
    uk = uk[uk["card"] == "Credit Card"].iloc[0]
    assert pd.isna(uk["amount_orig"]) and pd.isna(uk["currency"])


def test_gbp_rates_and_delete_card_rows(repo):
    from core import db
    db.execute("CREATE TABLE prices (fund_id TEXT, date TEXT, close REAL)")
    db.execute("INSERT INTO prices VALUES ('YF:GBPTRY=X', '2026-09-02', 68.1),"
               " ('YF:GBPTRY=X', '2026-09-01', 67.9), ('YF:GBPEUR=X', "
               "'2026-09-01', 1.17)")
    assert repo.gbp_rates("try").values.tolist() == [["2026-09-01", 67.9],
                                                     ["2026-09-02", 68.1]]
    repo.add_card("Turkey Card", "TRY")
    repo.import_rows("Turkey Card", "e.pdf", lira_rows(68.0), 1)
    assert repo.delete_card_rows("Turkey Card") == {"transactions": 1,
                                                    "imports": 1}
    assert "Turkey Card" in repo.card_names()


def test_ensure_tables_adds_the_currency_columns_to_an_older_database(
        tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE spend_cards (name TEXT PRIMARY KEY, sort_order INTEGER
            NOT NULL DEFAULT 99, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT);
        INSERT INTO spend_cards (name) VALUES ('Debit Card');
        CREATE TABLE spend_transactions (id INTEGER PRIMARY KEY, date TEXT,
            card TEXT, description TEXT, amount REAL, category TEXT,
            subcategory TEXT, status TEXT, import_id INTEGER,
            created_at TEXT, updated_at TEXT);
    """)
    c.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    from core.repo import spending as r
    r.ensure_tables()
    r.ensure_tables()                                  # twice is harmless
    assert r.card_currency("Debit Card") == "GBP"
    cols = {x[1] for x in sqlite3.connect(path).execute(
        "PRAGMA table_info(spend_transactions)")}
    assert {"currency", "amount_orig"} <= cols


def test_reset_turkey_card_script(tmp_path):
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    db_path = home / "data" / "funds.db"
    sys.path.insert(0, str(ROOT))
    from core.repo import spending as r
    c = sqlite3.connect(db_path)
    c.executescript(r.SCHEMA)
    c.executescript("""
        INSERT INTO spend_cards (name, sort_order, active) VALUES
            ('Debit Card', 0, 1), ('Turkey Card', 4, 0);
        INSERT INTO spend_subcategories VALUES ('Food & Drink', 'Restaurant'),
            ('Finance', 'Transfer');
        INSERT INTO spend_rules (keyword, category, subcategory, priority)
            VALUES ('UBER', 'Transport', 'Taxi', 43);
        INSERT INTO spend_transactions (date, card, description, amount)
            VALUES ('2025-11-21', 'Turkey Card', 'OLD LINE', -219.6),
                   ('2026-01-02', 'Debit Card', 'TESCO', -5.0);
        INSERT INTO spend_imports (card, filename) VALUES ('Turkey Card', 'x');
    """)
    c.commit()
    c.close()
    run = lambda *a: subprocess.run(                          # noqa: E731
        [sys.executable, str(ROOT / "scripts" / "reset_turkey_card.py"), *a],
        capture_output=True, text=True, env={"HOME": str(home), "PATH": ""})

    dry = run()
    assert dry.returncode == 0, dry.stdout + dry.stderr
    assert "nothing written" in dry.stdout
    t = sqlite3.connect(db_path)
    assert t.execute("SELECT COUNT(*) FROM spend_transactions").fetchone()[0] == 2
    assert t.execute("SELECT currency, active FROM spend_cards WHERE name = "
                     "'Turkey Card'").fetchone() == ("GBP", 0)
    assert t.execute("SELECT COUNT(*) FROM spend_rules").fetchone()[0] == 1
    t.close()

    done = run("--apply")
    assert done.returncode == 0, done.stdout + done.stderr
    t = sqlite3.connect(db_path)
    assert t.execute("SELECT card FROM spend_transactions").fetchall() == \
        [("Debit Card",)]
    assert t.execute("SELECT COUNT(*) FROM spend_imports").fetchone()[0] == 0
    assert t.execute("SELECT currency, active FROM spend_cards WHERE name = "
                     "'Turkey Card'").fetchone() == ("TRY", 1)
    rules = t.execute("SELECT keyword, priority FROM spend_rules "
                      "ORDER BY priority, id").fetchall()
    assert rules == [("UBERTRENDYOLGO", 42), ("UBER EATS/", 42),
                     ("UBER", 43), ("HESAPTAN ÖDEME", 44)]
    assert len(list((home / "data" / "backups").iterdir())) == 1
    t.close()
    assert run("--apply").returncode == 0            # again: nothing to do
    t = sqlite3.connect(db_path)
    assert t.execute("SELECT COUNT(*) FROM spend_rules").fetchone()[0] == 4


# --- pass 4: amount rules ------------------------------------------------------------

AMOUNT_RULES = [("PAYPAL PAYMENT DD", "Lifestyle", "Subscriptions", "exact",
                 12.99, "VPN"),
                ("ROYAL BOROUGH", "Transport", "Parking", "max", 20.0, None),
                ("ROYAL BOROUGH", "Tax & Fee", "Tax", None, None, None),
                ("PAYPAL", "Shopping", "Home", None, None, None)]


def test_rules_ignore_spacing():
    rules = [("UBER   *EATS", "Food & Drink", "Restaurant")]
    for d in ["UBER *EATS HELP.UBER.COM VIS",
              "UBER   *EATS           HELP.UBER.COM ENG"]:
        assert sp.classify(d, rules).subcategory == "Restaurant"


def test_an_amount_rule_can_place_a_manual_queue_line():
    m = sp.classify("PAYPAL PAYMENT DD", AMOUNT_RULES, -12.99)
    assert (m.category, m.rule, m.note) == ("Lifestyle",
                                            "PAYPAL PAYMENT DD = 12.99", "VPN")
    assert m.key == ("PAYPAL PAYMENT DD", "exact", 12.99)
    # Another amount: the keyword-only PAYPAL rule still may not take it.
    assert sp.classify("PAYPAL PAYMENT DD", AMOUNT_RULES, -10).rule == \
        sp.MANUAL


def test_up_to_rules_and_their_fallback():
    assert sp.classify("ROYAL BOROUGH OF GREENWICH", AMOUNT_RULES,
                       -4.4).subcategory == "Parking"
    assert sp.classify("ROYAL BOROUGH OF GREENWICH", AMOUNT_RULES,
                       -176).subcategory == "Tax"
    # Without an amount an amount rule cannot match.
    assert sp.classify("ROYAL BOROUGH", AMOUNT_RULES).subcategory == "Tax"


def test_remember_plan():
    plan, _ = sp.remember_plan([{"description": "KFC ELTHAM", "amount": -5}],
                               "kfc")
    assert plan == {"keyword": "KFC", "amount_kind": None, "amount": None}
    plan, _ = sp.remember_plan(
        [{"description": "PAYPAL PAYMENT DD", "amount": -12.99}] * 2,
        sp.remember_keyword("PAYPAL PAYMENT DD"))
    assert plan == {"keyword": "PAYPAL PAYMENT DD", "amount_kind": "exact",
                    "amount": 12.99}
    _, problem = sp.remember_plan(
        [{"description": "PAYPAL PAYMENT DD", "amount": -12.99},
         {"description": "PAYPAL PAYMENT DD", "amount": -6}], "PAYPAL")
    assert "one amount" in problem
    _, problem = sp.remember_plan(
        [{"description": "PAYPAL PAYMENT DD", "amount": -1},
         {"description": "PAYPAL *SHOP", "amount": -1}], "PAYPAL")
    assert "apart" in problem
    assert sp.remember_keyword("CASH RB SCOT SEP17 @18:11 ATM") == "CASH"


# --- pass 4: splits ---------------------------------------------------------------

def test_part_amounts_and_resolving_a_template():
    assert sp.parse_part_amount("120") == ("fixed", 120.0)
    assert sp.parse_part_amount(" 40% ") == ("percent", 40.0)
    assert sp.parse_part_amount("") == ("rest", None)
    with pytest.raises(ValueError):
        sp.parse_part_amount("abc")
    tpl = [{"kind": "fixed", "value": 120, "category": "Household",
            "subcategory": "Cleaning"},
           {"kind": "rest", "value": None, "category": "Education",
            "subcategory": "Tutor"}]
    assert [p["amount"] for p in sp.resolve_parts(-200, tpl)] == [-120, -80]
    assert [p["amount"] for p in sp.resolve_parts(-190, tpl)] == [-120, -70]
    with pytest.raises(ValueError, match="already take"):
        sp.resolve_parts(-100, tpl)
    pct = [{"kind": "percent", "value": 25}, {"kind": "rest"}]
    assert [p["amount"] for p in sp.resolve_parts(-99.99, pct)] == \
        [-25.0, -74.99]
    with pytest.raises(ValueError, match="add up"):
        sp.resolve_parts(-50, [{"kind": "fixed", "value": 20},
                               {"kind": "fixed", "value": 20}])


def test_check_split():
    good = [{"amount": -120, "category": "A", "subcategory": "a"},
            {"amount": -80, "category": "B", "subcategory": "b"}]
    assert sp.check_split(-200, good) is None
    assert "two parts" in sp.check_split(-200, good[:1])
    assert "add up" in sp.check_split(-210, good)
    assert "wrong sign" in sp.check_split(
        -200, [good[0], {**good[1], "amount": 80}])
    assert "needs a category" in sp.check_split(
        -200, [good[0], {**good[1], "category": None}])


def test_expand_splits_replaces_a_line_by_its_parts():
    txns = pd.DataFrame({"id": [1, 2], "date": ["2026-09-01"] * 2,
                         "description": ["CASH", "TESCO"],
                         "amount": [-200.0, -5.0],
                         "category": [None, "Groceries"],
                         "subcategory": [None, "Tesco"],
                         "note": [None, None], "status": ["confirmed"] * 2})
    parts = pd.DataFrame({"transaction_id": [1, 1], "part": [1, 2],
                          "amount": [-120.0, -80.0],
                          "category": ["Household", "Education"],
                          "subcategory": ["Cleaning", "Tutor"],
                          "note": [None, "maths"]})
    out = sp.expand_splits(txns, parts)
    assert sorted(out["amount"]) == [-120.0, -80.0, -5.0]
    assert out["amount"].sum() == txns["amount"].sum()
    assert set(out.loc[out["split_of"] == 1, "subcategory"]) == \
        {"Cleaning", "Tutor"}


# --- pass 4: suggestions ---------------------------------------------------------

def test_suggestions_from_history():
    hist = pd.DataFrame({
        "date": ["2025-01-05", "2025-06-05", "2025-03-01", "2026-01-01"],
        "description": ["PAYPAL PAYMENT DD", "PAYPAL PAYMENT DD",
                        "CASH RB SCOT X ATM", "CASH RB SCOT Y ATM"],
        "amount": [-12.99, -12.99, -200.0, -200.0],
        "category": ["Lifestyle", "Lifestyle", "Household", None],
        "subcategory": ["Subscriptions", "Subscriptions", "Cleaning", None],
        "note": ["VPN", "VPN", None, None], "template": [None, None, None, 3]})
    idx = sp.history_index(hist)
    s = sp.suggestions("PAYPAL PAYMENT DD", -12.99, idx)
    assert s[0]["category"] == "Lifestyle" and s[0]["count"] == 2 \
        and s[0]["note"] == "VPN" and s[0]["last"] == "2025-06-05"
    assert sp.suggestions("PAYPAL PAYMENT DD", -6, idx) == []
    # Cash: by amount alone; the newest first among equals.
    cash = sp.suggestions("CASH RB SCOT Z ATM", -200, idx)
    assert [c["template"] for c in cash] == [3, None]
    assert sp.top_categories(hist, "PayPal") == [("Lifestyle", "Subscriptions")]


# --- pass 4: statements ---------------------------------------------------------

def lines(rows):
    return pd.DataFrame(rows, columns=["date", "description", "amount"])


def stored(rows):
    return pd.DataFrame(rows, columns=["id", "date", "description", "amount"])


def test_match_statement_passes():
    st_lines = lines([("2026-09-14", "PUB VIS", -6.54),
                      ("2026-09-14", "PUB VIS", -6.54),
                      ("2026-09-23", "AIL DD", -34.82),
                      ("2026-10-01", "ROYAL GREENWICH DD", -176.0),
                      ("2026-10-05", "PAYPAL PAYMENT DD", -12.99),
                      ("2026-10-06", "TESCO STORES 6500 ELTHAM GBR", -4.58),
                      ("2026-10-07", "COUNCIL DD", -50.0),
                      ("2026-10-08", "TESCO STORES 6500 ELTHAM GBR", -9.0),
                      ("2026-10-09", "NEW SHOP VIS", -9.0)])
    db_rows = stored([(1, "2026-09-14", "PUB VIS", -6.54),
                      (2, "2026-09-14", "PUB  VIS", -6.54),
                      (3, "2026-09-22", "AIL DD", -34.8),
                      (4, "2026-10-01", "ROYAL GREENWICH DD - tax", -176.0),
                      (5, "2026-10-04", "PAYPAL PAYMENT DD - VPN", -12.99),
                      (6, "2026-10-06", "))) TESCO STORES 6500 ELTHAM 4.60",
                       -4.6),
                      (7, "2026-10-07", "BOROUGH COUNCIL TAX", -50.0),
                      (8, "2026-10-09", "TESCO EXPRESS 22", -9.04),
                      (9, "2026-09-30", "PHANTOM VIS", -1.0),
                      (10, "2026-10-20", "AFTER VIS", -2.0)])
    res = sp.match_statement(st_lines, db_rows, ("2026-09-12", "2026-10-11"))
    kinds = {sid: kind for _, sid, kind in res["pairs"]}
    assert kinds == {1: "exact", 2: "exact", 3: "shop", 4: "shop",
                     5: "shop", 6: "shop", 7: "reworded", 8: "near"}
    assert res["new"] == [8]
    assert res["leftover"] == [9]            # 10 is outside the window


def test_rounded_history_pairs_and_its_duplicates_go():
    # 8052 kept amounts to 10p; an export of the same weeks is also stored.
    st_lines = lines([("2025-12-22", "LIDL GB  ELTHAM        0827 LIDL GB  GBR",
                       -174.22),
                      ("2025-12-29", "UBER   *TRIP           HELP.UBER.COM ENG",
                       -9.62)])
    db_rows = stored([(1, "2025-12-22", "LIDL GB ELTHAM 0827 LIDL GB GBR",
                       -174.2),
                      (2, "2025-12-22", "LIDL GB  ELTHAM        0827 LIDL GB  "
                       "GBR", -174.22),
                      (3, "2025-12-29", "UBER *TRIP HELP.UBER.COM ENG 9.60",
                       -9.6)])
    res = sp.match_statement(st_lines, db_rows, ("2025-12-08", "2026-01-07"))
    assert res["pairs"] == [(0, 2, "exact"), (1, 3, "shop")]
    assert res["leftover"] == [1]
    # A refund the history stored as spending pairs, and takes its sign.
    flip = sp.match_statement(lines([("2025-12-22", "ASDA REFUND VIS", 8.0)]),
                              stored([(1, "2025-12-22", "ASDA REFUND VIS",
                                       -8.0)]), ("2025-12-01", "2025-12-31"))
    assert flip["pairs"] == [(0, 1, "sign")]
    # Same sign is preferred when both are there.
    both = sp.match_statement(lines([("2025-12-22", "ASDA", 8.0)]),
                              stored([(1, "2025-12-22", "ASDA", -8.0),
                                      (2, "2025-12-22", "ASDA", 8.0)]),
                              ("2025-12-01", "2025-12-31"))
    assert both["pairs"] == [(0, 2, "exact")] and both["leftover"] == [1]
    # Not beyond 5p.
    assert sp.match_statement(lines([("2025-12-22", "LIDL", -4.6)]),
                              stored([(1, "2025-12-22", "LIDL", -4.7)]),
                              ("2025-12-01", "2025-12-31"))["new"] == [0]


def test_shop_text():
    assert sp.core_text("))) TESCO STORES 6500 ELTHAM 4.60") == \
        "TESCO STORES 6500 ELTHAM"
    assert sp.same_shop("Amazon.co.uk*ZG7073G84",
                        "Amazon.co.uk*ZG7073G84 LONDON        GBR")
    assert not sp.same_shop("TESCO EXPRESS", "TESCO STORES")
    assert not sp.extra_wording("))) TESCO STORES 6500 ELTHAM 4.60",
                                "TESCO STORES 6500      ELTHAM        GBR")
    assert sp.extra_wording("PAYPAL PAYMENT DD - VPN", "PAYPAL PAYMENT DD")
    # The export drops commas.
    assert not sp.extra_wording("WILLOW WALK VICTORIA  LON )))",
                                "WILLOW WALK VICTORIA, LON )))")


def test_match_statements_pairs_exact_across_statements_first():
    # The stored row is the second statement's word for word; the first
    # statement has a looser candidate for it. Exact wins.
    first = lines([("2026-09-10", "PUB VIS", -5.0)])
    second = lines([("2026-09-12", "PUB VIS", -5.0)])
    db_rows = stored([(1, "2026-09-12", "PUB VIS", -5.0),
                      (2, "2026-09-05", "OLD VIS", -1.0)])
    out = sp.match_statements([(first, ("2026-08-12", "2026-09-11")),
                               (second, ("2026-09-12", "2026-10-11"))],
                              db_rows)
    assert out[1]["pairs"] == [(0, 1, "exact")]
    assert out[0]["pairs"] == [] and out[0]["new"] == [0]
    assert out[0]["leftover"] == [2] and out[1]["leftover"] == []


def test_statement_checks_and_holes():
    a = {"period_from": "2026-08-12", "period_to": "2026-09-11",
         "previous_balance": 100.0, "new_balance": 90.0, "debits": 10.0,
         "credits": 0.0}
    b = {"period_from": "2026-09-12", "period_to": "2026-10-11",
         "previous_balance": 90.0, "new_balance": 95.0, "debits": 5.0,
         "credits": 10.0}
    linked = pd.DataFrame({"amount": [-5.0, 10.0]})
    chk = sp.statement_checks(b, linked, a)
    assert chk["ok"] and chk["lines_ok"] and chk["chain_ok"]
    assert not sp.statement_checks(b, linked.iloc[:1], a)["ok"]
    assert sp.statement_checks({**b, "previous_balance": 91.0}, linked,
                               a)["chain_ok"] is False
    assert not sp.statement_checks(b, linked, a, unlinked=1)["ok"]
    c = {**b, "period_from": "2026-11-12", "period_to": "2026-12-11"}
    sts = pd.DataFrame([{**a, "card": "D"}, {**c, "card": "D"}])
    assert sp.missing_periods(sts) == [{"card": "D", "from": "2026-09-12",
                                        "to": "2026-11-11"}]
    assert sp.statement_checks(c, linked, a)["chain_ok"] is None


def test_coverage_grid():
    sts = pd.DataFrame([{"card": "D", "id": 1, "period_to": "2026-09-11",
                         "ok": True},
                        {"card": "D", "id": 2, "period_to": "2026-12-11",
                         "ok": False}])
    missing = [{"card": "D", "from": "2026-09-12", "to": "2026-11-11"}]
    unl = pd.DataFrame([{"card": "D", "month": "2027-01", "rows": 4},
                        {"card": "C", "month": "2026-10", "rows": 1}])
    grid = sp.coverage(sp.month_span("2026-09", "2027-01"), sts, missing, unl)
    assert grid["D"] == {"2026-09": ("ok", 1), "2026-10": ("missing", None),
                         "2026-11": ("missing", None),
                         "2026-12": ("break", 2),
                         "2027-01": ("provisional", None)}
    assert grid["C"] == {"2026-10": ("provisional", None)}


# --- pass 4: repo ---------------------------------------------------------------

def test_old_rules_table_loses_its_keyword_unique(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE spend_rules (id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL UNIQUE, category TEXT NOT NULL,
            subcategory TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 100,
            created_at TEXT);
        INSERT INTO spend_rules (id, keyword, category, subcategory, priority)
        VALUES (7, 'PAYPAL PAYMENT DD', 'Shopping', 'Home', 5);
    """)
    c.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    from core.repo import spending as r
    r.ensure_tables()
    assert r.rules()["id"].tolist() == [7]
    assert r.save_rule("paypal payment dd", "Lifestyle", "Subscriptions",
                       amount_kind="exact", amount=-12.99,
                       note="VPN") == "PAYPAL PAYMENT DD = 12.99"
    assert r.save_rule("PAYPAL PAYMENT DD", "Lifestyle", "Fitness",
                       amount_kind="exact", amount=12.99) == \
        "PAYPAL PAYMENT DD = 12.99"                     # same rule, updated
    assert len(r.rules()) == 2
    assert r.rule_tuples()[-1] == ("PAYPAL PAYMENT DD", "Lifestyle",
                                   "Fitness", "exact", 12.99, None)
    new_id = int(r.rules()["id"].max())
    assert "already uses" in r.update_rule(new_id, "PAYPAL PAYMENT DD",
                                           "X", "y", None)


def test_apply_statements_links_rewords_adds_and_deletes(repo):
    repo.add_card("Debit Card")
    keep = rows_(("2026-09-14", "PUB VIS", -6.5))
    old = rows_(("2026-10-01", "COUNCIL DD - tax", -176.0))
    junk = rows_(("2026-09-30", "PHANTOM VIS", -9.0))
    for r in (keep, old, junk):
        repo.import_rows("Debit Card", "x.csv", r, 1)
    ids = dict(zip(repo.transactions()["description"],
                   repo.transactions()["id"]))
    record = {"bank": "HSBC UK current", "account_ref": "HSBC UK current:5678",
              "currency": "GBP", "period_from": "2026-09-12",
              "period_to": "2026-10-11", "statement_date": "2026-10-11",
              "previous_balance": 1000.0, "new_balance": 803.5,
              "debits": 196.5, "credits": 0.0, "lines": 3,
              "filename": "s.pdf"}
    res = repo.apply_statements("Debit Card", "s.pdf", [{
        "record": record, "ref": "HSBC UK current:5678",
        "link": [{"id": ids["PUB VIS"], "date": "2026-09-14",
                  "description": "PUB VIS", "old_description": "PUB VIS"},
                 {"id": ids["COUNCIL DD - tax"], "date": "2026-10-01",
                  "description": "COUNCIL DD",
                  "old_description": "COUNCIL DD - tax"}],
        "insert": [{"date": "2026-10-05", "description": "NEW VIS",
                    "amount": -14.0, "category": None, "subcategory": None,
                    "status": "pending", "note": None}],
        "delete": [ids["PHANTOM VIS"]]}], 3)
    assert (res["linked"], res["reworded"], res["inserted"],
            res["deleted"]) == (2, 1, 1, 1)
    t = repo.transactions().set_index("description")
    assert t.loc["COUNCIL DD", "note"] == "COUNCIL DD - tax"
    assert t["statement_id"].notna().all() and "PHANTOM VIS" not in t.index
    assert repo.card_for_ref("HSBC UK current:5678") == "Debit Card"
    assert res["amounts"] == 0
    over = repo.statement_overview().iloc[0]
    assert over["ok"] and over["lines_stored"] == 3
    # The same statement again changes nothing and stays one record.
    again = repo.match_candidates("Debit Card", "2026-09-01", "2026-10-31",
                                  [int(over["id"])])
    assert len(again) == 3 and len(repo.statements()) == 1
    # A CSV row on no statement inside the period breaks it.
    repo.import_rows("Debit Card", "y.csv",
                     rows_(("2026-09-20", "STRAY VIS", -1.0)), 1)
    assert not repo.statement_overview().iloc[0]["ok"]
    assert repo.unlinked_months()["month"].tolist() == ["2026-09"]


def test_splits_templates_and_history(repo):
    repo.add_category("Household")
    repo.add_subcategory("Household", "Cleaning")
    repo.import_rows("Credit Card", "a.csv",
                     rows_(("2026-09-01", "CASH ATM", -200.0),
                           status="pending"), 1)
    tid = int(repo.transactions()["id"].iloc[0])
    assert repo.save_template("Cash", [
        {"category": "Household", "subcategory": "Cleaning", "kind": "fixed",
         "value": 120},
        {"category": "Groceries", "subcategory": "Tesco", "kind": "rest",
         "value": None}]) is None
    tpl = repo.templates().iloc[0]
    parts = sp.resolve_parts(-200, tpl["parts"])
    assert repo.save_split(tid, parts, int(tpl["id"])) is None
    line = repo.transactions().iloc[0]
    assert line["status"] == "confirmed" and line["category"] is None
    year = repo.year_rows("2026", expand=True)
    assert sorted(year["amount"]) == [-120.0, -80.0]
    assert repo.templates().iloc[0]["uses"] == 1
    assert repo.history()["template"].tolist() == [int(tpl["id"])]
    assert repo.remap("Household", "Cleaning", "Groceries", "Tesco") == 1
    assert set(repo.splits_for([tid])["category"]) == {"Groceries"}
    assert "add up" in repo.save_split(tid, parts[:1] + parts[:1])
    repo.assign([tid], "Groceries", "Tesco", note="weekly")
    assert repo.splits_for([tid]).empty
    assert repo.transactions().iloc[0]["note"] == "weekly"
    repo.save_split(tid, parts, None)
    repo.remove_split(tid)
    assert repo.pending()["id"].tolist() == [tid]
    repo.delete_template(int(tpl["id"]))
    assert repo.templates().empty


def test_restore_plan_fills_pending_lines_once():
    sys.path.insert(0, str(ROOT / "scripts"))
    import spending_manual_backup as smb
    backup = pd.DataFrame([
        {"id": 1, "card": "C", "date": "2026-01-07", "description": "AMAZON A",
         "amount": -77.4, "category": "Shopping", "subcategory": "Home",
         "note": "", "splits": ""},
        {"id": 2, "card": "C", "date": "2026-01-08", "description": "KEEP",
         "amount": -5.0, "category": "X", "subcategory": "y", "note": "",
         "splits": ""}])
    current = pd.DataFrame({"id": [2, 10, 11]})
    pending = pd.DataFrame([
        {"id": 10, "card": "C", "date": "2026-01-08", "description": "AMAZON B",
         "amount": -77.4},
        {"id": 11, "card": "C", "date": "2026-01-07", "description": "AMAZON A",
         "amount": -77.4}])
    pairs = smb.plan_restore(backup, current, pending)
    assert [(b["id"], p["id"]) for b, p in pairs] == [(1, 11)]
    # History kept 10p: a pending -77.38 still takes the -77.40 line.
    pending.loc[1, "amount"] = -77.38
    pairs = smb.plan_restore(backup, current, pending)
    assert [(b["id"], p["id"]) for b, p in pairs] == [(1, 11)]


def test_a_linked_line_takes_the_statements_exact_amount(repo):
    repo.add_category("Household")
    repo.add_subcategory("Household", "Cleaning")
    repo.import_rows("Credit Card", "h.csv",
                     rows_(("2025-12-22", "LIDL", -174.2),
                           ("2025-12-23", "CASH ATM", -200.0)), 2)
    t = repo.transactions().set_index("description")["id"]
    repo.save_split(int(t["CASH ATM"]), [
        {"amount": -120.0, "category": "Household", "subcategory": "Cleaning"},
        {"amount": -80.0, "category": "Groceries", "subcategory": "Tesco"}])
    record = {"bank": "HSBC UK", "currency": "GBP",
              "period_from": "2025-12-08", "period_to": "2026-01-07",
              "debits": 374.24, "credits": 0.0}
    res = repo.apply_statements("Credit Card", "s.pdf", [{
        "record": record, "ref": None, "insert": [], "delete": [],
        "link": [{"id": int(t["LIDL"]), "date": "2025-12-22",
                  "description": "LIDL", "amount": -174.22},
                 {"id": int(t["CASH ATM"]), "date": "2025-12-23",
                  "description": "CASH ATM", "amount": -200.02}]}], 2)
    assert res["amounts"] == 2
    got = repo.transactions().set_index("description")["amount"]
    assert got["LIDL"] == -174.22 and got["CASH ATM"] == -200.02
    parts = repo.splits_for([int(t["CASH ATM"])])["amount"].tolist()
    assert parts == [-120.0, -80.02]
    assert repo.statement_overview().iloc[0]["ok"]
