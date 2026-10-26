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

Rules can carry an amount condition ("PAYPAL PAYMENT DD", exactly £12.99;
"ROYAL BOROUGH", up to £20). Only such a rule can place a manual-queue line:
a bare PayPal payment is anonymous, but the same amount every year is the
same subscription.

A line can be split into parts, each with its own category (£200 cash: £120
cleaning, the rest tutor). The bank line stays whole - statements reconcile
on it - and everything that sums by category uses the parts
(expand_splits). Split templates fill the parts in one click.

Statements are the source of truth from January 2025: match_statement pairs
each statement line with what is stored, so a category you set by hand
survives a reload.

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


def squash(text) -> str:
    """Runs of spaces as one: rules and statement matching ignore how many
    spaces a bank put between words ('UBER   *EATS' = 'UBER *EATS')."""
    return re.sub(r"\s+", " ", str(text))


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
    rule: str | None     # label of the rule that matched, MANUAL, or None
    note: str | None = None      # the rule's note, filled into the line
    key: tuple | None = None     # rule_key() of the rule that matched


AMOUNT_SIGNS = {"exact": "=", "max": "≤"}


def rule_key(keyword, amount_kind=None, amount=None) -> tuple:
    """What makes a rule unique: keyword plus its amount condition."""
    if not amount_kind:
        return (str(keyword).upper(), None, None)
    return (str(keyword).upper(), amount_kind, round(abs(float(amount)), 2))


def rule_label(keyword, amount_kind=None, amount=None) -> str:
    """'TESCO', 'PAYPAL PAYMENT DD = 12.99', 'ROYAL BOROUGH ≤ 20.00'."""
    if not amount_kind:
        return str(keyword)
    return f"{keyword} {AMOUNT_SIGNS[amount_kind]} {abs(float(amount)):,.2f}"


def amount_fits(amount_kind, limit, amount) -> bool:
    """Whether a line's amount meets a rule's condition. Compared without
    sign: a £20 refund fits '≤ 20' as a £20 payment does."""
    if not amount_kind:
        return True
    if amount is None or limit is None:
        return False
    a, lim = abs(float(amount)), abs(float(limit))
    if amount_kind == "exact":
        return abs(a - lim) < 0.005
    if amount_kind == "max":
        return a <= lim + 0.005
    return False


def _rule_parts(rule):
    """(keyword, category, subcategory, amount_kind, amount, note) from a
    plain (keyword, category, subcategory) or a full rule tuple."""
    r = tuple(rule)
    return (r + (None,) * 6)[:6]


def classify(description: str, rules, amount=None) -> Match:
    """rules: in the order to check them, keywords upper-case - plain
    (keyword, category, subcategory) or with amount_kind, amount and note
    after them, as repo.spending.rule_tuples() gives.

    The first rule whose keyword is in the description, and whose amount
    condition (if it has one) the line meets, wins. A keyword-only rule
    never places a manual-queue line; a rule with an amount does. Spacing
    does not matter: 'UBER   *EATS' matches 'UBER *EATS'."""
    d = squash(str(description).upper().strip())
    manual = is_manual(description)
    for rule in rules:
        kw, cat, sub, kind, limit, note = _rule_parts(rule)
        if not kw or squash(kw) not in d:
            continue
        if kind:
            if not amount_fits(kind, limit, amount):
                continue
        elif manual:
            continue
        return Match(cat, sub, "confirmed", rule_label(kw, kind, limit),
                     note or None, rule_key(kw, kind, limit))
    if manual:
        return Match(None, None, "pending", MANUAL)
    return Match(None, None, "pending", None)


def remembers(description: str) -> bool:
    """Whether a line can be remembered by keyword alone. Manual-queue
    lines (Amazon, cash, a bare PayPal payment) vary per purchase, so they
    are remembered with their amount instead - see remember_plan.
    'PAYPAL *SHEIN' names its merchant, so a keyword does."""
    return not is_manual(description)


def manual_keyword(description: str) -> str | None:
    """The queue pattern a manual-queue line is caught by - 'PAYPAL PAYMENT
    DD', 'CASH', 'AMAZON' - or None for any other line."""
    d = str(description).upper().strip()
    if d in MANUAL_QUEUE_EXACT:
        return d
    for p in MANUAL_QUEUE_PREFIXES:
        if d.startswith(p):
            return p.strip()
    return None


