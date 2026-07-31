# import_transactions.py
# Processes new bank CSV files from data/imports/ folder
# Run manually or via dashboard: python3 import_transactions.py
# Supports: Ahmet Debit, Ahmet CC, Burcu Debit, Burcu CC, Turkey

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sqlite3
import pandas as pd
import re
import glob
import urllib.request
import json
from datetime import datetime
from difflib import SequenceMatcher
import config

DB_PATH    = 'data/funds.db'
IMPORT_DIR = 'data/expenditure'

# ── Map filename patterns to account names ──
# Rename your files before dropping:
#   ahmet_debit_*.csv, ahmet_cc_*.csv, burcu_debit_*.csv etc.
FILENAME_SOURCE_MAP = {
    'ahmetdebit':  'Ahmet Debit',
    'ahmetcc':     'Ahmet CC',
    'burcudebit':  'Burcu Debit',
    'burcucc':     'Burcu CC',
    'turkey':       'Turkey',
}


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


def detect_source(filename):
    base = os.path.basename(filename).lower()
    for key, source in FILENAME_SOURCE_MAP.items():
        if key in base:
            return source
    return 'Unknown'


def apply_rules(desc_clean, rules):
    """Apply keyword rules to description. Returns (category, subcategory, confidence) or None."""
    best = None
    for pattern, match_type, category, subcategory, priority in rules:
        matched = False
        p = pattern.upper()
        if match_type == 'contains':
            matched = p in desc_clean
        elif match_type == 'starts_with':
            matched = desc_clean.startswith(p)
        elif match_type == 'ends_with':
            matched = desc_clean.endswith(p)
        elif match_type == 'regex':
            matched = bool(re.search(p, desc_clean))
        if matched:
            if best is None or priority > best[3]:
                best = (category, subcategory, 1.0, priority)
    return (best[0], best[1], best[2]) if best else None


def fuzzy_match(desc_clean, mappings):
    """Find best fuzzy match from mapping table. Returns (category, subcategory, confidence)."""
    best_score = 0
    best_match = None
    for map_desc, category, subcategory in mappings:
        score = SequenceMatcher(None, desc_clean, map_desc).ratio()
        if score > best_score:
            best_score = score
            best_match = (category, subcategory, score)
    return best_match if best_score >= 0.5 else None


def apply_ai(desc_clean, categories):
    """Call Claude API to suggest category for unmatched description."""
    try:
        api_key = getattr(config, 'ANTHROPIC_API_KEY', None)
        if not api_key:
            return None
        import urllib.request, json
        prompt = (
            f"Categorise this bank transaction description into one of these categories:\n"
            f"{', '.join(categories)}\n\n"
            f"Description: {desc_clean}\n\n"
            f"Reply with ONLY: category | subcategory\n"
            f"subcategory should be the merchant/retailer name if identifiable, otherwise blank.\n"
            f"Example: Grocery | Tesco"
        )
        payload = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 50,
            "messages": [{"role": "user", "content": prompt}]
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            result = json.loads(r.read())
        text = result['content'][0]['text'].strip()
        parts = [p.strip() for p in text.split('|')]
        category   = parts[0] if parts else None
        subcategory= parts[1] if len(parts) > 1 and parts[1] else None
        if category in categories:
            return (category, subcategory, 0.7)
    except Exception:
        pass
    return None


