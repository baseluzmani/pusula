"""
Spending tables. All the SQL for the Spending section lives here.

    spend_cards          cards you import from. active = 0 hides a card from
                         the Import dropdown without touching its history.
    spend_categories     categories, in display order
    spend_subcategories  sub-categories per category
    spend_rules          keyword rules: a keyword found anywhere in a
                         description sets the category and sub-category.
                         Checked in priority order, lowest first; equal
                         priorities in the order they were added.
    spend_imports        one row per uploaded file
    spend_transactions   one row per bank line. amount is signed as in the
                         bank file (spending negative). date is the posting
                         date. status is 'confirmed' or 'pending' - pending
                         rows are the Manual Queue.

Tables are created on first use. scripts/migrate_spending.py fills them from
the old 8052 database once.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from core import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS spend_cards (
    name        TEXT PRIMARY KEY,
    sort_order  INTEGER NOT NULL DEFAULT 99,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT
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
    keyword     TEXT NOT NULL UNIQUE,
    category    TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 100,
    created_at  TEXT
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
    updated_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_spend_txn_date   ON spend_transactions(date);
CREATE INDEX IF NOT EXISTS idx_spend_txn_card   ON spend_transactions(card, date);
CREATE INDEX IF NOT EXISTS idx_spend_txn_status ON spend_transactions(status);
"""

TXN_COLS = ("id, date, card, description, amount, category, subcategory, "
            "status, import_id")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_tables() -> None:
    with db.get_conn() as conn:
        conn.executescript(SCHEMA)


def _placeholders(items) -> str:
    return ",".join("?" for _ in items)


# --- Cards -------------------------------------------------------------------

def cards(active_only: bool = False) -> pd.DataFrame:
    ensure_tables()
    sql = "SELECT name, sort_order, active FROM spend_cards"
    if active_only:
        sql += " WHERE active = 1"
    return db.query(sql + " ORDER BY sort_order, name")


def card_names(active_only: bool = True) -> list[str]:
    return cards(active_only)["name"].tolist()


def card_summary() -> pd.DataFrame:
    """Every card with its row count and first / last posting date."""
    ensure_tables()
    return db.query("""
        SELECT c.name, c.active,
               COUNT(t.id) AS rows, MIN(t.date) AS first_date,
               MAX(t.date) AS last_date
        FROM spend_cards c LEFT JOIN spend_transactions t ON t.card = c.name
        GROUP BY c.name ORDER BY c.sort_order, c.name
    """)