def remember_keyword(description: str) -> str:
    """The keyword Remember offers for a line."""
    return manual_keyword(description) or suggest_keyword(description)


def remember_plan(rows, keyword: str):
    """How Remember saves a rule for the ticked rows: (plan, problem).

    rows: dicts with description and amount. Ordinary lines give a keyword
    rule - the keyword must appear in every one. Amazon, PayPal and cash
    lines give the keyword with the exact amount, so they must share one.
    plan: {'keyword', 'amount_kind', 'amount'}."""
    rows = list(rows)
    kw = (keyword or "").strip().upper()
    if not rows:
        return None, "Tick at least one row first."
    if not kw:
        return None, "The keyword is empty."
    for r in rows:
        if squash(kw) not in squash(str(r["description"]).upper()):
            return None, (f"'{keyword.strip()}' does not appear in "
                          f"'{r['description']}'.")
    manual = [is_manual(r["description"]) for r in rows]
    if not any(manual):
        return {"keyword": kw, "amount_kind": None, "amount": None}, None
    if not all(manual):
        return None, ("Amazon, PayPal and cash lines are remembered with their "
                      "amount - tick them apart from other lines.")
    amounts = {round(abs(float(r["amount"])), 2) for r in rows}
    if len(amounts) != 1:
        return None, ("Amazon, PayPal and cash lines are remembered with their "
                      "amount - tick lines of one amount.")
    return {"keyword": kw, "amount_kind": "exact",
            "amount": amounts.pop()}, None


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
        if squash(kw) not in squash(str(d).upper()):
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


# --- Splits -------------------------------------------------------------------

def parse_part_amount(text) -> tuple[str, float | None]:
    """A split part's amount box: '120' -> ('fixed', 120.0), '40%' ->
    ('percent', 40.0), blank -> ('rest', None) - the part that takes what
    is left. Raises ValueError for anything else."""
    t = str(text if text is not None else "").strip().replace(",", "")
    t = t.replace("£", "")
    if not t:
        return "rest", None
    if t.endswith("%"):
        v = float(t[:-1])
        if not 0 < v < 100:
            raise ValueError(f"{text} is not a share between 0% and 100%")
        return "percent", v
    v = abs(float(t))
    if v == 0:
        raise ValueError("a part cannot be 0")
    return "fixed", v


def part_text(kind: str, value) -> str:
    """The amount box for a part: the reverse of parse_part_amount."""
    if kind == "rest" or value is None:
        return ""
    if kind == "percent":
        return f"{float(value):g}%"
    return f"{abs(float(value)):.2f}"


def resolve_parts(amount: float, parts: list[dict]) -> list[dict]:
    """Split parts with pounds filled in, signed like the line.

    parts: dicts with category, subcategory, note, kind ('fixed', 'percent'
    or 'rest') and value (unsigned pounds, or a percentage). Fixed parts
    take their value, percent parts their share of the line, and the one
    'rest' part what is left. Raises ValueError when they cannot add up to
    the line - so a template made for £200 cannot silently split £150."""
    total = round(abs(float(amount)), 2)
    sign = -1 if float(amount) < 0 else 1
    rest = [i for i, p in enumerate(parts) if p.get("kind") == "rest"]
    if len(rest) > 1:
        raise ValueError("only one part can take what is left")
    out, used = [], 0.0
    for p in parts:
        kind = p.get("kind") or "fixed"
        if kind == "fixed":
            v = round(abs(float(p["value"])), 2)
        elif kind == "percent":
            v = round(total * float(p["value"]) / 100, 2)
        else:
            v = None
        out.append({**p, "kind": kind, "amount": v})
        used += v or 0.0
    used = round(used, 2)
    if rest:
        left = round(total - used, 2)
        if left <= 0:
            raise ValueError(f"the other parts already take {used:,.2f} of "
                             f"{total:,.2f}")
        out[rest[0]]["amount"] = left
    elif abs(used - total) >= 0.005:
        raise ValueError(f"the parts add up to {used:,.2f}, the line is "
                         f"{total:,.2f}")
    for p in out:
        p["amount"] = round(sign * p["amount"], 2)
    return out


