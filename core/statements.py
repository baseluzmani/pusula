"""
Credit-card PDF statements, read into the same shape as the online CSV export
so they go through the normal Import preview. Two layouts:

    HSBC UK credit card       sterling; parse_hsbc_credit_pdf
    HSBC UK current account   sterling; parse_hsbc_current_pdf
    HSBC Turkiye credit card  lira ("Ekstre"); parse_hsbc_tr_pdf

parse_statement_pdf() tells them apart and calls the right one. Each one
also reads the last four digits of the card or account number
(Statement.account_ref), so Import can tell which card a statement is for.
Only those four digits are kept.

HSBC UK
-------
Two things make a statement line match the export line for the same purchase:

    date         the export uses the date the payment reached the account -
                 the statement's "Received By Us" column - not the
                 transaction date beside it. Using the transaction date is
                 what double-counted 81 July rows on 25 Oct 2026.
    description  the statement prints the card network's fixed-width text
                 (22-character merchant, 13-character city, country), with
                 ')))' (contactless) or 'IAP' (in-app) in front and 'GBR'
                 left off. The export drops the prefix and keeps GBR.

The account summary (debits, credits, balances, statement date) is read too,
so the parsed lines can be checked against the bank's own totals before
anything is imported. Interest is not a transaction line: it is printed in
the "Summary Of Interest" box and included in the debits, and the export
shows it as a line called INTEREST on the statement date - which is what
the reader adds.

HSBC UK current account
-----------------------
One block per transaction: the payment type (VIS, DD, CR, ATM, ")))"...) in
its own column, the details over one or two lines, the amount in the Paid out
or Paid in column, and the running balance after the last payment of each
day. The date is printed once per day. The export joins the details and puts
the type at the end - "VIS CORNER CAFE / Leeds" is "CORNER CAFE
Leeds VIS" - and uses the same date, so the statement lines are exactly the
export rows. Which column an amount sits in is read from its position on the
page. The running balance is checked line by line, and the lines against the
summary's payments in and out.

HSBC Turkiye
------------
One line per purchase: transaction date (DD/MM/YYYY - there is no posting
date), description as printed ("MIGROS 1234 / ANKARA"), the amount in lira,
then optional columns: Advantage points, and for a purchase made in another
currency the foreign amount and its code ("17.52 GBP"). A payment or refund
is printed with a leading "+"; everything else is spending.

The "Hesap Özetiniz" box gives previous balance, spending, interest and fees,
payments and the new balance. Lines are checked against it: spending + fees
must equal the lines without "+", payments the lines with it. Statements are
cut on a day each month (Hesap Kesim Tarihi); a purchase on the cut-off day
can land on either side, so for this card the check is per statement rather
than by a date window.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

MONTH = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_TWO_DATES = re.compile(rf"^(\d{{2}}){MONTH}(\d{{2}})(\d{{2}}){MONTH}(\d{{2}})$")
_AMOUNT = re.compile(r"^[\d,]+\.\d{2}(CR)?$")

# Lines the bank writes itself; kept as they are.
BANK_LINES = ("PAYMENT - THANK YOU", "NON-STERLING TRANSACTION FEE")
_PREFIXES = (")))", "IAP")


@dataclass
class Statement:
    rows: pd.DataFrame                  # date, description, amount[, note]
    statement_date: str | None = None   # YYYY-MM-DD
    previous_balance: float | None = None
    debits: float | None = None
    credits: float | None = None
    new_balance: float | None = None
    problems: list = field(default_factory=list)
    currency: str = "GBP"               # what the amounts are in
    bank: str = "HSBC UK"
    by_posting_date: bool = True        # False: rows carry transaction dates
    account_ref: str | None = None      # 'HSBC UK:1234' - bank + last 4 only
    period_from: str | None = None      # printed on the statement, if it is
    period_to: str | None = None

    @property
    def period(self) -> tuple[str, str] | None:
        """Posting dates the statement covers. A current-account statement
        prints them; for a credit card it is the day after the previous
        statement date to this one (HSBC keeps the same day each month).
        None for a statement dated by transaction date - its cut-off day can
        fall either side, so a date window would not reconcile exactly."""
        if self.period_from and self.period_to:
            return self.period_from, self.period_to
        if not self.statement_date or not self.by_posting_date:
            return None
        end = pd.Timestamp(self.statement_date)
        start = end - pd.DateOffset(months=1) + pd.Timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @property
    def window(self) -> tuple[str, str] | None:
        """The dates the statement is taken to cover: its period, or for a
        statement dated by transaction date the month to its statement
        date (purchases on the cut-off day may sit on the next one)."""
        if self.period:
            return self.period
        if not self.statement_date:
            return None
        end = pd.Timestamp(self.statement_date)
        start = end - pd.DateOffset(months=1) + pd.Timedelta(days=1)
        return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    @property
    def rows_debits(self) -> float:
        return round(-self.rows.loc[self.rows["amount"] < 0, "amount"].sum(), 2)

    @property
    def rows_credits(self) -> float:
        return round(self.rows.loc[self.rows["amount"] > 0, "amount"].sum(), 2)

    @property
    def adds_up(self) -> bool:
        """The parsed lines total exactly what the statement says."""
        return (self.debits is not None and self.credits is not None
                and abs(self.rows_debits - self.debits) < 0.005
                and abs(self.rows_credits - self.credits) < 0.005)


# --- Pure parts (tested without a PDF) --------------------------------------------

def export_description(raw: str) -> str:
    """Statement description -> the online export's description."""
    s = raw.strip()
    if s in BANK_LINES:
        return s
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
            break
    s = s.lstrip()
    merchant, city, country = s[:22], s[23:36], s[37:].strip() or "GBR"
    return f"{merchant:<22} {city:<13} {country}"


