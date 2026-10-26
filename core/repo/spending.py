"""
Spending tables. All the SQL for the Spending section lives here.

    spend_cards          cards you import from. active = 0 hides a card from
                         the Import dropdown without touching its history.
                         currency is what its statements are in (GBP, TRY).
    spend_categories     categories, in display order
    spend_subcategories  sub-categories per category
    spend_rules          keyword rules: a keyword found anywhere in a
                         description sets the category and sub-category.
                         Checked in priority order, lowest first; equal
                         priorities in the order they were added. An
                         optional amount condition (amount_kind 'exact' or
                         'max', amount in pounds, unsigned) narrows a rule
                         and lets it place Amazon / PayPal / cash lines;
                         note is copied onto the lines it places. Unique on
                         keyword + condition.
    spend_imports        one row per uploaded file
    spend_transactions   one row per bank line. amount is signed as in the
                         bank file (spending negative) and always in pounds.
                         For a card in another currency, amount_orig and
                         currency keep what the statement said (lira), and
                         amount is converted at that day's rate on import.
                         date is the posting date where the bank gives one.
                         status is 'confirmed' or 'pending' - pending rows
                         are the Manual Queue. statement_id links the line
                         to the statement it is on (NULL: a CSV row waiting
                         for its statement). note is free text - yours, or
                         the old wording of a line a statement re-worded.
    spend_statements     one row per statement: card, period, opening and
                         closing balance, debits (paid out) and credits
                         (paid in) as printed - lira for the lira card.
    spend_card_accounts  '<bank>:<last 4 digits>' -> card, learnt the first
                         time a statement of that account is imported. Only
                         the last four digits are kept.
    spend_splits         the parts of a split line: amount (signed, pounds),
                         category, sub-category, note, and the template that
                         made it. A split line itself has no category.
    spend_split_templates  named splits, parts as JSON: category,
                         subcategory, note, kind (fixed / percent / rest),
                         value.

Tables are created on first use. scripts/migrate_spending.py fills them from
the old 8052 database once.
"""

from __future__ import annotations

import json
import re
from datetime import datetime

import pandas as pd

from core import db, spending as sp

SCHEMA = """
CREATE TABLE IF NOT EXISTS spend_cards (
    name        TEXT PRIMARY KEY,
    sort_order  INTEGER NOT NULL DEFAULT 99,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT,
    currency    TEXT NOT NULL DEFAULT 'GBP'
);
CREATE TABLE IF NOT EXISTS spend_categories (
    name        TEXT PRIMARY KEY,
    sort_order  INTEGER NOT NULL DEFAULT 99
);
CREATE TABLE IF NOT EXISTS spend_subcategories (
    category    TEXT NOT NULL,
    name        TEXT NOT NULL,
    PRIMARY KEY (category, name)
);
CREATE TABLE IF NOT EXISTS spend_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword     TEXT NOT NULL,
    category    TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT,
    amount_kind TEXT,
    amount      REAL,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS spend_imports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    card         TEXT NOT NULL,
    filename     TEXT,
    imported_at  TEXT,
    rows_in_file INTEGER,
    inserted     INTEGER,
    skipped      INTEGER,
    first_date   TEXT,
    last_date    TEXT
);
CREATE TABLE IF NOT EXISTS spend_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,
    card        TEXT NOT NULL,
    description TEXT NOT NULL,
    amount      REAL NOT NULL,
    category    TEXT,
    subcategory TEXT,
    status      TEXT NOT NULL DEFAULT 'confirmed',
    import_id   INTEGER,
    created_at  TEXT,
    updated_at  TEXT,
    currency    TEXT,
    amount_orig REAL,
    statement_id INTEGER,
    note        TEXT
);
CREATE TABLE IF NOT EXISTS spend_statements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card            TEXT NOT NULL,
    bank            TEXT,
    account_ref     TEXT,
    currency        TEXT NOT NULL DEFAULT 'GBP',
    period_from     TEXT NOT NULL,
    period_to       TEXT NOT NULL,
    statement_date  TEXT,
    previous_balance REAL,
    new_balance     REAL,
    debits          REAL,
    credits         REAL,
    lines           INTEGER,
    filename        TEXT,
    imported_at     TEXT,
    UNIQUE (card, period_to)
);
CREATE TABLE IF NOT EXISTS spend_card_accounts (
    ref         TEXT PRIMARY KEY,
    card        TEXT NOT NULL,
    created_at  TEXT
);
CREATE TABLE IF NOT EXISTS spend_splits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id  INTEGER NOT NULL,
    part            INTEGER NOT NULL,
    amount          REAL NOT NULL,
    category        TEXT NOT NULL,
    subcategory     TEXT NOT NULL,
    note            TEXT,
    template_id     INTEGER
);
CREATE TABLE IF NOT EXISTS spend_split_templates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    parts       TEXT NOT NULL,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_spend_txn_date   ON spend_transactions(date);
CREATE INDEX IF NOT EXISTS idx_spend_txn_card   ON spend_transactions(card, date);
CREATE INDEX IF NOT EXISTS idx_spend_txn_status ON spend_transactions(status);
CREATE INDEX IF NOT EXISTS idx_spend_splits_txn ON spend_splits(transaction_id);
"""