def check_split(amount: float, parts: list[dict]) -> str | None:
    """Why these parts cannot be saved as a split of a line, or None.
    parts: dicts with amount (signed like the line), category and
    subcategory."""
    if len(parts) < 2:
        return "A split needs at least two parts."
    for i, p in enumerate(parts, 1):
        if not (p.get("category") and p.get("subcategory")):
            return f"Part {i} needs a category and sub-category."
        a = float(p.get("amount") or 0)
        if a == 0:
            return f"Part {i} is 0."
        if (a < 0) != (float(amount) < 0):
            return f"Part {i} has the wrong sign for this line."
    total = round(sum(float(p["amount"]) for p in parts), 2)
    if abs(total - round(float(amount), 2)) >= 0.005:
        return (f"The parts add up to {total:,.2f}, the line is "
                f"{float(amount):,.2f}.")
    return None


def expand_splits(txns: pd.DataFrame, splits: pd.DataFrame) -> pd.DataFrame:
    """Rows as anything summing by category should see them: a split line
    replaced by its parts - same id, date, card, description and status,
    each part's amount, category, sub-category and note. split_of is the
    line's id on a part, None elsewhere.

    splits: transaction_id, part, amount, category, subcategory, note."""
    out = txns.copy()
    out["split_of"] = None
    if txns.empty or splits is None or splits.empty:
        return out
    split_ids = set(splits["transaction_id"])
    whole = out[~out["id"].isin(split_ids)]
    lines = out[out["id"].isin(split_ids)].drop(
        columns=["amount", "category", "subcategory", "note", "split_of"],
        errors="ignore")
    if lines.empty:
        return whole
    parts = splits.rename(columns={"transaction_id": "id"})[
        ["id", "part", "amount", "category", "subcategory", "note"]]
    merged = lines.merge(parts, on="id").sort_values(["id", "part"])
    merged["split_of"] = merged["id"]
    merged = merged.drop(columns=["part"])
    return pd.concat([whole, merged[whole.columns]], ignore_index=True)


# --- Suggestions from your own history -------------------------------------------

def history_key(description: str, amount) -> tuple:
    """What two lines must share to count as 'the same again'. Cash and
    Amazon descriptions carry dates, times and order codes, so for them
    the amount alone; for everything else description and amount."""
    g = queue_group(description)
    amt = round(abs(float(amount)), 2)
    if g in ("Cash", "Amazon"):
        return (g, "", amt)
    return (g, str(description).upper().strip(), amt)


def history_index(history: pd.DataFrame) -> dict:
    """history (confirmed lines: date, description, amount, category,
    subcategory, note, template) -> {history_key: [rows]}, newest first."""
    idx: dict = {}
    if history is None or history.empty:
        return idx
    h = history.sort_values("date", ascending=False)
    for r in h.to_dict("records"):
        idx.setdefault(history_key(r["description"], r["amount"]),
                       []).append(r)
    return idx


def suggestions(description: str, amount, index: dict,
                limit: int = 3) -> list[dict]:
    """How you placed lines like this one before, most often first: dicts
    with category, subcategory, note, template (a split template id, for
    a line you split with one), count and last (date)."""
    seen: dict = {}
    for r in index.get(history_key(description, amount), []):
        tpl = r.get("template")
        tpl = None if tpl is None or tpl != tpl else int(tpl)
        if tpl is None and not r.get("category"):
            continue
        note = r.get("note")
        note = None if note is None or note != note or not note else note
        k = (("template", tpl) if tpl is not None else
             (r["category"], r["subcategory"], note))
        if k not in seen:
            seen[k] = {"category": None if tpl else r["category"],
                       "subcategory": None if tpl else r["subcategory"],
                       "note": note if not tpl else None, "template": tpl,
                       "count": 0, "last": r["date"]}
        seen[k]["count"] += 1
    out = sorted(seen.values(), key=lambda s: str(s["last"]), reverse=True)
    out.sort(key=lambda s: -s["count"])          # stable: newest first within
    return out[:limit]


def top_categories(history: pd.DataFrame, group: str, n: int = 6) -> list:
    """The (category, sub-category) pairs you use most for a queue group -
    the quick buttons when reviewing it."""
    if history is None or history.empty:
        return []
    h = history[history["category"].notna()]
    h = h[h["description"].map(queue_group) == group]
    counts = h.groupby(["category", "subcategory"]).size()
    return [tuple(k) for k in counts.sort_values(ascending=False)
            .index[:n]]