def row_from_line(dates: str, raw_description: str, amount: str) -> dict:
    """One transaction line. dates is the two date columns run together,
    e.g. '08Aug2607Aug26' (received by us, then transaction date)."""
    m = _TWO_DATES.match(dates)
    if not m:
        raise ValueError(f"not a statement date pair: {dates!r}")
    posted = pd.to_datetime(f"{m.group(1)} {m.group(2)} {m.group(3)}",
                            format="%d %b %y")
    value = float(amount.replace("CR", "").replace(",", ""))
    return {"date": posted.strftime("%Y-%m-%d"),
            "description": export_description(raw_description),
            "amount": round(value if amount.endswith("CR") else -value, 2)}


def interest_from_text(text: str) -> float | None:
    """The interest charged, from the 'Summary Of Interest On This
    Statement' box ('TOTAL INTEREST CHARGED ON THIS STATEMENT 1.52'). It
    is not one of the transaction lines, but it is in the statement's
    debits, and the export lists it as a line called INTEREST on the
    statement date."""
    m = re.search(r"TOTALINTERESTCHARGEDONTHISSTATEMENT([\d,]+\.\d{2})",
                  re.sub(r"\s+", "", text or "").upper())
    return float(m.group(1).replace(",", "")) if m else None


def _money(pattern: str, text: str) -> float | None:
    m = re.search(pattern + r"£?([\d,]+\.\d{2})(CR)?", text)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -v if m.group(2) else v


def summary_from_text(text: str) -> dict:
    """The account summary box from page one's text. Spaces are ignored -
    the PDF's text layer drops some of them ('PreviousBalance')."""
    t = re.sub(r"\s+", "", text)
    out = {"previous_balance": _money("PreviousBalance", t),
           "debits": _money("Debits", t),
           "credits": _money("Credits", t),
           "new_balance": _money("NewBalance", t),
           "statement_date": None}
    m = re.search(r"StatementDate(\d{1,2})([A-Za-z]+)(\d{4})", t)
    if m:
        out["statement_date"] = pd.to_datetime(
            f"{m.group(1)} {m.group(2)} {m.group(3)}",
            format="%d %B %Y").strftime("%Y-%m-%d")
    return out


