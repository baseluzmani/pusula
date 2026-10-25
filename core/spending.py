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

Everything is stored in pounds. A card in another currency (the Turkish
card, in lira) is converted on import at that day's rate from the prices the
portfolio already keeps - convert_to_gbp - and the lira amount is kept beside
it, which is what re-uploads are compared on.
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


# --- Several files at once ---------------------------------------------------

def combine_files(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """One set of rows from several files of the same card.

    Two exports can overlap - each is a full view of its own dates - so a
    row both carry is the same row, not two. For every date, description and
    amount the result keeps as many copies as the file with the most of
    them: a statement's two identical coffees stay two, and the same coffee
    in two overlapping exports stays one. Rows keep the order of the files."""
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return pd.DataFrame(columns=["date", "description", "amount"])
    df = pd.concat([f.assign(_file=i) for i, f in enumerate(frames)],
                   ignore_index=True)
    df["_amt"] = df["amount"].astype(float).round(2)
    keys = ["date", "description", "_amt"]
    df["_copy"] = df.groupby(["_file", *keys]).cumcount()
    df = df.drop_duplicates(subset=[*keys, "_copy"])
    return df.drop(columns=["_file", "_amt", "_copy"]).reset_index(drop=True)


# --- Other currencies ---------------------------------------------------------

_PAID_GBP = re.compile(r"^([\d,]+\.\d{2}) GBP$")


def convert_to_gbp(df: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    """Pounds for rows in another currency.

    df: date, amount (as the statement prints it) and optionally note.
    rates: date, rate - units of that currency per pound, one row a trading
    day. Each row takes the latest rate on or before its date (a weekend
    takes Friday's). A row the bank itself noted as paid in pounds ("17.52
    GBP" - a London Uber on the lira card) takes that figure instead.

    Returns df with gbp (rounded to the penny) and rate_date added. Raises
    ValueError if a row is older than the first rate."""
    out = df.copy()
    if out.empty:
        return out.assign(gbp=pd.Series(dtype=float),
                          rate_date=pd.Series(dtype=str))
    if rates is None or rates.empty:
        raise ValueError("no exchange rates stored")
    r = rates[["date", "rate"]].copy()
    r["_d"] = pd.to_datetime(r["date"])
    r = r.sort_values("_d").rename(columns={"date": "rate_date"})
    out["_d"] = pd.to_datetime(out["date"])
    out["_i"] = range(len(out))
    m = pd.merge_asof(out.sort_values("_d"), r, on="_d", direction="backward")
    m = m.sort_values("_i")
    if m["rate"].isna().any():
        first = m.loc[m["rate"].isna(), "date"].min()
        raise ValueError(f"no exchange rate on or before {first}")
    gbp = (m["amount"].astype(float) / m["rate"].astype(float)).round(2)
    if "note" in m.columns:
        paid = m["note"].fillna("").astype(str).str.strip().str.extract(
            _PAID_GBP)[0]
        paid = pd.to_numeric(paid.str.replace(",", "", regex=False),
                             errors="coerce")
        sign = m["amount"].astype(float).apply(lambda a: -1 if a < 0 else 1)
        gbp = gbp.where(paid.isna(), (paid * sign).round(2))
    out["gbp"] = gbp.to_numpy()
    out["rate_date"] = m["rate_date"].to_numpy()
    return out.drop(columns=["_d", "_i"])


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


# --- Rule preview -------------------------------------------------------------------

def preview_rule(txns: pd.DataFrame, rules: pd.DataFrame, keyword: str,
                 category: str, subcategory: str, priority: int | None = None,
                 rule_id: int | None = None) -> dict:
    """Which stored lines a rule, as typed in the form, would place.

    txns: stored rows (id, description, category, subcategory, status, ...).
    rules: the rule table (id, keyword, category, subcategory, priority).
    rule_id: the rule being edited, which the form replaces; None for a new
    rule. A blank priority keeps the edited rule's place, or puts a new rule
    at the end - the same as saving would.

    A line counts only where this rule would be the first to match: an
    earlier rule containing the same text, or the manual queue, still wins.
    Returns
        matched   the lines this rule places, with 'state' = same (already
                  this category, confirmed) / pending / differs
        beaten    {rule keyword or MANUAL: lines} that contain the keyword
                  but go to something earlier"""
    kw = (keyword or "").upper()
    empty = txns.iloc[0:0].assign(state=[])
    if not kw.strip():
        return {"matched": empty, "beaten": {}}
    others = rules[rules["id"] != rule_id] if rule_id is not None else rules
    if priority is None:
        own = rules.loc[rules["id"] == rule_id, "priority"] \
            if rule_id is not None else []
        priority = (int(own.iloc[0]) if len(own) else
                    int(rules["priority"].max()) + 1 if len(rules) else 1)
    order = [(int(p), int(i), k, c, s) for p, i, k, c, s in zip(
        others["priority"], others["id"], others["keyword"],
        others["category"], others["subcategory"])]
    order.append((int(priority), rule_id if rule_id is not None
                  else 10 ** 12, kw, category, subcategory))
    order.sort(key=lambda r: (r[0], r[1]))
    tuples = [(k, c, s) for _, _, k, c, s in order]

    hits = txns[txns["description"].str.upper().str.contains(kw, regex=False)]
    keep, beaten = [], {}
    for i, d in zip(hits.index, hits["description"]):
        m = classify(d, tuples)
        if m.rule == kw:
            keep.append(i)
        else:
            beaten[m.rule] = beaten.get(m.rule, 0) + 1
    matched = hits.loc[keep].copy()
    same = ((matched["category"] == category)
            & (matched["subcategory"] == subcategory)
            & (matched["status"] == "confirmed"))
    matched["state"] = ["same" if s else "pending" if st == "pending"
                        else "differs"
                        for s, st in zip(same, matched["status"])]
    return {"matched": matched, "beaten": beaten}


# --- Card coverage ----------------------------------------------------------------

def gaps(dates, min_days: int) -> list[tuple[str, str, int]]:
    """Stretches with no transactions on a card: (first empty day, last
    empty day, days) wherever two consecutive posting dates are more than
    min_days apart. A card used most days that goes quiet for a fortnight
    is usually a file that was never imported - the Sep 2026 credit-card
    statement was one."""
    days = sorted({pd.Timestamp(d) for d in dates})
    out = []
    for a, b in zip(days, days[1:]):
        if (b - a).days > min_days:
            first, last = a + pd.Timedelta(days=1), b - pd.Timedelta(days=1)
            out.append((first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d"),
                        (b - a).days - 1))
    return out


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