# --- Statements --------------------------------------------------------------------

def _day(d) -> pd.Timestamp:
    return pd.Timestamp(str(d)[:10])


PASSES = ("exact", "shop", "sign", "reworded", "near")

# How far a stored amount may be from the statement's and still be the same
# purchase: the 8052 history kept amounts to the nearest 10p.
AMOUNT_SLACK = 0.05


def core_text(description) -> str:
    """A description reduced to the shop: upper case, single spaces, commas
    as spaces (the export drops them), without the ')))' or 'IAP' a
    statement prints in front or an amount the old history stuck on the end
    ('))) TESCO STORES 6500 ELTHAM 4.60' -> 'TESCO STORES 6500 ELTHAM')."""
    s = squash(str(description).upper().replace(",", " ")).strip()
    for p in (")))", "IAP"):
        if s.startswith(p):
            s = s[len(p):].strip()
    return re.sub(r"\s+[\d,]+\.\d{2}$", "", s).strip()


def same_shop(a, b) -> bool:
    """Two descriptions name the same shop: equal once reduced to it, or one
    the start of the other (history often dropped the city or country)."""
    ca, cb = core_text(a), core_text(b)
    if not ca or not cb:
        return False
    return ca == cb or ca.startswith(cb) or cb.startswith(ca)


def extra_wording(stored, statement) -> bool:
    """Whether a stored description says more than the statement's - a note
    you typed into it ('PAYPAL PAYMENT DD - VPN') - rather than only
    spacing, ')))', a trailing amount or a dropped city."""
    cs, cl = core_text(stored), core_text(statement)
    return not (cs == cl or cl.startswith(cs))


def match_statement(lines: pd.DataFrame, stored: pd.DataFrame, window,
                    days: int = 3, passes=PASSES) -> dict:
    """Pair a statement's lines with the card's stored rows.

    lines: date, description, amount as printed; the index numbers them.
    stored: id, date, description, amount as printed - rows already linked
    to another statement left out by the caller.
    window: (from, to), the dates the statement covers.

    The statement is right about every amount to the penny; the pairing
    only has to find which stored row is the same purchase. Four passes,
    each over what the one before left:
        exact     same date, description and amount (counted, so two
                  identical coffees pair with two stored ones); spacing
                  inside the description does not count
        shop      the same shop (same_shop), amounts up to 5p apart, dates
                  up to `days` apart - the 8052 history rounded amounts to
                  10p and sometimes kept ')))' and the amount in the text
        sign      the same, but the stored line has the opposite sign - the
                  history stored some refunds and incoming payments as
                  spending
        reworded  same date and amount, different description
        near      amounts up to 5p apart, dates up to `days` apart, same
                  first word
    passes: which of those to run - several statements are matched exact
    first, all of them, so one statement's loose pairing cannot take a row
    another statement has word for word.
    Returns {'pairs': [(line index, stored id, kind)], 'new': [line index],
    'leftover': [stored id]} - leftover being stored rows inside the
    window that pair with no line: on no statement, so not real."""
    pairs = []
    todo = [(i, str(r["date"])[:10], squash(r["description"]).strip(),
             round(float(r["amount"]), 2)) for i, r in lines.iterrows()]
    free = [(int(r["id"]), str(r["date"])[:10],
             squash(r["description"]).strip(),
             round(float(r["amount"]), 2)) for _, r in stored.iterrows()]

    def take(test, kind, closest=False):
        nonlocal todo
        left = []
        for line in todo:
            options = [s for s in free if test(line, s)]
            if not options:
                left.append(line)
                continue
            if closest:
                options.sort(key=lambda s: (
                    abs((_day(s[1]) - _day(line[1])).days),
                    abs(s[3] - line[3])))
            s = options[0]
            free.remove(s)
            pairs.append((line[0], s[0], kind))
        todo = left

    def near(a, b):
        return abs((_day(a) - _day(b)).days) <= days

    def close(a, b):
        return (a < 0) == (b < 0) and abs(a - b) <= AMOUNT_SLACK + 1e-9

    if "exact" in passes:
        take(lambda l, s: (l[1], l[2], l[3]) == (s[1], s[2], s[3]), "exact")
    if "shop" in passes:
        take(lambda l, s: (close(l[3], s[3]) and near(l[1], s[1])
                           and same_shop(l[2], s[2])), "shop", closest=True)
    if "sign" in passes:
        take(lambda l, s: (close(l[3], -s[3]) and near(l[1], s[1])
                           and same_shop(l[2], s[2])), "sign", closest=True)
    if "reworded" in passes:
        take(lambda l, s: l[1] == s[1] and l[3] == s[3], "reworded")
    if "near" in passes:
        take(lambda l, s: (close(l[3], s[3]) and near(l[1], s[1])
                           and core_text(l[2]).split()[:1]
                           == core_text(s[2]).split()[:1]),
             "near", closest=True)
    lo, hi = window
    leftover = [s[0] for s in free if lo <= s[1] <= hi]
    return {"pairs": pairs, "new": [t[0] for t in todo],
            "leftover": leftover}


