"""
Spending: the sums behind the Spending pages. No Dash, no SQL.

Ported from the Streamlit app that ran on port 8052 (mapper.py there). The
classification order is unchanged:

    1. Manual queue   Amazon, cash and generic PayPal lines vary per purchase,
                      so they always wait for you in the Manual Queue.
    2. Keyword rules  the first rule whose keyword appears anywhere in the
                      description wins; rules are checked in priority order.
    3. No rule        8052 filed these as Uncategorised / Other and confirmed
                      them silently. Here they are flagged in the import
                      preview, and any you do not categorise there go to the
                      Manual Queue.

Amounts are signed the way the bank file has them: spending negative, refunds
and income positive. Dates are the posting date, which is what HSBC's online
export uses (the PDF statement also shows a transaction date, a day or two
earlier - mixing the two is how the July rows were double-counted).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass

import pandas as pd

# --- Manual queue ----------------------------------------------------------

# A description equal to one of these, or starting with one of the prefixes,
# always goes to the Manual Queue. "CASH " keeps its trailing space so that a
# merchant merely starting with the letters CASH is not caught.
MANUAL_QUEUE_EXACT = ("PAYPAL PAYMENT", "PAYPAL PAYMENT DD")
MANUAL_QUEUE_PREFIXES = ("CASH ", "AMAZON", "AMZ", "AMZN")

QUEUE_GROUPS = ["Amazon", "PayPal", "Cash", "No rule"]

NO_RULE = "No rule"
MANUAL = "Manual queue"


def is_manual(description: str) -> bool:
    d = str(description).upper().strip()
    return (d in MANUAL_QUEUE_EXACT
            or any(d.startswith(p) for p in MANUAL_QUEUE_PREFIXES))


def queue_group(description: str) -> str:
    """Which Manual Queue group a pending row belongs to."""
    d = str(description).upper()
    if d.startswith("CASH") or "ATM" in d:
        return "Cash"
    if "AMAZON" in d or "AMZN" in d or "AMZ" in d:
        return "Amazon"
    if "PAYPAL" in d:
        return "PayPal"
    return NO_RULE


# --- Classification ----------------------------------------------------------

@dataclass(frozen=True)
class Match:
    category: str | None
    subcategory: str | None
    status: str          # 'confirmed' or 'pending'
    rule: str | None     # the keyword that matched, MANUAL, or None


def classify(description: str, rules) -> Match:
    """rules: (keyword, category, subcategory) in the order to check them,
    keywords upper-case - repo.spending.rule_tuples() gives exactly that."""
    if is_manual(description):
        return Match(None, None, "pending", MANUAL)
    d = str(description).upper().strip()
    for keyword, category, subcategory in rules:
        if keyword and keyword in d:
            return Match(category, subcategory, "confirmed", keyword)
    return Match(None, None, "pending", None)


def remembers(description: str) -> bool:
    """A rule is only worth saving for a merchant that recurs. Manual-queue
    lines (Amazon, cash, a bare PayPal payment) vary per purchase, so they
    are never remembered. 'PAYPAL *SHEIN' names its merchant, so it can be."""
    return not is_manual(description)


def keyword_problem(descriptions, keyword: str) -> str | None:
    """Why a keyword cannot be saved as a rule for these rows, or None.
    The keyword must appear in every one of them - otherwise the rule would
    not have matched the very rows it was made from."""
    descriptions = list(descriptions)
    if not all(remembers(d) for d in descriptions):
        return ("Amazon, PayPal and cash lines vary per purchase, so they "
                "are not remembered.")
    kw = (keyword or "").strip().upper()
    if not kw:
        return "The keyword is empty."
    for d in descriptions:
        if kw not in str(d).upper():
            return f"'{keyword.strip()}' does not appear in '{d}'."
    return None


# --- Keyword suggestion ------------------------------------------------------

# Trailing tokens worth dropping: card-type codes on debit lines, and anything
# carrying a digit (store numbers, references, phone numbers).
_CODES = {"VIS", "BP", "OBP", "DD", "DR", "CR", "SO", "ATM", ")))", "TFR",
          "CHQ", "-", "&"}


def _merchant_field(description: str) -> str:
    """Credit-card lines are fixed width: 22-char merchant, 13-char city,
    country code. Keep the merchant. Debit lines have no fixed layout."""
    d = str(description).rstrip()
    if len(d) >= 37 and d[22] == " " and d[36] == " ":
        return d[:22].rstrip()
    return d.strip()


def suggest_keyword(description: str) -> str:
    """A keyword to remember a merchant by: the merchant name without store
    numbers, references or card-type codes, upper-case.

        'TESCO STORES 6500      ELTHAM        GBR'  ->  'TESCO STORES'
        'JD WETHERSPOON Watford VIS'                ->  'JD WETHERSPOON WATFORD'
        'POUNDLAND LTD - 1884   ELTHAM        GBR'  ->  'POUNDLAND LTD'

    Internal double spaces are kept: rules match the description as it is."""
    merchant = _merchant_field(description)
    # Card-type codes only trail debit lines; on a fixed-width credit line
    # a short word like SO is part of the name ('Eltham Park So').
    codes = _CODES if merchant == str(description).strip() else {"-", "&"}
    s = merchant.upper()
    while True:
        m = re.search(r"\s+(\S+)$", s)
        if not m:
            break
        token = m.group(1)
        if token not in codes and not re.search(r"\d", token):
            break
        shorter = s[:m.start()].rstrip()
        if len(shorter) < 5:   # keep 'HSBC PREMIER543458', not 'HSBC'
            break
        s = shorter
    return s.strip()


# --- Bank files ---------------------------------------------------------------

def parse_bank_csv(raw: bytes) -> pd.DataFrame:
    """HSBC online export: no header, three columns - DD/MM/YYYY, description,
    amount (signed, thousands separators, quoted when they contain a comma).

    Returns date (YYYY-MM-DD), description, amount. Rows without a valid date
    or amount are dropped, as 8052 did."""
    text = None
    for enc in ("utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    df = pd.read_csv(io.StringIO(text), header=None, dtype=str,
                     names=["date", "description", "amount"],
                     usecols=[0, 1, 2], skip_blank_lines=True)
    df["description"] = df["description"].fillna("").str.strip()
    df["date"] = pd.to_datetime(df["date"].str.strip(), format="%d/%m/%Y",
                                errors="coerce")
    df["amount"] = pd.to_numeric(
        df["amount"].fillna("").str.replace(",", "", regex=False).str.strip(),
        errors="coerce")
    df = df.dropna(subset=["date", "amount"])
    df = df[df["description"] != ""]
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    df["amount"] = df["amount"].round(2)
    return df.reset_index(drop=True)[["date", "description", "amount"]]


# --- Comparing a file with what is already stored ----------------------------

def _key(date, description, amount):
    return (str(date), str(description), round(float(amount), 2))


def compare_with_existing(new: pd.DataFrame, existing: pd.DataFrame,
                          window_days: int = 3) -> pd.DataFrame:
    """Label each row of an uploaded file (one card):

        exists   already stored - same date, description and amount. Counted,
                 so two identical coffees on one day both survive: the file's
                 second copy is only 'exists' if the database holds two.
        near     not stored on that date, but an unclaimed stored row with the
                 same description and amount sits within window_days of it,
                 inside the file's own date range. The usual cause is a file
                 dated by a different convention (statement transaction date
                 against export posting date).
        new      neither.

    existing: the card's stored rows (date, description, amount).
    Returns new with 'state' and 'near_date' added."""
    out = new.copy()
    out["state"] = "new"
    out["near_date"] = None
    if out.empty:
        return out

    stored: dict = {}
    for r in existing.itertuples(index=False):
        k = _key(r.date, r.description, r.amount)
        stored[k] = stored.get(k, 0) + 1

    # 1. exact, counted
    for i, r in out.iterrows():
        k = _key(r["date"], r["description"], r["amount"])
        if stored.get(k, 0) > 0:
            stored[k] -= 1
            out.at[i, "state"] = "exists"

    # 2. near, only against stored rows the file should itself contain
    lo, hi = out["date"].min(), out["date"].max()
    pool: dict = {}
    for (d, desc, amt), n in stored.items():
        if n > 0 and lo <= d <= hi:
            pool.setdefault((desc, amt), []).extend([d] * n)
    if not pool:
        return out
    for i, r in out[out["state"] == "new"].iterrows():
        dates = pool.get((r["description"], round(float(r["amount"]), 2)))
        if not dates:
            continue
        here = pd.Timestamp(r["date"])
        gaps = [abs((pd.Timestamp(d) - here).days) for d in dates]
        best = min(range(len(dates)), key=lambda j: gaps[j])
        if gaps[best] <= window_days:
            out.at[i, "state"] = "near"
            out.at[i, "near_date"] = dates.pop(best)
    return out


def guess_card(new: pd.DataFrame, existing: pd.DataFrame):
    """The card whose stored rows the file overlaps most.

    existing: stored rows of every card (card, date, description, amount)
    over the file's date range. Returns (card, matching rows), or (None, 0)
    when nothing overlaps."""
    if new.empty or existing.empty:
        return None, 0
    keys = {}
    for r in new.itertuples(index=False):
        k = _key(r.date, r.description, r.amount)
        keys[k] = keys.get(k, 0) + 1
    best, best_n = None, 0
    for card, grp in existing.groupby("card"):
        have = {}
        for r in grp.itertuples(index=False):
            k = _key(r.date, r.description, r.amount)
            have[k] = have.get(k, 0) + 1
        n = sum(min(c, have.get(k, 0)) for k, c in keys.items())
        if n > best_n:
            best, best_n = card, n
    return best, best_n


# --- Overview --------------------------------------------------------------

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_table(df: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Signed amounts summed by `by` and calendar month.

    Columns: *by, Jan..Dec, Total. Spending comes out negative, as in the
    bank file; refunds within a category reduce it."""
    cols = [*by, *MONTHS, "Total"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    d = df.copy()
    d["month"] = pd.to_datetime(d["date"]).dt.month
    for c in by:
        d[c] = d[c].fillna("")
    t = d.pivot_table(index=by, columns="month", values="amount",
                      aggfunc="sum", fill_value=0.0)
    t = t.reindex(columns=range(1, 13), fill_value=0.0)
    t.columns = MONTHS
    t["Total"] = t.sum(axis=1)
    return t.reset_index()[cols]
