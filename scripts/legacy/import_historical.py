# import_historical.py
# Imports historical transactions from Harcamalar_csv.csv
# Run once: python3 import_historical.py

import sqlite3
import pandas as pd
import re
from datetime import datetime

DB_PATH      = 'data/funds.db'
HISTORY_FILE = 'data/expenditure/Harcamalar_csv.csv'

SOURCE_MAP = {
    'Debit Card':   'Ahmet Debit',
    'Credit Card':  'Ahmet CC',
    'Burcu Card':   'Burcu Debit',
    'Burcu Credit': 'Burcu CC',
    'Turkey Card':  'Turkey',
}

CATEGORY_MAP = {
    'Lunch':          'Dining',
    'Food':           'Dining',
    'Pub':            'Dining',
    'Transportation': 'Transport',
    'Clothes':        'Shopping',
    'Home':           'Shopping',
    'Pharmacy':       'Health',
    'Fitness':        'Health',
    'Nursery':        'Childcare',
    'Cleaning':       'Home Services',
    'Repair':         'Home Services',
    'Tax':            'Tax & Fee',
    'Fee':            'Tax & Fee',
    'Salary':         'Income',
    'Bitcoin':        'Investment',
    'Transfer Turkey':'Transfer',
    'ATM':            'Cash',
    'Lottery':        'Entertainment',
    'Turkey':         'Transfer',
    # keep as-is
    'Grocery':        'Grocery',
    'Entertainment':  'Entertainment',
    'Utilities':      'Utilities',
    'Subscription':   'Subscription',
    'Mortgage':       'Mortgage',
    'Investment':     'Investment',
    'Transfer':       'Transfer',
    'Cash':           'Cash',
    'Amazon':         'Amazon',
    'PAYPAL':         'PAYPAL',
    'Holiday':        'Holiday',
    'Electronics':    'Electronics',
    'Health':         'Health',
}