# --- Reading the PDF ----------------------------------------------------------------

def _transaction_lines(page):
    """(dates, raw description, amount) for each transaction line on a page.

    Words are grouped into lines by position. A line is a transaction when it
    ends in an amount and starts with the two date columns. The description
    is rebuilt from the page's characters, spaces included, because the
    card network's fixed-width spacing is what makes it match the export.
    Foreign-currency detail lines ('400.00 TRY@64.1025') have no dates and
    are skipped."""
    lines = []
    for w in sorted(page.extract_words(keep_blank_chars=False),
                    key=lambda w: w["top"]):
        if lines and abs(lines[-1][0] - w["top"]) < 3:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    for _, words in lines:
        words.sort(key=lambda w: w["x0"])
        texts = [w["text"] for w in words]
        if not _AMOUNT.match(texts[-1]):
            continue
        acc, i = "", 0
        while i < len(words) and len(acc) < 14:     # '08Aug26' + '07Aug26'
            acc += texts[i]
            i += 1
        if not _TWO_DATES.match(acc) or i >= len(words) - 1:
            continue
        left, right = words[i]["x0"] - 0.5, words[-1]["x0"] - 0.5
        chars = sorted((c for c in page.chars
                        if abs(c["top"] - words[i]["top"]) < 2
                        and left <= c["x0"] < right), key=lambda c: c["x0"])
        yield acc, "".join(c["text"] for c in chars).rstrip(), texts[-1]


def parse_hsbc_credit_pdf(raw: bytes) -> Statement:
    import pdfplumber                      # only needed for PDFs

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        first = pdf.pages[0].extract_text() or ""
        if detect(first) != "HSBC UK":
            raise ValueError("not an HSBC UK credit-card statement")
        rows = [row_from_line(*line) for page in pdf.pages
                for line in _transaction_lines(page)]
        everything = "\n".join(p.extract_text() or "" for p in pdf.pages)
    summ = summary_from_text(first)
    interest = interest_from_text(everything)
    if interest and summ["statement_date"]:
        rows.append({"date": summ["statement_date"], "description": "INTEREST",
                     "amount": -interest})
    st = Statement(rows=pd.DataFrame(rows, columns=["date", "description",
                                                    "amount"]),
                   account_ref=account_ref("HSBC UK", first), **summ)
    if st.rows.empty:
        st.problems.append("no transaction lines found")
    elif st.debits is None or st.credits is None:
        st.problems.append("could not read the account summary")
    elif not st.adds_up:
        st.problems.append(
            f"lines add up to debits {st.rows_debits:,.2f} / credits "
            f"{st.rows_credits:,.2f}, statement says {st.debits:,.2f} / "
            f"{st.credits:,.2f}")
    return st


# --- HSBC UK current account ----------------------------------------------------

_CA_MONEY = re.compile(r"^[\d,]+\.\d{2}$")
_CA_DAY = re.compile(r"^\d{2}$")
_CA_MON = re.compile(rf"^{MONTH}$")
_CA_PERIOD = re.compile(r"(\d{1,2})\s*([A-Za-z]+)\s*(\d{4})?\s*to\s*(\d{1,2})\s*"
                        r"([A-Za-z]+)\s*(\d{4})")


def _ca_money(label: str, squashed: str) -> float | None:
    """'OpeningBalance£3,727.35' (spaces removed); a trailing D is
    overdrawn."""
    m = re.search(label + r"£?([\d,]+\.\d{2})(D|OD)?", squashed)
    if not m:
        return None
    v = float(m.group(1).replace(",", ""))
    return -v if m.group(2) else v