def process_file(filepath, conn, now):
    source = detect_source(filepath)
    filename = os.path.basename(filepath)

    # Load CSV
    try:
        df = pd.read_csv(filepath, header=None,
                         names=['Date', 'Description', 'Amount'])
    except Exception as e:
        print(f"  ERROR reading {filename}: {e}")
        return

    df['Amount']   = df['Amount'].apply(parse_amount)
    df['Date']     = pd.to_datetime(df['Date'], format='%d/%m/%Y', errors='coerce')
    df             = df.dropna(subset=['Date', 'Amount'])
    df['Date']     = df['Date'].dt.strftime('%Y-%m-%d')
    df['desc_clean'] = df['Description'].apply(clean_description)

    print(f"  {filename} → {source}: {len(df)} rows")

    # Load rules and mappings
    rules = conn.execute("""
        SELECT pattern, match_type, category, subcategory, priority
        FROM expenditure_rules WHERE active = 1
        ORDER BY priority DESC
    """).fetchall()

    mappings = conn.execute("""
        SELECT description_clean, category, subcategory
        FROM expenditure_mappings
    """).fetchall()

    categories = [r[0] for r in conn.execute(
        "SELECT name FROM expenditure_categories ORDER BY sort_order"
    ).fetchall()]

    # Register import batch
    conn.execute("""
        INSERT INTO expenditure_imports
            (filename, source, import_date, row_count, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (filename, source, datetime.now().strftime('%Y-%m-%d'), len(df), now))
    conn.commit()
    import_id  = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    new_count  = 0
    skip_count = 0

    for _, row in df.iterrows():
        desc_raw   = str(row['Description']).strip()
        desc_clean = row['desc_clean']
        amount     = row['Amount']
        date       = row['Date']

        # Layer 1: Exact mapping match
        exact = conn.execute(
            "SELECT category, subcategory FROM expenditure_mappings WHERE description_clean = ?",
            (desc_clean,)
        ).fetchone()

        if exact:
            category, subcategory, mapped_by, confidence, status = (
                exact[0], exact[1], 'exact', 1.0, 'confirmed'
            )
        else:
            # Layer 2: Keyword rules
            rule_result = apply_rules(desc_clean, rules)
            if rule_result:
                category, subcategory, confidence = rule_result
                mapped_by = 'keyword'
                status    = 'confirmed'
            else:
                # Layer 3: Fuzzy match
                fuzzy = fuzzy_match(desc_clean, mappings)
                if fuzzy:
                    category, subcategory, confidence = fuzzy
                    mapped_by = 'fuzzy'
                    status    = 'confirmed' if confidence >= 0.9 else 'needs_review'
                else:
                    # Layer 4: AI
                    ai_result = apply_ai(desc_clean, categories)
                    if ai_result:
                        category, subcategory, confidence = ai_result
                        mapped_by = 'ai'
                        status    = 'needs_review'
                    else:
                        category, subcategory, confidence = None, None, 0.0
                        mapped_by = 'none'
                        status    = 'needs_review'

        try:
            conn.execute("""
                INSERT OR IGNORE INTO expenditure_transactions
                    (import_id, date, description_raw, description_clean,
                     amount, source, category, subcategory,
                     mapped_by, confidence, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (import_id, date, desc_raw, desc_clean, amount, source,
                  category, subcategory, mapped_by, confidence, status, now, now))

            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                new_count += 1
                # Update mapping use count
                if exact:
                    conn.execute(
                        "UPDATE expenditure_mappings SET use_count = use_count + 1 WHERE description_clean = ?",
                        (desc_clean,)
                    )
            else:
                skip_count += 1
        except Exception:
            skip_count += 1

    conn.execute("""
        UPDATE expenditure_imports SET new_count = ?, skip_count = ? WHERE id = ?
    """, (new_count, skip_count, import_id))
    conn.commit()
    print(f"    New: {new_count} | Skipped: {skip_count}")


def main():
    conn = sqlite3.connect(DB_PATH)
    now  = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    os.makedirs(IMPORT_DIR, exist_ok=True)

    # Find unprocessed CSV files
    files = sorted(glob.glob(os.path.join(IMPORT_DIR, '*.csv')))
    # Exclude historical file
    files = [f for f in files if 'harcamalar' not in f.lower()]

    if not files:
        print(f"No CSV files found in {IMPORT_DIR}/")
        print("Drop your bank export CSVs there with names like:")
        print("  ahmet_debit_apr2026.csv")
        print("  ahmet_cc_apr2026.csv")
        print("  burcu_debit_apr2026.csv")
        conn.close()
        return

    print(f"Found {len(files)} file(s) to process:\n")
    for f in files:
        process_file(f, conn, now)

    # Summary
    total_new  = conn.execute("SELECT SUM(new_count) FROM expenditure_imports").fetchone()[0] or 0
    total_rev  = conn.execute(
        "SELECT COUNT(*) FROM expenditure_transactions WHERE status = 'needs_review'"
    ).fetchone()[0]

    print(f"\nTotal new transactions: {total_new}")
    print(f"Needs review: {total_rev}")
    print("\nDone. Open the dashboard to review and confirm.")
    conn.close()


if __name__ == '__main__':
    main()