def match_statements(batch: list, stored: pd.DataFrame,
                     days: int = 3) -> list[dict]:
    """Several statements of one card against its stored rows.

    batch: [(lines, window)] as match_statement takes them. Every
    statement is matched word for word first, then by the looser passes,
    so a pairing never steals a row another statement has exactly. Rows
    inside a window that nothing paired are left over - each to the first
    statement whose window holds it. Returns one {'pairs', 'new',
    'leftover'} per statement, in batch order."""
    claimed: set = set()
    out = [{"pairs": [], "new": [], "leftover": []} for _ in batch]
    todo = [lines for lines, _ in batch]
    for passes in (PASSES[:1], PASSES[1:]):
        for k, (_, window) in enumerate(batch):
            free = stored[~stored["id"].isin(claimed)]
            res = match_statement(todo[k], free, window, days, passes)
            out[k]["pairs"] += res["pairs"]
            claimed |= {sid for _, sid, _ in res["pairs"]}
            todo[k] = todo[k].loc[res["new"]]
    dates = stored["date"].astype(str).str[:10] if not stored.empty \
        else pd.Series(dtype=str)
    for k, (_, (lo, hi)) in enumerate(batch):
        out[k]["new"] = list(todo[k].index)
        mask = (~stored["id"].isin(claimed)) & (dates >= lo) & (dates <= hi)
        ids = [int(i) for i in stored.loc[mask, "id"]]
        out[k]["leftover"] = ids
        claimed |= set(ids)
    return out


def statement_checks(st: dict, linked: pd.DataFrame,
                     previous: dict | None = None, unlinked: int = 0) -> dict:
    """How a stored statement stands.

    st / previous: statement records (debits, credits, previous_balance,
    new_balance, period_from, period_to); previous is the card's statement
    before it, or None. linked: the stored rows on it, amount as printed.
    unlinked: rows dated inside its period that are on no statement.

    lines_ok   stored lines add up to the statement's debits and credits
    chain_ok   its opening balance is the previous statement's closing one
               (None when there is no previous statement right before it)
    ok         all of those, and nothing unlinked inside the period"""
    amt = linked["amount"] if not linked.empty else pd.Series(dtype=float)
    deb = round(-amt[amt < 0].sum(), 2)
    cre = round(amt[amt > 0].sum(), 2)
    lines_ok = (st.get("debits") is not None and st.get("credits") is not None
                and abs(deb - st["debits"]) < 0.005
                and abs(cre - st["credits"]) < 0.005)
    chain_ok = None
    if previous is not None and contiguous(previous, st):
        a, b = previous.get("new_balance"), st.get("previous_balance")
        if a is not None and b is not None:
            chain_ok = abs(a - b) < 0.005
    return {"lines_debits": deb, "lines_credits": cre, "lines": len(linked),
            "lines_ok": bool(lines_ok), "chain_ok": chain_ok,
            "unlinked": int(unlinked),
            "ok": bool(lines_ok) and chain_ok is not False
            and not unlinked}


def contiguous(a: dict, b: dict, slack: int = 3) -> bool:
    """Whether statement b starts where a ended (a day later; a few days'
    slack for statements dated by transaction date)."""
    gap = (_day(b["period_from"]) - _day(a["period_to"])).days
    return -slack <= gap <= 1 + slack