def ca_summary_from_text(text: str) -> dict:
    """Page one: the period printed under the address and the Account
    Summary box (opening and closing balance, payments in and out)."""
    t = re.sub(r"\s+", "", text or "")
    out = {"previous_balance": _ca_money("OpeningBalance", t),
           "credits": _ca_money("PaymentsIn", t),
           "debits": _ca_money("PaymentsOut", t),
           "new_balance": _ca_money("ClosingBalance", t),
           "period_from": None, "period_to": None, "statement_date": None}
    m = _CA_PERIOD.search(text or "")
    if m:
        d1, m1, y1, d2, m2, y2 = m.groups()
        end = pd.to_datetime(f"{d2} {m2} {y2}", format="%d %B %Y")
        start = pd.to_datetime(f"{d1} {m1} {y1 or y2}", format="%d %B %Y")
        if start > end:                          # 12 December to 11 January
            start = start - pd.DateOffset(years=1)
        out["period_from"] = start.strftime("%Y-%m-%d")
        out["period_to"] = out["statement_date"] = end.strftime("%Y-%m-%d")
    return out


def _ca_columns(header):
    """Column positions from the table header line: the right edge of each
    money column (amounts are right-aligned) and where Payment type
    starts."""
    cols, pay = {}, None
    texts = [w[2] for w in header]
    for i, (x0, x1, t) in enumerate(w[:3] for w in header):
        if t == "out" and i and texts[i - 1].endswith("Paid"):
            cols["out"] = x1
        elif t == "in" and i and texts[i - 1].endswith("Paid"):
            cols["in"] = x1
        elif t.endswith("Balance"):
            cols["balance"] = x1
        elif t == "Payment" and pay is None:
            pay = x0
    if len(cols) < 3 or pay is None:
        return None
    return cols, pay


def ca_rows_from_lines(pages) -> tuple[list[dict], list[str], float | None]:
    """The transactions from a current-account statement's words.

    pages: one list per page of lines, each a list of (x0, x1, text) words
    left to right (what pdfplumber gives, grouped by line); a fourth item,
    when there, is how many spaces the PDF has before the word - the
    export keeps a bank's double spaces ('UBER   *ONE'), so the details do
    too. Returns (rows, problems, opening balance as brought forward)."""
    rows, problems = [], []
    bal = opening = None
    date = None
    pending = None
    for lines in pages:
        start = next((i for i, ln in enumerate(lines)
                      if _ca_columns(ln)), None)
        if start is None:
            continue
        cols, pay = _ca_columns(lines[start])
        code_lo, code_hi = pay - 15, pay + 18
        for line in lines[start + 1:]:
            words = list(line)
            if (len(words) >= 3 and words[0][0] < code_lo
                    and _CA_DAY.match(words[0][2])
                    and _CA_MON.match(words[1][2])
                    and _CA_DAY.match(words[2][2])):
                date = pd.to_datetime(" ".join(w[2] for w in words[:3]),
                                      format="%d %b %y").strftime("%Y-%m-%d")
                words = words[3:]
            money, other = {}, []
            for word in words:
                x0, x1, t = word[:3]
                if _CA_MONEY.match(t) and x1 > cols["out"] - 45:
                    col = min(cols, key=lambda c: abs(cols[c] - x1))
                    money[col] = float(t.replace(",", ""))
                elif (t in ("D", "OD") and "balance" in money
                      and x0 > cols["balance"] - 5):
                    money["balance"] = -money["balance"]
                else:
                    other.append(word)
            squashed = "".join(w[2] for w in other).upper()
            if "BALANCEBROUGHTFORWARD" in squashed:
                b = money.get("balance")
                if b is not None:
                    if bal is None:
                        bal = opening = b
                    elif abs(bal - b) > 0.005:
                        problems.append(f"balance brought forward {b:,.2f}, "
                                        f"lines give {bal:,.2f}")
                        bal = b
                continue
            if "BALANCECARRIEDFORWARD" in squashed:
                b = money.get("balance")
                if b is not None and bal is not None and abs(bal - b) > 0.005:
                    problems.append(f"balance carried forward {b:,.2f}, "
                                    f"lines give {bal:,.2f}")
                break                                    # end of this page
            code = [w[2] for w in other if code_lo <= w[0] < code_hi]
            details = _spaced([w for w in other if w[0] >= code_hi])
            if code:
                if pending is not None:
                    problems.append(f"no amount for '{' '.join(pending['details'])}'"
                                    f" on {pending['date']}")
                pending = {"code": " ".join(code), "details": details,
                           "date": date}
            elif details and pending is not None:
                pending["details"] += details
            paid = (-money["out"] if "out" in money else
                    money["in"] if "in" in money else None)
            if paid is not None:
                if pending is None:
                    problems.append(f"amount {abs(paid):,.2f} on {date} has "
                                    "no payment line")
                else:
                    rows.append({"date": pending["date"],
                                 "description": " ".join(
                                     pending["details"] + [pending["code"]]),
                                 "amount": round(paid, 2), "note": None})
                    if bal is not None:
                        bal = round(bal + paid, 2)
                    pending = None
            if "balance" in money:
                b = money["balance"]
                if bal is not None and abs(bal - b) > 0.005:
                    problems.append(f"running balance on {date}: statement "
                                    f"{b:,.2f}, lines give {bal:,.2f}")
                bal = b
    if pending is not None:
        problems.append(f"no amount for '{' '.join(pending['details'])}' on "
                        f"{pending['date']}")
    return rows, problems, opening