# Indexes on columns an older database only gains in ensure_tables().
LATE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_spend_txn_stmt ON spend_transactions(statement_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_spend_rules
    ON spend_rules(keyword, IFNULL(amount_kind, ''), IFNULL(amount, -1));
"""

TXN_COLS = ("id, date, card, description, amount, category, subcategory, "
            "status, import_id, currency, amount_orig, statement_id, note")

# Columns added after the first release, created on existing databases.
LATER_COLUMNS = {
    "spend_cards": [("currency", "TEXT NOT NULL DEFAULT 'GBP'")],
    "spend_transactions": [("currency", "TEXT"), ("amount_orig", "REAL"),
                           ("statement_id", "INTEGER"), ("note", "TEXT")],
    "spend_rules": [("amount_kind", "TEXT"), ("amount", "REAL"),
                    ("note", "TEXT")],
}

# Databases already brought up to date in this process (by path).
_READY: set = set()


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables() -> None:
    """Create the tables, and bring an older database up to date: add the
    columns later releases need, and drop the one-rule-per-keyword
    constraint (a keyword can now have rules for different amounts). Done
    once per database per process."""
    key = str(db.config.DB_PATH)
    if key in _READY:
        return
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)
        for table, cols in LATER_COLUMNS.items():
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        conn.commit()
        _drop_keyword_unique(conn)
        conn.executescript(LATE_INDEXES)
    _READY.add(key)


def _drop_keyword_unique(conn) -> None:
    """spend_rules was created with keyword UNIQUE. SQLite cannot drop a
    column constraint, so the table is rebuilt once - ids, and so the order
    of equal priorities, kept."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' "
                       "AND name = 'spend_rules'").fetchone()
    if not sql or not re.search(r"keyword\s+TEXT\s+NOT\s+NULL\s+UNIQUE",
                                sql[0], re.I):
        return
    cols = "id, keyword, category, subcategory, priority, created_at, " \
           "amount_kind, amount, note"
    conn.execute("BEGIN")
    conn.execute("ALTER TABLE spend_rules RENAME TO spend_rules_old")
    conn.execute("""
        CREATE TABLE spend_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword     TEXT NOT NULL,
            category    TEXT NOT NULL,
            subcategory TEXT NOT NULL,
            priority    INTEGER NOT NULL DEFAULT 100,
            created_at  TEXT,
            amount_kind TEXT,
            amount      REAL,
            note        TEXT)""")
    conn.execute(f"INSERT INTO spend_rules ({cols}) "
                 f"SELECT {cols} FROM spend_rules_old")
    conn.execute("DROP TABLE spend_rules_old")
    conn.commit()


def _placeholders(items) -> str:
    return ",".join("?" for _ in items)


# --- Cards -------------------------------------------------------------------

def cards(active_only: bool = False) -> pd.DataFrame:
    ensure_tables()
    sql = "SELECT name, sort_order, active, currency FROM spend_cards"
    if active_only:
        sql += " WHERE active = 1"
    return db.query(sql + " ORDER BY sort_order, name")


def card_names(active_only: bool = True) -> list[str]:
    return cards(active_only)["name"].tolist()


def card_currency(name: str) -> str:
    df = cards()
    hit = df.loc[df["name"] == name, "currency"]
    return hit.iloc[0] if len(hit) and hit.iloc[0] else "GBP"


def cards_in(currency: str, active_only: bool = True) -> list[str]:
    df = cards(active_only)
    return df.loc[df["currency"] == currency, "name"].tolist()


def set_card_currency(name: str, currency: str) -> None:
    db.execute("UPDATE spend_cards SET currency = ? WHERE name = ?",
               (currency.upper(), name))


def card_summary() -> pd.DataFrame:
    """Every card with its row count and first / last posting date."""
    ensure_tables()
    return db.query("""
        SELECT c.name, c.active, c.currency,
               COUNT(t.id) AS rows, MIN(t.date) AS first_date,
               MAX(t.date) AS last_date
        FROM spend_cards c LEFT JOIN spend_transactions t ON t.card = c.name
        GROUP BY c.name ORDER BY c.sort_order, c.name
    """)


def add_card(name: str, currency: str = "GBP") -> str | None:
    """Returns an error message, or None when added."""
    name = (name or "").strip()
    if not name:
        return "Type a card name first."
    ensure_tables()
    if name.lower() in {n.lower() for n in card_names(active_only=False)}:
        return f"{name} already exists."
    db.execute("""
        INSERT INTO spend_cards (name, sort_order, active, created_at, currency)
        VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM spend_cards),
                1, ?, ?)
    """, (name, _now(), (currency or "GBP").upper()))
    return None


def rename_card(old: str, new: str) -> str | None:
    """Renames the card and every transaction and import booked to it."""
    new = (new or "").strip()
    if not new:
        return "Type the new name."
    if new != old and new.lower() in {n.lower()
                                      for n in card_names(active_only=False)}:
        return f"{new} already exists."
    with db.get_conn() as conn:
        conn.execute("UPDATE spend_cards SET name = ? WHERE name = ?",
                     (new, old))
        conn.execute("UPDATE spend_transactions SET card = ? WHERE card = ?",
                     (new, old))
        conn.execute("UPDATE spend_imports SET card = ? WHERE card = ?",
                     (new, old))
        conn.execute("UPDATE spend_statements SET card = ? WHERE card = ?",
                     (new, old))
        conn.execute("UPDATE spend_card_accounts SET card = ? WHERE card = ?",
                     (new, old))
    return None


def set_card_active(name: str, active: bool) -> None:
    db.execute("UPDATE spend_cards SET active = ? WHERE name = ?",
               (1 if active else 0, name))


