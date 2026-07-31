# create_expenditure_tables.py
# Run once: python3 create_expenditure_tables.py

import sqlite3
from datetime import datetime

DB_PATH = 'data/funds.db'
conn    = sqlite3.connect(DB_PATH)
now     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

conn.executescript("""

-- Categories
CREATE TABLE IF NOT EXISTS expenditure_categories (
    id         INTEGER PRIMARY KEY,
    name       TEXT UNIQUE NOT NULL,
    sort_order INTEGER DEFAULT 0
);

-- Subcategory autocomplete history
CREATE TABLE IF NOT EXISTS expenditure_subcategories (
    id          INTEGER PRIMARY KEY,
    category    TEXT NOT NULL,
    subcategory TEXT NOT NULL,
    use_count   INTEGER DEFAULT 1,
    UNIQUE(category, subcategory)
);

-- Merchant mapping table
CREATE TABLE IF NOT EXISTS expenditure_mappings (
    id                INTEGER PRIMARY KEY,
    description_clean TEXT UNIQUE NOT NULL,
    category          TEXT,
    subcategory       TEXT,
    match_type        TEXT,   -- 'exact','keyword','fuzzy','ai','manual'
    confidence        REAL DEFAULT 1.0,
    use_count         INTEGER DEFAULT 1,
    updated_at        TEXT
);

-- User-editable keyword rules
CREATE TABLE IF NOT EXISTS expenditure_rules (
    id          INTEGER PRIMARY KEY,
    pattern     TEXT NOT NULL,
    match_type  TEXT NOT NULL,  -- 'contains','starts_with','ends_with','regex'
    category    TEXT NOT NULL,
    subcategory TEXT,
    priority    INTEGER DEFAULT 0,
    active      INTEGER DEFAULT 1,
    created_at  TEXT
);

-- Import batches
CREATE TABLE IF NOT EXISTS expenditure_imports (
    id          INTEGER PRIMARY KEY,
    filename    TEXT NOT NULL,
    source      TEXT NOT NULL,
    import_date TEXT NOT NULL,
    row_count   INTEGER DEFAULT 0,
    new_count   INTEGER DEFAULT 0,
    skip_count  INTEGER DEFAULT 0,
    created_at  TEXT
);

-- Main transactions table
CREATE TABLE IF NOT EXISTS expenditure_transactions (
    id                INTEGER PRIMARY KEY,
    import_id         INTEGER REFERENCES expenditure_imports(id),
    parent_id         INTEGER REFERENCES expenditure_transactions(id),
    date              TEXT NOT NULL,
    description_raw   TEXT NOT NULL,
    description_clean TEXT,
    amount            REAL NOT NULL,
    source            TEXT NOT NULL,
    category          TEXT,
    subcategory       TEXT,
    mapped_by         TEXT,
    confidence        REAL,
    status            TEXT DEFAULT 'pending',
    netting_id        INTEGER,
    notes             TEXT,
    created_at        TEXT,
    updated_at        TEXT,
    UNIQUE(date, description_raw, amount, source)
);

-- Index for fast lookups
CREATE INDEX IF NOT EXISTS idx_exp_txn_date     ON expenditure_transactions(date);
CREATE INDEX IF NOT EXISTS idx_exp_txn_source   ON expenditure_transactions(source);
CREATE INDEX IF NOT EXISTS idx_exp_txn_status   ON expenditure_transactions(status);
CREATE INDEX IF NOT EXISTS idx_exp_txn_category ON expenditure_transactions(category);
CREATE INDEX IF NOT EXISTS idx_exp_map_desc     ON expenditure_mappings(description_clean);
CREATE INDEX IF NOT EXISTS idx_exp_rules_pri    ON expenditure_rules(priority DESC);

""")

# ── Seed categories in correct order ──
categories = [
    'Grocery', 'Dining', 'Transport', 'Shopping', 'Electronics',
    'Entertainment', 'Health', 'Childcare', 'Home Services',
    'Utilities', 'Subscription', 'Mortgage', 'Tax & Fee',
    'Income', 'Investment', 'Transfer', 'Cash',
    'Amazon', 'PAYPAL', 'Holiday',
]
for i, cat in enumerate(categories):
    conn.execute(
        "INSERT OR IGNORE INTO expenditure_categories (name, sort_order) VALUES (?, ?)",
        (cat, i)
    )