def missing_periods(statements: pd.DataFrame, slack: int = 3) -> list[dict]:
    """Holes between a card's statements: {card, from, to} wherever a
    statement does not start where the one before it ended."""
    out = []
    if statements is None or statements.empty:
        return out
    for card, grp in statements.sort_values("period_to").groupby("card"):
        recs = grp.to_dict("records")
        for a, b in zip(recs, recs[1:]):
            gap = (_day(b["period_from"]) - _day(a["period_to"])).days
            if gap > 1 + slack:
                out.append({"card": card, "from": (_day(a["period_to"])
                            + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                            "to": (_day(b["period_from"])
                                   - pd.Timedelta(days=1)).strftime("%Y-%m-%d")})
    return out


def month_span(first: str, last: str) -> list[str]:
    """'2025-11', '2026-02' -> ['2025-11', '2025-12', '2026-01', '2026-02']."""
    out, d = [], pd.Period(first, "M")
    while d <= pd.Period(last, "M"):
        out.append(str(d))
        d += 1
    return out


def coverage(months: list[str], statements: pd.DataFrame,
             missing: list[dict], unlinked_months: pd.DataFrame) -> dict:
    """{card: {month: (state, statement id)}} for the coverage grid.

    statements: card, id, period_to, ok. A month shows the statement that
    ends in it ('ok' or 'break'); otherwise 'missing' when a hole between
    statements covers it; otherwise 'provisional' when rows on no statement
    are dated in it (CSV rows waiting for their statement); otherwise
    nothing."""
    grid: dict = {}
    if statements is not None and not statements.empty:
        for r in statements.to_dict("records"):
            m = str(r["period_to"])[:7]
            grid.setdefault(r["card"], {})[m] = (
                "ok" if r["ok"] else "break", int(r["id"]))
    for gap in missing:
        for m in month_span(gap["from"][:7], gap["to"][:7]):
            grid.setdefault(gap["card"], {}).setdefault(m, ("missing", None))
    if unlinked_months is not None and not unlinked_months.empty:
        for r in unlinked_months.to_dict("records"):
            grid.setdefault(r["card"], {}).setdefault(
                r["month"], ("provisional", None))
    return {card: {m: v for m, v in cells.items() if m in months}
            for card, cells in grid.items()}


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
                 rule_id: int | None = None, amount_kind: str | None = None,
                 amount: float | None = None) -> dict:
    """Which stored lines a rule, as typed in the form, would place.

    txns: stored rows (id, description, amount, category, subcategory,
    status, ...) - split lines left out by the caller.
    rules: the rule table (id, keyword, category, subcategory, priority,
    and amount_kind / amount if it has them).
    rule_id: the rule being edited, which the form replaces; None for a new
    rule. A blank priority keeps the edited rule's place, or puts a new rule
    at the end - the same as saving would. amount_kind / amount: the form's
    amount condition, if any.

    A line counts only where this rule would be the first to match: an
    earlier rule containing the same text, or the manual queue, still wins.
    Returns
        matched   the lines this rule places, with 'state' = same (already
                  this category, confirmed) / pending / differs
        beaten    {rule label or MANUAL: lines} that contain the keyword
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

    def cond(row, col):
        v = row.get(col) if hasattr(row, "get") else None
        return None if v is None or v != v else v

    order = []
    for r in others.to_dict("records"):
        order.append((int(r["priority"]), int(r["id"]),
                      (r["keyword"], r["category"], r["subcategory"],
                       cond(r, "amount_kind"), cond(r, "amount"))))
    order.append((int(priority), rule_id if rule_id is not None
                  else 10 ** 12, (kw, category, subcategory,
                                  amount_kind or None,
                                  amount if amount_kind else None)))
    order.sort(key=lambda r: (r[0], r[1]))
    tuples = [t for _, _, t in order]
    mine = rule_key(kw, amount_kind, amount)

    hits = txns[txns["description"].map(lambda d: squash(kw) in squash(
        str(d).upper()))]
    keep, beaten = [], {}
    amounts = hits["amount"] if "amount" in hits else [None] * len(hits)
    for i, d, a in zip(hits.index, hits["description"], amounts):
        m = classify(d, tuples, a)
        if m.key == mine:
            keep.append(i)
        elif m.rule is not None or amount_fits(amount_kind, amount, a):
            label = m.rule or NO_RULE
            beaten[label] = beaten.get(label, 0) + 1
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