def _spaced(words) -> list[str]:
    """One line's detail words as a single string, with as many spaces
    between them as the PDF has (at least one); [] for no words."""
    if not words:
        return []
    out = words[0][2]
    for w in words[1:]:
        gap = w[3] if len(w) > 3 else 1
        out += " " * gap + w[2]
    return [out]


def _page_lines(page):
    """A page's words as lines of (x0, x1, text, spaces before), top to
    bottom. The spaces are counted from the page's characters; two words
    that touch are one word drawn in two pieces ('ENTERTA' 'I'), so none."""
    lines = []
    for w in sorted(page.extract_words(keep_blank_chars=False),
                    key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1][0] - w["top"]) < 3:
            lines[-1][1].append(w)
        else:
            lines.append([w["top"], [w]])
    spaces = [c for c in page.chars if c["text"] == " "]
    out = []
    for top, ws in lines:
        ws = sorted(ws, key=lambda w: w["x0"])
        row, prev = [], None
        for w in ws:
            gap = 0
            if prev is not None:
                gap = sum(1 for c in spaces if abs(c["top"] - w["top"]) < 2
                          and prev["x1"] - 0.5 <= c["x0"] < w["x0"])
                if w["x0"] - prev["x1"] < 0.5:
                    gap = 0          # one word the PDF drew in two pieces
                elif gap == 0:
                    gap = 1
            row.append((w["x0"], w["x1"], w["text"], gap))
            prev = w
        out.append(row)
    return out


def parse_hsbc_current_pdf(raw: bytes) -> Statement:
    import pdfplumber                      # only needed for PDFs

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        first = pdf.pages[0].extract_text() or ""
        if detect(first) != "HSBC UK current":
            raise ValueError("not an HSBC UK current-account statement")
        pages = [_page_lines(p) for p in pdf.pages]
    rows, problems, brought = ca_rows_from_lines(pages)
    summ = ca_summary_from_text(first)
    st = Statement(rows=pd.DataFrame(rows, columns=["date", "description",
                                                    "amount", "note"]),
                   bank="HSBC UK current",
                   account_ref=account_ref("HSBC UK current", first), **summ)
    st.problems.extend(problems)
    if st.rows.empty:
        st.problems.append("no transaction lines found")
    elif st.debits is None or st.credits is None:
        st.problems.append("could not read the account summary")
    elif not st.adds_up:
        st.problems.append(
            f"lines add up to paid out {st.rows_debits:,.2f} / paid in "
            f"{st.rows_credits:,.2f}, statement says {st.debits:,.2f} / "
            f"{st.credits:,.2f}")
    if (brought is not None and st.previous_balance is not None
            and abs(brought - st.previous_balance) > 0.005):
        st.problems.append(f"opening balance {st.previous_balance:,.2f} but "
                           f"brought forward {brought:,.2f}")
    if None not in (st.previous_balance, st.new_balance, st.debits,
                    st.credits):
        moved = round(st.previous_balance + st.credits - st.debits, 2)
        if abs(moved - st.new_balance) > 0.005:
            st.problems.append(f"opening + in - out is {moved:,.2f}, closing "
                               f"balance {st.new_balance:,.2f}")
    return st