# Auto-fill subcategory from description when missing
# (pattern, category_applies_to, subcategory)
# category_applies_to = None means applies to all
AUTO_SUBCATEGORY_RULES = [
    # Utilities
    ('EDF',          'Utilities',     'Electric'),
    ('BRITISH GAS',  'Utilities',     'Gas'),
    ('THAMES WATER', 'Utilities',     'Water'),
    ('VIRGIN MEDIA', 'Utilities',     'Broadband'),
    ('SKY',          'Utilities',     'Broadband'),
    ('BT ',          'Utilities',     'Broadband'),
    ('O2 ',          'Utilities',     'Mobile'),
    ('VODAFONE',     'Utilities',     'Mobile'),
    ('EE ',          'Utilities',     'Mobile'),
    ('THREE',        'Utilities',     'Mobile'),
    # Subscription
    ('NETFLIX',      'Subscription',  'Streaming'),
    ('SPOTIFY',      'Subscription',  'Streaming'),
    ('DISNEY',       'Subscription',  'Streaming'),
    ('APPLE.COM',    'Subscription',  'Apple'),
    ('AMAZON PRIME', 'Subscription',  'Amazon Prime'),
    ('SPECTATOR',    'Subscription',  'Magazine'),
    ('TIMES ',       'Subscription',  'Magazine'),
    ('GUARDIAN',     'Subscription',  'Magazine'),
    # Food/Dining
    ('MCDONALDS',    'Dining',        'McDonalds'),
    ('DOMINO',       'Dining',        'Takeaway'),
    ('DELIVEROO',    'Dining',        'Takeaway'),
    ('JUST EAT',     'Dining',        'Takeaway'),
    ('UBER EATS',    'Dining',        'Takeaway'),
    ('KFC',          'Dining',        'KFC'),
    ('GREGGS',       'Dining',        'Greggs'),
    ('WASABI',       'Dining',        'Wasabi'),
    ('PRET',         'Dining',        'Pret'),
    ('COSTA',        'Dining',        'Costa'),
    ('STARBUCKS',    'Dining',        'Starbucks'),
    ('NANDOS',       'Dining',        'Nandos'),
    ('PIZZA',        'Dining',        'Pizza'),
    ('SUBWAY',       'Dining',        'Subway'),
    ('TAKEAWAY',     'Dining',        'Takeaway'),
    # Transport
    ('TFL',          'Transport',     'TFL'),
    ('OYSTER',       'Transport',     'TFL'),
    ('UBER',         'Transport',     'Uber'),
    ('TRAINLINE',    'Transport',     'Train'),
    ('NATIONAL RAIL','Transport',     'Train'),
    ('NATIONAL EXPRESS','Transport',  'Coach'),
    ('EASYJET',      'Transport',     'Flight'),
    ('RYANAIR',      'Transport',     'Flight'),
    ('BRITISH AIRWAYS','Transport',   'Flight'),
    ('EUROSTAR',     'Transport',     'Train'),
    # Tax & Fee
    ('HMRC',         'Tax & Fee',     'HMRC'),
    ('ROYAL GREENWICH','Tax & Fee',   'Council Tax'),
    ('TVLICENSING',  'Tax & Fee',     'TV License'),
    ('PASSPORT',     'Tax & Fee',     'Passport'),
    ('DVSA',         'Tax & Fee',     'Driving'),
    ('INTEREST',     'Tax & Fee',     'Interest'),
    ('NON-STERLING', 'Tax & Fee',     'FX Fee'),
    ('HSBC',         'Tax & Fee',     'Bank Fee'),
    # Income
    ('HMRC PAYE',    'Income',        'Salary'),
    ('HMRC SA',      'Income',        'HMRC'),
    ('JONES LANG',   'Income',        'Salary'),
    # Investment
    ('ROYAL MINT',   'Investment',    'Gold'),
    ('HL.CO.UK',     'Investment',    'Hargreaves'),
    ('HARGREAVES',   'Investment',    'Hargreaves'),
    ('PENSION',      'Investment',    'Pension'),
    ('INTERACTIVE',  'Investment',    'Broker'),
    # Health
    ('PHARMACY',     'Health',        'Pharmacy'),
    ('PHARM',        'Health',        'Pharmacy'),
    ('HOLLAND',      'Health',        'Supplements'),
    ('RANDOX',       'Health',        'Health Test'),
    ('DENTIST',      'Health',        'Dental'),
    ('DENTAL',       'Health',        'Dental'),
    ('GYM',          'Health',        'Gym'),
    # Mortgage
    ('MTG',          'Mortgage',      'Mortgage'),
    # Shopping
    ('TK MAXX',      'Shopping',      'TK Maxx'),
    ('NEXT ',        'Shopping',      'Next'),
    ('H&M',          'Shopping',      'H&M'),
    ('ZARA',         'Shopping',      'Zara'),
    ('ASOS',         'Shopping',      'ASOS'),
    ('SHEIN',        'Shopping',      'Shein'),
    ('AMAZON',       'Amazon',        None),   # Amazon stays blank
    # Cash
    ('CASH',         'Cash',          'ATM'),
    ('ATM',          'Cash',          'ATM'),
    # Transfer
    ('INTERNET TRANSFER', 'Transfer', 'Internal'),
    ('FASTER PAYMENT','Transfer',     'Internal'),
    ('CARD PYMT',    'Transfer',      'Internal'),
    # Dining — additional
    ('WASABI',       'Dining', 'Wasabi'),
    ('GREGGS',       'Dining', 'Greggs'),
    ('MCDONALDS',    'Dining', 'McDonalds'),
    ('VUE',          'Dining', 'Cinema'),   # actually Entertainment
    ('NATIONAL LOTTERY', 'Entertainment', 'Lottery'),
    ('POSTCODE LOTTERY',  'Entertainment', 'Lottery'),
    ('CINEMA',       'Entertainment', 'Cinema'),
    # Shopping
    ('TK MAXX',      'Shopping', 'TK Maxx'),
    ('NEXT ',        'Shopping', 'Next'),
    ('BOOTS',        'Shopping', 'Boots'),
    ('WHITTARD',     'Shopping', 'Whittard'),
    # Tax & Fee  
    ('AIL - HSBC',   'Tax & Fee', 'Bank Fee'),
    ('NON-STERLING', 'Tax & Fee', 'FX Fee'),
    ('ROYAL MAIL',   'Tax & Fee', 'Postage'),
    # Income
    ('HMRC PAYE CR', 'Income', 'Salary'),
    ('HMRC SA CR',   'Income', 'HMRC'),
    ('FIL INVESTMENT','Income', 'Dividends'),
    ('COMPUTERSHARE', 'Income', 'Dividends'),
    # Investment  
    ('II INVESTMENT', 'Investment', 'Interactive Investor'),
    ('II SIPP',       'Investment', 'SIPP'),
    ('INTERACTIVE INV','Investment', 'Interactive Investor'),
]


def clean_description(desc):
    if not desc or pd.isna(desc):
        return ''
    d = str(desc).strip()
    d = re.sub(r'\s{2,}[A-Z][A-Za-z\s]+\s{2,}[A-Z]{2,3}\s*$', '', d)
    d = re.sub(r'\*+', ' ', d)
    d = re.sub(r'\s+', ' ', d)
    d = re.sub(r'\s+\d{4,}\s*$', '', d)
    return d.strip().upper()


def parse_amount(amt_str):
    if pd.isna(amt_str):
        return None
    s = str(amt_str).strip().replace(',', '')
    if s.startswith('(') and s.endswith(')'):
        return -float(s[1:-1])
    try:
        return float(s)
    except:
        return None


def auto_fill_subcategory(desc_clean, category):
    """Try to fill subcategory from description using rules."""
    for pattern, cat_filter, subcategory in AUTO_SUBCATEGORY_RULES:
        if cat_filter and cat_filter != category:
            continue
        if pattern.upper() in desc_clean:
            return subcategory
    return None