# --- Categories --------------------------------------------------------------

def categories() -> dict[str, list[str]]:
    """{category: [sub-categories]} in display order."""
    ensure_tables()
    cats = db.query("SELECT name FROM spend_categories "
                    "ORDER BY sort_order, name")["name"].tolist()
    subs = db.query("SELECT category, name FROM spend_subcategories "
                    "ORDER BY name")
    out = {c: [] for c in cats}
    for r in subs.itertuples(index=False):
        if r.category in out:
            out[r.category].append(r.name)
    return out


def add_category(name: str) -> None:
    name = (name or "").strip()
    if not name:
        return
    ensure_tables()
    db.execute("""
        INSERT OR IGNORE INTO spend_categories (name, sort_order)
        VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1
                    FROM spend_categories))
    """, (name,))


def delete_category(name: str) -> None:
    """Removes it from the lists. Transactions keep their values - use
    remap() to move them first."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM spend_categories WHERE name = ?", (name,))
        conn.execute("DELETE FROM spend_subcategories WHERE category = ?",
                     (name,))


def add_subcategory(category: str, name: str) -> None:
    name = (name or "").strip()
    if not (category and name):
        return
    ensure_tables()
    db.execute("INSERT OR IGNORE INTO spend_subcategories (category, name) "
               "VALUES (?, ?)", (category, name))


def delete_subcategory(category: str, name: str) -> None:
    db.execute("DELETE FROM spend_subcategories WHERE category = ? "
               "AND name = ?", (category, name))


def remap(old_cat: str, old_sub: str | None, new_cat: str,
          new_sub: str) -> int:
    """Move transactions from one category / sub-category to another.
    old_sub None means every sub-category of old_cat. Split parts move
    too; the count is of lines and parts together."""
    where = "category = ? AND subcategory = ?" if old_sub else "category = ?"
    params = (old_cat, old_sub) if old_sub else (old_cat,)
    with db.get_conn() as conn:
        n = conn.execute(f"UPDATE spend_transactions SET category = ?, "
                         f"subcategory = ?, updated_at = ? WHERE {where}",
                         (new_cat, new_sub, _now(), *params)).rowcount
        n += conn.execute(f"UPDATE spend_splits SET category = ?, "
                          f"subcategory = ? WHERE {where}",
                          (new_cat, new_sub, *params)).rowcount
    return n


def categories_in_use() -> list[str]:
    ensure_tables()
    return db.query("""
        SELECT category FROM spend_transactions WHERE category IS NOT NULL
        UNION SELECT category FROM spend_splits
        ORDER BY category
    """)["category"].tolist()


def subcategories_in_use(category: str) -> list[str]:
    """Sub-categories stored transactions carry under a category - including
    ones missing from the lists (8052 let any sub-category go with any
    category), so Re-map can reach them."""
    ensure_tables()
    return db.query("""
        SELECT subcategory FROM spend_transactions
        WHERE category = ? AND subcategory IS NOT NULL AND subcategory <> ''
        UNION SELECT subcategory FROM spend_splits WHERE category = ?
        ORDER BY subcategory
    """, (category, category))["subcategory"].tolist()


def used_subcategories() -> list[str]:
    ensure_tables()
    return db.query("""
        SELECT subcategory FROM spend_transactions
        WHERE subcategory IS NOT NULL AND subcategory <> ''
        UNION SELECT subcategory FROM spend_splits
        ORDER BY subcategory
    """)["subcategory"].tolist()


# --- Rules --------------------------------------------------------------------

def rules() -> pd.DataFrame:
    ensure_tables()
    return db.query("SELECT id, keyword, category, subcategory, priority, "
                    "amount_kind, amount, note "
                    "FROM spend_rules ORDER BY priority, id")


def rule_tuples() -> list[tuple]:
    """(keyword, category, subcategory, amount_kind, amount, note) in the
    order they are checked - what core.spending.classify takes."""
    return [(r.keyword, r.category, r.subcategory,
             r.amount_kind if isinstance(r.amount_kind, str) else None,
             None if pd.isna(r.amount) else float(r.amount),
             r.note if isinstance(r.note, str) and r.note else None)
            for r in rules().itertuples(index=False)]


def _condition(amount_kind, amount):
    """Normalised amount condition: (kind, unsigned pounds) or (None, None)."""
    if amount_kind not in ("exact", "max") or amount in (None, ""):
        return None, None
    return amount_kind, round(abs(float(amount)), 2)


def _same_rule(conn, kw, kind, amount, exclude_id=None):
    sql = ("SELECT id, priority FROM spend_rules WHERE keyword = ? "
           "AND IFNULL(amount_kind, '') = ? AND IFNULL(amount, -1) = ?")
    params = [kw, kind or "", -1 if amount is None else amount]
    if exclude_id is not None:
        sql += " AND id <> ?"
        params.append(int(exclude_id))
    return conn.execute(sql, params).fetchone()


def save_rule(keyword: str, category: str, subcategory: str,
              priority: int | None = None, amount_kind: str | None = None,
              amount: float | None = None, note: str | None = None) -> str:
    """Add a rule, or update the one with this keyword and amount
    condition. A new rule with no priority goes to the end of the list, so
    it cannot change how anything already matched is classified. Returns
    the rule's label ('TESCO', 'PAYPAL PAYMENT DD = 12.99')."""
    kw = (keyword or "").upper()
    if not kw.strip():
        raise ValueError("empty keyword")
    kind, amount = _condition(amount_kind, amount)
    note = (note or "").strip() or None
    ensure_tables()
    with db.get_conn() as conn:
        row = _same_rule(conn, kw, kind, amount)
        if priority is None:
            priority = row[1] if row else conn.execute(
                "SELECT COALESCE(MAX(priority), 0) + 1 FROM spend_rules"
            ).fetchone()[0]
        if row:
            conn.execute("UPDATE spend_rules SET category = ?, subcategory = ?, "
                         "priority = ?, note = ? WHERE id = ?",
                         (category, subcategory, int(priority), note, row[0]))
        else:
            conn.execute("""
                INSERT INTO spend_rules (keyword, category, subcategory,
                    priority, created_at, amount_kind, amount, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (kw, category, subcategory, int(priority), _now(), kind,
                  amount, note))
    return sp.rule_label(kw, kind, amount)


def update_rule(rule_id: int, keyword: str, category: str, subcategory: str,
                priority: int | None, amount_kind: str | None = None,
                amount: float | None = None,
                note: str | None = None) -> str | None:
    """Edit a rule in place, keyword and amount condition included. Returns
    an error message, or None when saved. A blank priority keeps the rule's
    current one."""
    kw = (keyword or "").upper()
    if not kw.strip():
        return "Type a keyword."
    kind, amount = _condition(amount_kind, amount)
    note = (note or "").strip() or None
    with db.get_conn() as conn:
        if _same_rule(conn, kw, kind, amount, exclude_id=rule_id):
            return (f"Another rule already uses "
                    f"'{sp.rule_label(kw.strip(), kind, amount)}'.")
        sets = ("keyword = ?, category = ?, subcategory = ?, amount_kind = ?, "
                "amount = ?, note = ?")
        params = [kw, category, subcategory, kind, amount, note]
        if priority is not None:
            sets += ", priority = ?"
            params.append(int(priority))
        conn.execute(f"UPDATE spend_rules SET {sets} WHERE id = ?",
                     (*params, int(rule_id)))
    return None


def delete_rule(rule_id: int) -> None:
    db.execute("DELETE FROM spend_rules WHERE id = ?", (int(rule_id),))


# --- Transactions -------------------------------------------------------------

def years() -> list[str]:
    ensure_tables()
    return db.query("""
        SELECT DISTINCT substr(date, 1, 4) AS yr FROM spend_transactions
        WHERE status = 'confirmed' ORDER BY yr DESC
    """)["yr"].tolist()


def transactions(date_from: str | None = None, date_to: str | None = None,
                 status: str | None = None) -> pd.DataFrame:
    ensure_tables()
    sql = f"SELECT {TXN_COLS} FROM spend_transactions WHERE 1 = 1"
    params: list = []
    if date_from:
        sql += " AND date >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND date <= ?"
        params.append(date_to)
    if status:
        sql += " AND status = ?"
        params.append(status)
    return db.query(sql + " ORDER BY date DESC, id DESC", tuple(params))


def year_rows(year: str, expand: bool = False) -> pd.DataFrame:
    """A calendar year's lines; expand=True gives split lines as their
    parts, which is what anything summing by category needs."""
    df = transactions(f"{year}-01-01", f"{year}-12-31")
    return expanded(df) if expand else df


def expanded(df: pd.DataFrame) -> pd.DataFrame:
    """Lines with every split line replaced by its parts."""
    if df.empty:
        return df.assign(split_of=None)
    return sp.expand_splits(df, splits_for(df["id"]))


def pending() -> pd.DataFrame:
    return transactions(status="pending")


def by_ids(ids) -> pd.DataFrame:
    ids = [int(i) for i in ids or []]
    if not ids:
        return pd.DataFrame(columns=[c.strip() for c in TXN_COLS.split(",")])
    return db.query(f"SELECT {TXN_COLS} FROM spend_transactions "
                    f"WHERE id IN ({_placeholders(ids)})", tuple(ids))


def counts() -> dict:
    ensure_tables()
    r = db.query("""
        SELECT COUNT(*) AS total,
               COALESCE(SUM(status = 'pending'), 0) AS pending,
               MAX(date) AS last_date
        FROM spend_transactions
    """).iloc[0]
    return {"total": int(r["total"]), "pending": int(r["pending"]),
            "last_date": r["last_date"]}


def existing_between(date_from: str, date_to: str,
                     card: str | None = None) -> pd.DataFrame:
    """Stored rows over a date range: card, date, description, amount -
    the amount as the bank printed it (lira for a lira card), which is what
    an uploaded file is compared on."""
    ensure_tables()
    sql = ("SELECT card, date, description, "
           "COALESCE(amount_orig, amount) AS amount FROM spend_transactions "
           "WHERE date >= ? AND date <= ?")
    params: list = [date_from, date_to]
    if card:
        sql += " AND card = ?"
        params.append(card)
    return db.query(sql, tuple(params))


def last_import_card() -> str | None:
    ensure_tables()
    df = db.query("SELECT card FROM spend_imports ORDER BY id DESC LIMIT 1")
    return None if df.empty else df["card"].iloc[0]


def assign(ids, category: str, subcategory: str,
           note: str | None = None) -> int:
    """Set category and sub-category and confirm - leaves the Manual Queue.
    A split line stops being split. note, when given, replaces the note."""
    ids = [int(i) for i in ids or []]
    if not ids:
        return 0
    ph = _placeholders(ids)
    with db.get_conn() as conn:
        conn.execute(f"DELETE FROM spend_splits WHERE transaction_id IN ({ph})",
                     ids)
        n = conn.execute(f"""
            UPDATE spend_transactions
            SET category = ?, subcategory = ?, status = 'confirmed',
                updated_at = ?
            WHERE id IN ({ph})
        """, (category, subcategory, _now(), *ids)).rowcount
        if note is not None:
            conn.execute(f"UPDATE spend_transactions SET note = ? "
                         f"WHERE id IN ({ph})", ((note or "").strip() or None,
                                                 *ids))
    return n


def set_note(ids, note: str | None) -> int:
    ids = [int(i) for i in ids or []]
    if not ids:
        return 0
    return db.execute(f"UPDATE spend_transactions SET note = ?, updated_at = ? "
                      f"WHERE id IN ({_placeholders(ids)})",
                      ((note or "").strip() or None, _now(), *ids))


def set_status(ids, status: str) -> int:
    ids = [int(i) for i in ids or []]
    if not ids or status not in ("confirmed", "pending"):
        return 0
    return db.execute(f"""
        UPDATE spend_transactions SET status = ?, updated_at = ?
        WHERE id IN ({_placeholders(ids)})
    """, (status, _now(), *ids))


def _insert(conn, card: str, r: dict, import_id, now: str,
            statement_id=None) -> int:
    """One new line. r: date, description, amount (pounds), category,
    subcategory, status, and optionally note, amount_orig and currency."""
    orig = r.get("amount_orig")
    cur = conn.execute("""
        INSERT INTO spend_transactions
            (date, card, description, amount, category, subcategory, status,
             import_id, created_at, updated_at, currency, amount_orig,
             statement_id, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (r["date"], card, r["description"], round(float(r["amount"]), 2),
          r.get("category"), r.get("subcategory"),
          r.get("status", "confirmed"), import_id, now, now,
          r.get("currency") if orig is not None else None,
          None if orig is None else round(float(orig), 2), statement_id,
          r.get("note") or None))
    return cur.lastrowid


def import_rows(card: str, filename: str, rows: list[dict],
                rows_in_file: int) -> dict:
    """Store an uploaded file's chosen rows.

    rows: dicts with date, description, amount (pounds), category,
    subcategory and status, plus amount_orig and currency for a card in
    another currency. Counted de-duplication, as 8052 did it: a row is only
    skipped if the database already holds as many identical rows (same
    card, date, description and amount as printed) as this batch brings -
    so two identical coffees on one day both get in, and re-importing the
    same file adds nothing.

    Returns {'import_id', 'inserted', 'skipped'}."""
    ensure_tables()
    now = _now()
    groups: dict = {}
    for r in rows:
        printed = r.get("amount_orig")
        printed = r["amount"] if printed is None else printed
        k = (r["date"], r["description"], round(float(printed), 2))
        groups.setdefault(k, []).append(r)

    inserted = skipped = 0
    with db.get_conn() as conn:
        dates = [r["date"] for r in rows] or [None]
        cur = conn.execute("""
            INSERT INTO spend_imports (card, filename, imported_at,
                                       rows_in_file, first_date, last_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (card, filename, now, rows_in_file, min(dates), max(dates)))
        import_id = cur.lastrowid
        for (d, desc, amt), batch in groups.items():
            have = conn.execute("""
                SELECT COUNT(*) FROM spend_transactions
                WHERE card = ? AND date = ? AND description = ?
                  AND ROUND(COALESCE(amount_orig, amount), 2) = ?
            """, (card, d, desc, amt)).fetchone()[0]
            for r in batch[have:]:
                _insert(conn, card, r, import_id, now)
                inserted += 1
            skipped += min(have, len(batch))
        conn.execute("UPDATE spend_imports SET inserted = ?, skipped = ? "
                     "WHERE id = ?", (inserted, skipped, import_id))
    return {"import_id": import_id, "inserted": inserted, "skipped": skipped}


def posting_dates(since: str) -> pd.DataFrame:
    """Distinct posting dates per card from `since`: card, date."""
    ensure_tables()
    return db.query("SELECT DISTINCT card, date FROM spend_transactions "
                    "WHERE date >= ? ORDER BY card, date", (since,))


def last_imports() -> pd.DataFrame:
    """The latest import of each card: card, filename, imported_at."""
    ensure_tables()
    return db.query("""
        SELECT i.card, i.filename, i.imported_at FROM spend_imports i
        JOIN (SELECT card, MAX(id) AS id FROM spend_imports GROUP BY card) l
          ON l.id = i.id
    """)


def gbp_rates(currency: str) -> pd.DataFrame:
    """Daily units of `currency` per pound - date, rate - from the prices
    the portfolio already keeps (YF:GBPTRY=X for lira)."""
    return db.query("SELECT date, close AS rate FROM prices "
                    "WHERE fund_id = ? AND close > 0 ORDER BY date",
                    (f"YF:GBP{currency.upper()}=X",))


def delete_card_rows(card: str) -> dict:
    """Remove every transaction, split, statement and import record of a
    card (the card itself stays). Returns how many lines and imports
    went."""
    ensure_tables()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM spend_splits WHERE transaction_id IN "
                     "(SELECT id FROM spend_transactions WHERE card = ?)",
                     (card,))
        conn.execute("DELETE FROM spend_statements WHERE card = ?", (card,))
        t = conn.execute("DELETE FROM spend_transactions WHERE card = ?",
                         (card,)).rowcount
        i = conn.execute("DELETE FROM spend_imports WHERE card = ?",
                         (card,)).rowcount
    return {"transactions": t, "imports": i}


def imports(limit: int = 20) -> pd.DataFrame:
    ensure_tables()
    return db.query("""
        SELECT id, card, filename, imported_at, rows_in_file, inserted,
               skipped, first_date, last_date
        FROM spend_imports ORDER BY id DESC LIMIT ?
    """, (limit,))


# --- Card accounts -------------------------------------------------------------

def card_for_ref(ref: str | None) -> str | None:
    """The card a statement account ('HSBC UK:1234') was imported to."""
    if not ref:
        return None
    ensure_tables()
    df = db.query("SELECT card FROM spend_card_accounts WHERE ref = ?", (ref,))
    return None if df.empty else df["card"].iloc[0]


def card_refs() -> dict[str, list[str]]:
    """card -> the statement accounts known for it."""
    ensure_tables()
    out: dict = {}
    for r in db.query("SELECT ref, card FROM spend_card_accounts ORDER BY ref"
                      ).itertuples(index=False):
        out.setdefault(r.card, []).append(r.ref)
    return out


# --- Statements --------------------------------------------------------------------

STATEMENT_COLS = ("id, card, bank, account_ref, currency, period_from, "
                  "period_to, statement_date, previous_balance, new_balance, "
                  "debits, credits, lines, filename, imported_at")


def statements(card: str | None = None) -> pd.DataFrame:
    ensure_tables()
    sql = f"SELECT {STATEMENT_COLS} FROM spend_statements"
    if card:
        return db.query(sql + " WHERE card = ? ORDER BY period_to", (card,))
    return db.query(sql + " ORDER BY card, period_to")


def statement_windows(card: str) -> list[tuple[str, str]]:
    """(from, to) of every statement stored for a card."""
    df = statements(card)
    return list(zip(df["period_from"], df["period_to"]))


def statement_ids_for(card: str, period_tos) -> dict[str, int]:
    """period_to -> id for statements of a card already stored."""
    ensure_tables()
    tos = list(period_tos)
    if not tos:
        return {}
    df = db.query(f"SELECT id, period_to FROM spend_statements WHERE card = ? "
                  f"AND period_to IN ({_placeholders(tos)})", (card, *tos))
    return dict(zip(df["period_to"], df["id"].astype(int)))


def match_candidates(card: str, date_from: str, date_to: str,
                     statement_ids=()) -> pd.DataFrame:
    """A card's stored rows a statement may pair with: those on no
    statement, or on one of statement_ids (the same statements uploaded
    again). amount is as printed - lira for the lira card; gbp is the
    stored pound amount."""
    ensure_tables()
    ids = [int(i) for i in statement_ids]
    cond = "statement_id IS NULL"
    if ids:
        cond = f"(statement_id IS NULL OR statement_id IN ({_placeholders(ids)}))"
    return db.query(f"""
        SELECT id, date, description, COALESCE(amount_orig, amount) AS amount,
               amount AS gbp, statement_id, category, subcategory, status, note
        FROM spend_transactions
        WHERE card = ? AND date >= ? AND date <= ? AND {cond}
        ORDER BY date, id
    """, (card, date_from, date_to, *ids))


def apply_statements(card: str, filename: str, batches: list[dict],
                     rows_in_files: int) -> dict:
    """Store statements and bring the card's rows in line with them, all in
    one transaction.

    Each batch: {'record': statement fields, 'ref': account ref or None,
    'link': [{'id', 'date', 'description', 'amount' (pounds), and
    optionally 'amount_orig' (a lira card's lira), 'old_description' (kept
    in the note), 'category' and 'subcategory' you set in the preview}],
    'insert': [new rows], 'delete': [stored ids]}. Linked rows take the
    statement's date, wording and exact amount - a split line's last part
    takes up any difference. Returns counts and the statement ids."""
    ensure_tables()
    now = _now()
    done = {"statements": [], "linked": 0, "reworded": 0, "inserted": 0,
            "deleted": 0, "amounts": 0}
    with db.get_conn() as conn:
        dates = [b["record"]["period_from"] for b in batches] + \
                [b["record"]["period_to"] for b in batches]
        cur = conn.execute("""
            INSERT INTO spend_imports (card, filename, imported_at,
                                       rows_in_file, first_date, last_date)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (card, filename, now, rows_in_files, min(dates), max(dates)))
        import_id = cur.lastrowid
        for b in batches:
            rec = {**b["record"], "card": card, "imported_at": now}
            cols = [c.strip() for c in STATEMENT_COLS.split(",")
                    if c.strip() != "id"]
            conn.execute(f"""
                INSERT INTO spend_statements ({", ".join(cols)})
                VALUES ({_placeholders(cols)})
                ON CONFLICT(card, period_to) DO UPDATE SET
                {", ".join(f"{c} = excluded.{c}" for c in cols if c != "card")}
            """, [rec.get(c) for c in cols])
            sid = conn.execute("SELECT id FROM spend_statements WHERE card = ? "
                               "AND period_to = ?",
                               (card, rec["period_to"])).fetchone()[0]
            done["statements"].append(sid)
            if b.get("ref"):
                conn.execute("INSERT OR REPLACE INTO spend_card_accounts "
                             "(ref, card, created_at) VALUES (?, ?, ?)",
                             (b["ref"], card, now))
            for lk in b["link"]:
                if lk.get("amount") is not None:
                    done["amounts"] += _set_amount(conn, int(lk["id"]),
                                                   lk["amount"],
                                                   lk.get("amount_orig"))
                old = lk.get("old_description")
                if old and old != lk["description"]:
                    conn.execute("""
                        UPDATE spend_transactions SET note = CASE
                            WHEN note IS NULL OR note = '' THEN ?
                            ELSE note || ' · ' || ? END
                        WHERE id = ?""", (old, old, int(lk["id"])))
                    done["reworded"] += 1
                conn.execute("UPDATE spend_transactions SET statement_id = ?, "
                             "date = ?, description = ?, updated_at = ? "
                             "WHERE id = ?",
                             (sid, lk["date"], lk["description"], now,
                              int(lk["id"])))
                if lk.get("category") and lk.get("subcategory"):
                    conn.execute("DELETE FROM spend_splits WHERE "
                                 "transaction_id = ?", (int(lk["id"]),))
                    conn.execute("UPDATE spend_transactions SET category = ?, "
                                 "subcategory = ?, status = 'confirmed' "
                                 "WHERE id = ?", (lk["category"],
                                                  lk["subcategory"],
                                                  int(lk["id"])))
                done["linked"] += 1
            for r in b["insert"]:
                _insert(conn, card, r, import_id, now, statement_id=sid)
                done["inserted"] += 1
            ids = [int(i) for i in b["delete"]]
            if ids:
                ph = _placeholders(ids)
                conn.execute(f"DELETE FROM spend_splits WHERE transaction_id "
                             f"IN ({ph})", ids)
                done["deleted"] += conn.execute(
                    f"DELETE FROM spend_transactions WHERE id IN ({ph})",
                    ids).rowcount
        conn.execute("UPDATE spend_imports SET inserted = ?, skipped = ? "
                     "WHERE id = ?", (done["inserted"], done["linked"],
                                      import_id))
    return done


def _set_amount(conn, tid: int, amount: float, amount_orig=None) -> int:
    """Give a stored line the statement's exact amount. A split line's last
    part absorbs the difference, so its parts still add up. Returns 1 if
    the amount changed."""
    amount = round(float(amount), 2)
    old = conn.execute("SELECT amount FROM spend_transactions WHERE id = ?",
                       (tid,)).fetchone()
    if old is None or abs(old[0] - amount) < 0.005:
        if amount_orig is not None:
            conn.execute("UPDATE spend_transactions SET amount_orig = ? "
                         "WHERE id = ?", (round(float(amount_orig), 2), tid))
        return 0
    conn.execute("UPDATE spend_transactions SET amount = ? WHERE id = ?",
                 (amount, tid))
    if amount_orig is not None:
        conn.execute("UPDATE spend_transactions SET amount_orig = ? "
                     "WHERE id = ?", (round(float(amount_orig), 2), tid))
    last = conn.execute("SELECT id FROM spend_splits WHERE transaction_id = ? "
                        "ORDER BY part DESC LIMIT 1", (tid,)).fetchone()
    if last:
        conn.execute("UPDATE spend_splits SET amount = ROUND(amount + ?, 2) "
                     "WHERE id = ?", (round(amount - old[0], 2), last[0]))
    return 1


def statement_rows(statement_id: int) -> pd.DataFrame:
    ensure_tables()
    return db.query(f"SELECT {TXN_COLS} FROM spend_transactions "
                    f"WHERE statement_id = ? ORDER BY date, id",
                    (int(statement_id),))


def statement_overview() -> pd.DataFrame:
    """Every stored statement with its checks (core.spending.statement_checks)
    and status 'ok' / 'break'."""
    st = statements()
    if st.empty:
        return st.assign(ok=[], lines_ok=[], chain_ok=[], unlinked=[],
                         lines_debits=[], lines_credits=[], lines_stored=[])
    linked = db.query("""
        SELECT statement_id, COALESCE(amount_orig, amount) AS amount
        FROM spend_transactions WHERE statement_id IS NOT NULL
    """)
    unl = db.query("""
        SELECT card, date FROM spend_transactions WHERE statement_id IS NULL
    """)
    by_sid = dict(tuple(linked.groupby("statement_id"))) if not linked.empty \
        else {}
    out = []
    for card, grp in st.groupby("card", sort=False):
        prev = None
        card_unl = unl.loc[unl["card"] == card, "date"]
        for r in grp.sort_values("period_to").to_dict("records"):
            rows = by_sid.get(r["id"], linked.iloc[0:0])
            n_unl = int(((card_unl >= r["period_from"])
                         & (card_unl <= r["period_to"])).sum())
            # Lira statements are cut by transaction date: a purchase on
            # the cut-off day can sit on the next one, so only rows on no
            # statement at all count against it.
            chk = sp.statement_checks(r, rows, prev, n_unl)
            out.append({**r, **chk, "lines_stored": chk["lines"]})
            prev = r
    return pd.DataFrame(out)


def unlinked_months() -> pd.DataFrame:
    """card, month ('YYYY-MM'), rows - lines on no statement (CSV rows
    waiting for theirs, or history before the statements)."""
    ensure_tables()
    return db.query("""
        SELECT card, substr(date, 1, 7) AS month, COUNT(*) AS rows
        FROM spend_transactions WHERE statement_id IS NULL
        GROUP BY card, month ORDER BY card, month
    """)


# --- Splits and templates ----------------------------------------------------------

def splits_for(ids) -> pd.DataFrame:
    """Parts of the given lines: transaction_id, part, amount, category,
    subcategory, note, template_id."""
    ensure_tables()
    ids = [int(i) for i in (list(ids) if ids is not None else [])]
    cols = ["transaction_id", "part", "amount", "category", "subcategory",
            "note", "template_id"]
    if not ids:
        return pd.DataFrame(columns=cols)
    out = []
    for i in range(0, len(ids), 900):                # SQLite variable limit
        chunk = ids[i:i + 900]
        out.append(db.query(f"SELECT {', '.join(cols)} FROM spend_splits "
                            f"WHERE transaction_id IN ({_placeholders(chunk)}) "
                            f"ORDER BY transaction_id, part", tuple(chunk)))
    return pd.concat(out, ignore_index=True)


def save_split(transaction_id: int, parts: list[dict],
               template_id: int | None = None) -> str | None:
    """Replace a line's parts. parts: amount (signed), category,
    subcategory, note. The line is confirmed and loses its own category.
    Returns an error message, or None when saved."""
    ensure_tables()
    row = db.query("SELECT amount FROM spend_transactions WHERE id = ?",
                   (int(transaction_id),))
    if row.empty:
        return "That line is no longer there."
    problem = sp.check_split(float(row["amount"].iloc[0]), parts)
    if problem:
        return problem
    with db.get_conn() as conn:
        conn.execute("DELETE FROM spend_splits WHERE transaction_id = ?",
                     (int(transaction_id),))
        for i, p in enumerate(parts, 1):
            conn.execute("""
                INSERT INTO spend_splits (transaction_id, part, amount,
                    category, subcategory, note, template_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (int(transaction_id), i, round(float(p["amount"]), 2),
                  p["category"], p["subcategory"],
                  (p.get("note") or "").strip() or None, template_id))
        conn.execute("UPDATE spend_transactions SET category = NULL, "
                     "subcategory = NULL, status = 'confirmed', updated_at = ? "
                     "WHERE id = ?", (_now(), int(transaction_id)))
    return None


def remove_split(transaction_id: int) -> None:
    """Back to one line, with no category - it returns to the Manual
    Queue."""
    with db.get_conn() as conn:
        conn.execute("DELETE FROM spend_splits WHERE transaction_id = ?",
                     (int(transaction_id),))
        conn.execute("UPDATE spend_transactions SET status = 'pending', "
                     "updated_at = ? WHERE id = ? AND category IS NULL",
                     (_now(), int(transaction_id)))


def templates() -> pd.DataFrame:
    """id, name, parts (list of dicts), uses (lines split with it)."""
    ensure_tables()
    df = db.query("""
        SELECT t.id, t.name, t.parts,
               (SELECT COUNT(DISTINCT transaction_id) FROM spend_splits s
                WHERE s.template_id = t.id) AS uses
        FROM spend_split_templates t ORDER BY uses DESC, t.name
    """)
    df["parts"] = df["parts"].map(json.loads)
    return df


def template(template_id) -> dict | None:
    df = templates()
    hit = df[df["id"] == int(template_id)] if template_id is not None else df.iloc[0:0]
    return None if hit.empty else hit.iloc[0].to_dict()


def save_template(name: str, parts: list[dict]) -> str | None:
    """parts: category, subcategory, note, kind (fixed / percent / rest),
    value. Saving under an existing name replaces it. Returns an error
    message, or None."""
    name = (name or "").strip()
    if not name:
        return "Name the template."
    if len(parts) < 2:
        return "A template needs at least two parts."
    for i, p in enumerate(parts, 1):
        if not (p.get("category") and p.get("subcategory")):
            return f"Part {i} needs a category and sub-category."
    if sum(p.get("kind") == "rest" for p in parts) > 1:
        return "Only one part can take what is left."
    clean = [{"category": p["category"], "subcategory": p["subcategory"],
              "note": (p.get("note") or "").strip() or None,
              "kind": p.get("kind") or "fixed",
              "value": None if p.get("kind") == "rest" else float(p["value"])}
             for p in parts]
    ensure_tables()
    db.execute("""
        INSERT INTO spend_split_templates (name, parts, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET parts = excluded.parts
    """, (name, json.dumps(clean), _now()))
    return None


def delete_template(template_id: int) -> None:
    """Lines already split with it keep their parts."""
    with db.get_conn() as conn:
        conn.execute("UPDATE spend_splits SET template_id = NULL "
                     "WHERE template_id = ?", (int(template_id),))
        conn.execute("DELETE FROM spend_split_templates WHERE id = ?",
                     (int(template_id),))


def history(since: str | None = None) -> pd.DataFrame:
    """Confirmed lines you placed - by category, or split with a template -
    for suggestions: date, description, amount, category, subcategory,
    note, template."""
    ensure_tables()
    sql = """
        SELECT t.date, t.description, t.amount, t.category, t.subcategory,
               t.note, s.template AS template
        FROM spend_transactions t
        LEFT JOIN (SELECT transaction_id, MAX(template_id) AS template
                   FROM spend_splits GROUP BY transaction_id) s
          ON s.transaction_id = t.id
        WHERE t.status = 'confirmed'
          AND (t.category IS NOT NULL OR s.template IS NOT NULL)
    """
    if since:
        return db.query(sql + " AND t.date >= ?", (since,))
    return db.query(sql)