# --- HSBC Turkiye ----------------------------------------------------------------

_TR_MONTHS = {"ocak": 1, "subat": 2, "mart": 3, "nisan": 4, "mayis": 5,
              "haziran": 6, "temmuz": 7, "agustos": 8, "eylul": 9, "ekim": 10,
              "kasim": 11, "aralik": 12}
_FOLD = str.maketrans("ıişğüöçâİ", "iisguocai")
_TR_DATE_LINE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})\s")
_EN_NUM = re.compile(r"^\+?(\d{1,3}(,\d{3})+|\d+)\.\d{2}$")      # 1,025.70
_TR_NUM = re.compile(r"^\+?(\d{1,3}(\.\d{3})+|\d+),\d{2}$")      # 1.025,70
_CCY = re.compile(r"^[A-Z]{3}$")


def detect(first_page_text: str) -> str | None:
    """Which layout a statement's first page is, or None."""
    t = first_page_text or ""
    squashed = re.sub(r"\s+", "", t)
    if "HSBC" in t and "Credit Card Statement" in t:
        return "HSBC UK"
    if "HSBC" in t and "Hesap Kesim Tarihi" in t:
        return "HSBC TR"
    # Some current-account statements only say "www.hsbc.co.uk".
    if ("HSBC" in t.upper() and "PaymentsOut" in squashed
            and "Sortcode" in squashed):
        return "HSBC UK current"
    return None


def account_ref(bank: str, first_page_text: str) -> str | None:
    """'<bank>:<last four digits>' of the card or account a statement is
    for - enough to tell your cards apart, and all that is kept.

        HSBC UK          the 16-digit card number under 'Card number'
        HSBC UK current  the 8-digit account number after the sort code
        HSBC TR          'Kart No : 4111 22** **** 5678'"""
    t = first_page_text or ""
    m = None
    if bank == "HSBC UK":
        m = re.search(r"\b\d{4} \d{4} \d{4} (\d{4})\b", t)
    elif bank == "HSBC UK current":
        m = re.search(r"\b\d{2}-\d{2}-\d{2}\s+\d{4}(\d{4})\b", t)
    elif bank == "HSBC TR":
        m = re.search(r"Kart No\s*:\s*[\d* ]*?(\d{4})\s*$", t, re.M)
    return f"{bank}:{m.group(1)}" if m else None


def tr_amount(token: str) -> float | None:
    """'1,025.70', '+45,000.00' or Turkish style '1.025,70' -> float, the
    sign kept as printed ('+' stays positive). None if not an amount."""
    t = token.strip()
    if _EN_NUM.match(t):
        return float(t.replace("+", "").replace(",", ""))
    if _TR_NUM.match(t):
        return float(t.replace("+", "").replace(".", "").replace(",", "."))
    return None


def tr_row_from_line(line: str) -> dict | None:
    """One Ekstre line -> date, description, signed amount, note; None for
    anything that is not a transaction (balances, page totals, headings).

    The amount is the first number after the description that is followed
    only by numbers or a currency code - so a store number inside the name
    stays in the name."""
    m = _TR_DATE_LINE.match(line.strip())
    if not m:
        return None
    tokens = line.strip().split()
    for i in range(1, len(tokens)):
        value = tr_amount(tokens[i])
        rest = tokens[i + 1:]
        if value is None or not all(tr_amount(x) is not None or _CCY.match(x)
                                    for x in rest):
            continue
        description = " ".join(tokens[1:i]).strip()
        if not description:
            return None
        note = None
        if len(rest) >= 2 and _CCY.match(rest[-1]) and tr_amount(rest[-2]):
            note = f"{rest[-2]} {rest[-1]}"
        credit = tokens[i].startswith("+")
        return {"date": f"{m.group(3)}-{m.group(2)}-{m.group(1)}",
                "description": description,
                "amount": round(value if credit else -value, 2),
                "note": note}
    return None