def add_card(name: str) -> str | None:
    """Returns an error message, or None when added."""
    name = (name or "").strip()
    if not name:
        return "Type a card name first."
    ensure_tables()
    if name.lower() in {n.lower() for n in card_names(active_only=False)}:
        return f"{name} already exists."
    db.execute("""
        INSERT INTO spend_cards (name, sort_order, active, created_at)
        VALUES (?, (SELECT COALESCE(MAX(sort_order), 0) + 1 FROM spend_cards),
                1, ?)
    """, (name, _now()))
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
    old_sub None means every sub-category of old_cat."""
    if old_sub:
        return db.execute("""
            UPDATE spend_transactions
            SET category = ?, subcategory = ?, updated_at = ?
            WHERE category = ? AND subcategory = ?
        """, (new_cat, new_sub, _now(), old_cat, old_sub))
    return db.execute("""
        UPDATE spend_transactions SET category = ?, subcategory = ?,
               updated_at = ?
        WHERE category = ?
    """, (new_cat, new_sub, _now(), old_cat))


def categories_in_use() -> list[str]:
    ensure_tables()
    return db.query("""
        SELECT DISTINCT category FROM spend_transactions
        WHERE category IS NOT NULL ORDER BY category
    """)["category"].tolist()


def subcategories_in_use(category: str) -> list[str]:
    """Sub-categories stored transactions carry under a category - including
    ones missing from the lists (8052 let any sub-category go with any
    category), so Re-map can reach them."""
    ensure_tables()
    return db.query("""
        SELECT DISTINCT subcategory FROM spend_transactions
        WHERE category = ? AND subcategory IS NOT NULL AND subcategory <> ''
        ORDER BY subcategory
    """, (category,))["subcategory"].tolist()


def used_subcategories() -> list[str]:
    ensure_tables()
    return db.query("""
        SELECT DISTINCT subcategory FROM spend_transactions
        WHERE subcategory IS NOT NULL AND subcategory <> ''
        ORDER BY subcategory
    """)["subcategory"].tolist()


# --- Rules --------------------------------------------------------------------

def rules() -> pd.DataFrame:
    ensure_tables()
    return db.query("SELECT id, keyword, category, subcategory, priority "
                    "FROM spend_rules ORDER BY priority, id")


def rule_tuples() -> list[tuple[str, str, str]]:
    """(keyword, category, subcategory) in the order they are checked."""
    return [(r.keyword, r.category, r.subcategory)
            for r in rules().itertuples(index=False)]


def save_rule(keyword: str, category: str, subcategory: str,
              priority: int | None = None) -> str:
    """Add a rule, or update the one with this keyword. A new rule with no
    priority goes to the end of the list, so it cannot change how anything
    already matched is classified. Returns the keyword as stored."""
    kw = (keyword or "").upper()
    if not kw.strip():
        raise ValueError("empty keyword")
    ensure_tables()
    with db.get_conn() as conn:
        if priority is None:
            row = conn.execute("SELECT priority FROM spend_rules "
                               "WHERE keyword = ?", (kw,)).fetchone()
            if row:
                priority = row[0]
            else:
                priority = conn.execute(
                    "SELECT COALESCE(MAX(priority), 0) + 1 "
                    "FROM spend_rules").fetchone()[0]
        conn.execute("""
            INSERT INTO spend_rules (keyword, category, subcategory, priority,
                                     created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(keyword) DO UPDATE SET
                category = excluded.category,
                subcategory = excluded.subcategory,
                priority = excluded.priority
        """, (kw, category, subcategory, int(priority), _now()))
    return kw


def update_rule(rule_id: int, keyword: str, category: str, subcategory: str,
                priority: int | None) -> str | None:
    """Edit a rule in place, keyword included. Returns an error message, or
    None when saved. A blank priority keeps the rule's current one."""
    kw = (keyword or "").upper()
    if not kw.strip():
        return "Type a keyword."
    clash = db.query("SELECT id FROM spend_rules WHERE keyword = ? AND id <> ?",
                     (kw, int(rule_id)))
    if not clash.empty:
        return f"Another rule already uses '{kw.strip()}'."
    if priority is None:
        db.execute("UPDATE spend_rules SET keyword = ?, category = ?, "
                   "subcategory = ? WHERE id = ?",
                   (kw, category, subcategory, int(rule_id)))
    else:
        db.execute("UPDATE spend_rules SET keyword = ?, category = ?, "
                   "subcategory = ?, priority = ? WHERE id = ?",
                   (kw, category, subcategory, int(priority), int(rule_id)))
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


def year_rows(year: str) -> pd.DataFrame:
    return transactions(f"{year}-01-01", f"{year}-12-31")


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
    """Stored rows over a date range: card, date, description, amount."""
    ensure_tables()
    sql = ("SELECT card, date, description, amount FROM spend_transactions "
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


def assign(ids, category: str, subcategory: str) -> int:
    """Set category and sub-category and confirm - leaves the Manual Queue."""
    ids = [int(i) for i in ids or []]
    if not ids:
        return 0
    return db.execute(f"""
        UPDATE spend_transactions
        SET category = ?, subcategory = ?, status = 'confirmed',
            updated_at = ?
        WHERE id IN ({_placeholders(ids)})
    """, (category, subcategory, _now(), *ids))


def set_status(ids, status: str) -> int:
    ids = [int(i) for i in ids or []]
    if not ids or status not in ("confirmed", "pending"):
        return 0
    return db.execute(f"""
        UPDATE spend_transactions SET status = ?, updated_at = ?
        WHERE id IN ({_placeholders(ids)})
    """, (status, _now(), *ids))


def import_rows(card: str, filename: str, rows: list[dict],
                rows_in_file: int) -> dict:
    """Store an uploaded file's chosen rows.

    rows: dicts with date, description, amount, category, subcategory and
    status. Counted de-duplication, as 8052 did it: a row is only skipped if
    the database already holds as many identical rows (same card, date,
    description and amount) as this batch brings - so two identical coffees
    on one day both get in, and re-importing the same file adds nothing.

    Returns {'import_id', 'inserted', 'skipped'}."""
    ensure_tables()
    now = _now()
    groups: dict = {}
    for r in rows:
        k = (r["date"], r["description"], round(float(r["amount"]), 2))
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
                  AND ROUND(amount, 2) = ?
            """, (card, d, desc, amt)).fetchone()[0]
            for r in batch[have:]:
                conn.execute("""
                    INSERT INTO spend_transactions
                        (date, card, description, amount, category,
                         subcategory, status, import_id, created_at,
                         updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (d, card, desc, amt, r.get("category"),
                      r.get("subcategory"), r.get("status", "confirmed"),
                      import_id, now, now))
                inserted += 1
            skipped += min(have, len(batch))
        conn.execute("UPDATE spend_imports SET inserted = ?, skipped = ? "
                     "WHERE id = ?", (inserted, skipped, import_id))
    return {"import_id": import_id, "inserted": inserted, "skipped": skipped}


def imports(limit: int = 20) -> pd.DataFrame:
    ensure_tables()
    return db.query("""
        SELECT id, card, filename, imported_at, rows_in_file, inserted,
               skipped, first_date, last_date
        FROM spend_imports ORDER BY id DESC LIMIT ?
    """, (limit,))