# ── Seed initial keyword rules from known patterns ──
rules = [
    # Transport
    ('TFL',          'starts_with', 'Transport',   'TFL',        100),
    ('OYSTER',       'contains',    'Transport',   'TFL',        100),
    ('UBER',         'contains',    'Transport',   'Uber',       100),
    ('TRAINLINE',    'contains',    'Transport',   'Train',      100),
    ('NATIONAL RAIL','contains',    'Transport',   'Train',      100),
    ('GREATER ANGL', 'contains',    'Transport',   'Train',      100),
    # Grocery
    ('TESCO',        'contains',    'Grocery',     'Tesco',      100),
    ('SAINSBURY',    'contains',    'Grocery',     'Sainsburys', 100),
    ('WAITROSE',     'contains',    'Grocery',     'Waitrose',   100),
    ('ASDA',         'contains',    'Grocery',     'Asda',       100),
    ('LIDL',         'contains',    'Grocery',     'Lidl',       100),
    ('ALDI',         'contains',    'Grocery',     'Aldi',       100),
    ('MARKS & SPENCER','contains',  'Grocery',     'M&S',        90),
    ('M&S SIMPLY',   'contains',    'Grocery',     'M&S',        100),
    ('CO-OP',        'contains',    'Grocery',     'Co-op',      100),
    ('MORRISON',     'contains',    'Grocery',     'Morrisons',  100),
    ('ICELAND',      'contains',    'Grocery',     'Iceland',    100),
    # Utilities
    ('EDF ENERGY',   'contains',    'Utilities',   'Electric',   100),
    ('BRITISH GAS',  'contains',    'Utilities',   'Gas',        100),
    ('THAMES WATER', 'contains',    'Utilities',   'Water',      100),
    ('VIRGIN MEDIA', 'contains',    'Utilities',   'Broadband',  100),
    ('SKY',          'starts_with', 'Utilities',   'Broadband',  90),
    ('BT GROUP',     'contains',    'Utilities',   'Broadband',  100),
    ('EE ',          'starts_with', 'Utilities',   'Mobile',     90),
    ('O2 ',          'starts_with', 'Utilities',   'Mobile',     90),
    ('VODAFONE',     'contains',    'Utilities',   'Mobile',     100),
    # Subscription
    ('NETFLIX',      'contains',    'Subscription','Streaming',  100),
    ('SPOTIFY',      'contains',    'Subscription','Streaming',  100),
    ('DISNEY',       'contains',    'Subscription','Streaming',  100),
    ('APPLE.COM',    'contains',    'Subscription','Apple',      100),
    ('AMAZON PRIME', 'contains',    'Subscription','Amazon Prime',100),
    ('PRIME VIDEO',  'contains',    'Subscription','Streaming',  100),
    # Income
    ('HMRC PAYE',    'contains',    'Income',      'Salary',     100),
    ('SALARY',       'contains',    'Income',      'Salary',     100),
    ('HMRC SA CR',   'contains',    'Income',      'HMRC',       100),
    # Tax & Fee
    ('HMRC',         'contains',    'Tax & Fee',   'HMRC',       80),
    ('COUNCIL TAX',  'contains',    'Tax & Fee',   'Council Tax',100),
    ('ROYAL GREENWICH','contains',  'Tax & Fee',   'Council Tax',100),
    ('TVLICENSING',  'contains',    'Tax & Fee',   'TV License', 100),
    ('INTEREST',     'starts_with', 'Tax & Fee',   'Interest',   90),
    # Cash
    ('CASH ',        'starts_with', 'Cash',        'ATM',        100),
    ('ATM',          'contains',    'Cash',        'ATM',        90),
    # Dining
    ('MCDONALDS',    'contains',    'Dining',      'McDonalds',  100),
    ('DOMINO',       'contains',    'Dining',      'Takeaway',   100),
    ('DELIVEROO',    'contains',    'Dining',      'Takeaway',   100),
    ('JUST EAT',     'contains',    'Dining',      'Takeaway',   100),
    ('UBER EATS',    'contains',    'Dining',      'Takeaway',   100),
    ('WASABI',       'contains',    'Dining',      'Lunch',      100),
    ('PRET',         'contains',    'Dining',      'Cafe',       100),
    ('COSTA',        'contains',    'Dining',      'Cafe',       100),
    ('STARBUCKS',    'contains',    'Dining',      'Cafe',       100),
    # Health
    ('PHARMACY',     'contains',    'Health',      'Pharmacy',   100),
    ('CHEMIST',      'contains',    'Health',      'Pharmacy',   100),
    ('BOOTS',        'contains',    'Health',      'Pharmacy',   90),
    ('DENTIST',      'contains',    'Health',      'Dental',     100),
    # Shopping
    ('AMAZON',       'contains',    'Amazon',      None,         100),
    ('PAYPAL',       'contains',    'PAYPAL',      None,         50),
    # Mortgage
    ('MORTGAGE',     'contains',    'Mortgage',    None,         100),
    # Investment
    ('INTERACTIVE INVEST','contains','Investment', 'Broker',     100),
    ('HARGREAVES',   'contains',    'Investment',  'Broker',     100),
    ('VANGUARD',     'contains',    'Investment',  'Broker',     100),
    # Transfer / Internal
    ('HSBC CARD PYMT','contains',   'Transfer',    'Internal',   100),
    ('DIRECT DEBIT PAYMENT','contains','Transfer', 'Internal',   100),
    ('TRANSFER',     'contains',    'Transfer',    'Internal',   80),
]

for pattern, match_type, category, subcategory, priority in rules:
    conn.execute("""
        INSERT OR IGNORE INTO expenditure_rules
            (pattern, match_type, category, subcategory, priority, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
    """, (pattern, match_type, category, subcategory, priority, now))

conn.commit()

# ── Verify ──
print("Tables created successfully:")
for t in ['expenditure_categories', 'expenditure_subcategories',
          'expenditure_mappings', 'expenditure_rules',
          'expenditure_imports', 'expenditure_transactions']:
    count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {count} rows")

print(f"\nCategories:")
for r in conn.execute("SELECT name FROM expenditure_categories ORDER BY sort_order").fetchall():
    print(f"  {r[0]}")

print(f"\nInitial rules: {conn.execute('SELECT COUNT(*) FROM expenditure_rules').fetchone()[0]}")
conn.close()
print("\nDone.")