def _tr_date(text: str, label: str) -> str | None:
    m = re.search(label + r"\s*:?\s*(\d{1,2})\s+(\S+)\s+(\d{4})", text)
    if not m:
        return None
    month = _TR_MONTHS.get(m.group(2).lower().translate(_FOLD))
    if not month:
        return None
    return f"{m.group(3)}-{month:02d}-{int(m.group(1)):02d}"


def tr_summary_from_text(text: str) -> dict:
    """Statement date and the Hesap Özetiniz figures. The summary is the
    line of six amounts under its heading: previous balance, spending,
    interest and fees, payments, new balance, minimum payment."""
    out = {"statement_date": _tr_date(text, "Hesap Kesim Tarihi"),
           "previous_balance": None, "spending": None, "fees": None,
           "payments": None, "new_balance": None}
    for line in text.splitlines():
        parts = line.split()
        values = [tr_amount(x) for x in parts]
        if len(parts) == 6 and all(v is not None for v in values):
            (out["previous_balance"], out["spending"], out["fees"],
             out["payments"], out["new_balance"], _) = values
    return out


def parse_hsbc_tr_pdf(raw: bytes) -> Statement:
    import pdfplumber                      # only needed for PDFs

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        texts = [p.extract_text() or "" for p in pdf.pages]
    if detect(texts[0] if texts else "") != "HSBC TR":
        raise ValueError("not an HSBC Turkiye credit-card statement")
    rows = [r for t in texts for line in t.splitlines()
            if (r := tr_row_from_line(line))]
    summ = tr_summary_from_text("\n".join(texts))
    debits = (None if summ["spending"] is None else
              round(summ["spending"] + (summ["fees"] or 0), 2))
    st = Statement(rows=pd.DataFrame(rows, columns=["date", "description",
                                                    "amount", "note"]),
                   account_ref=account_ref("HSBC TR", texts[0]),
                   statement_date=summ["statement_date"],
                   previous_balance=summ["previous_balance"], debits=debits,
                   credits=summ["payments"], new_balance=summ["new_balance"],
                   currency="TRY", bank="HSBC TR", by_posting_date=False)
    if st.rows.empty:
        st.problems.append("no transaction lines found")
    elif st.debits is None or st.credits is None:
        st.problems.append("could not read the Hesap Özetiniz summary")
    elif not st.adds_up:
        st.problems.append(
            f"lines add up to spending {st.rows_debits:,.2f} / payments "
            f"{st.rows_credits:,.2f}, statement says {st.debits:,.2f} / "
            f"{st.credits:,.2f}")
    return st


def parse_statement_pdf(raw: bytes) -> Statement:
    """Any supported statement: reads page one, then the matching parser."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        first = (pdf.pages[0].extract_text() or "") if pdf.pages else ""
    bank = detect(first)
    if bank == "HSBC UK":
        return parse_hsbc_credit_pdf(raw)
    if bank == "HSBC UK current":
        return parse_hsbc_current_pdf(raw)
    if bank == "HSBC TR":
        return parse_hsbc_tr_pdf(raw)
    raise ValueError("not a statement layout Pusula knows (HSBC UK credit "
                     "card or current account, HSBC Turkiye credit card)")


def reconcile(stored: pd.DataFrame, debits: float, credits: float) -> dict:
    """Stored rows (signed amounts) for a statement period against the
    statement's debits and credits. Differences are stored minus statement."""
    got_d = round(-stored.loc[stored["amount"] < 0, "amount"].sum(), 2)
    got_c = round(stored.loc[stored["amount"] > 0, "amount"].sum(), 2)
    dd, dc = round(got_d - debits, 2), round(got_c - credits, 2)
    return {"debits": got_d, "credits": got_c, "debits_diff": dd,
            "credits_diff": dc, "ok": abs(dd) < 0.005 and abs(dc) < 0.005,
            "rows": len(stored)}