conn = sqlite3.connect(DB_PATH)
now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

print("Loading historical data...")
df = pd.read_csv(HISTORY_FILE, encoding='utf-8-sig', low_memory=False)
df = df.rename(columns={
    'Merchant/Description': 'Description',
    ' Debit/Credit ':       'Amount',
    'Type':                 'Category',
})
df = df[['Date','Source','Description','Amount','Category','Retailer']]
df = df.dropna(subset=['Date','Description'])
df['Amount']     = df['Amount'].apply(parse_amount)
df['Source']     = df['Source'].map(SOURCE_MAP).fillna(df['Source'])
df['Date']       = pd.to_datetime(df['Date'], format='%d/%m/%Y', errors='coerce')
df               = df.dropna(subset=['Date','Amount'])
df['Date']       = df['Date'].dt.strftime('%Y-%m-%d')
df['desc_clean'] = df['Description'].apply(clean_description)

# Map old categories to new
df['new_category'] = df['Category'].map(CATEGORY_MAP).fillna(df['Category'])

# Auto-fill missing subcategories
def get_subcategory(row):
    if pd.notna(row['Retailer']) and str(row['Retailer']).strip() not in ('', 'nan'):
        return str(row['Retailer']).strip()
    return auto_fill_subcategory(row['desc_clean'], row['new_category'])

df['final_subcategory'] = df.apply(get_subcategory, axis=1)

# Stats
total     = len(df)
filled    = df['final_subcategory'].notna().sum()
still_missing = total - filled
print(f"  Loaded: {total} rows")
print(f"  Subcategory filled: {filled} ({filled/total*100:.0f}%)")
print(f"  Still missing: {still_missing} ({still_missing/total*100:.0f}%)")
print(f"  Date range: {df['Date'].min()} → {df['Date'].max()}")

# Register import batch
conn.execute("""
    INSERT INTO expenditure_imports
        (filename, source, import_date, row_count, created_at)
    VALUES ('Harcamalar_csv.csv', 'historical', ?, ?, ?)
""", (datetime.now().strftime('%Y-%m-%d'), total, now))
conn.commit()
import_id  = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

# Insert transactions
print("\nInserting transactions...")
new_count  = 0
skip_count = 0

for _, row in df.iterrows():
    try:
        conn.execute("""
            INSERT OR IGNORE INTO expenditure_transactions
                (import_id, date, description_raw, description_clean,
                 amount, source, category, subcategory,
                 mapped_by, confidence, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'manual', 1.0, 'confirmed', ?, ?)
        """, (
            import_id,
            row['Date'],
            str(row['Description']).strip(),
            row['desc_clean'],
            row['Amount'],
            row['Source'],
            row['new_category'],
            row['final_subcategory'],
            now, now
        ))
        if conn.execute("SELECT changes()").fetchone()[0] > 0:
            new_count += 1
        else:
            skip_count += 1
    except Exception:
        skip_count += 1

conn.execute("""
    UPDATE expenditure_imports SET new_count = ?, skip_count = ? WHERE id = ?
""", (new_count, skip_count, import_id))
conn.commit()
print(f"  Inserted: {new_count} | Skipped: {skip_count}")

# Extract mapping table
print("\nExtracting mapping table...")
mapping_df = df.dropna(subset=['new_category']).copy()
mapping_df = mapping_df.groupby('desc_clean').agg(
    category   = ('new_category',       lambda x: x.mode()[0]),
    subcategory= ('final_subcategory',  lambda x: x.dropna().mode()[0] if len(x.dropna()) > 0 else None),
    use_count  = ('new_category',       'count')
).reset_index()

mapping_count = 0
for _, row in mapping_df.iterrows():
    if not row['desc_clean']:
        continue
    conn.execute("""
        INSERT OR REPLACE INTO expenditure_mappings
            (description_clean, category, subcategory,
             match_type, confidence, use_count, updated_at)
        VALUES (?, ?, ?, 'exact', 1.0, ?, ?)
    """, (row['desc_clean'], row['category'], row['subcategory'],
          int(row['use_count']), now))
    mapping_count += 1

# Extract subcategory autocomplete
print("Extracting subcategory history...")
sub_df = df[df['final_subcategory'].notna()].copy()
sub_df = sub_df.groupby(['new_category','final_subcategory']).size().reset_index(name='use_count')
sub_count = 0
for _, row in sub_df.iterrows():
    conn.execute("""
        INSERT OR REPLACE INTO expenditure_subcategories
            (category, subcategory, use_count)
        VALUES (?, ?, ?)
    """, (row['new_category'], row['final_subcategory'], int(row['use_count'])))
    sub_count += 1

conn.commit()
conn.close()

print(f"  Mapping entries: {mapping_count}")
print(f"  Subcategory entries: {sub_count}")
print("\nDone. Historical data imported successfully.")