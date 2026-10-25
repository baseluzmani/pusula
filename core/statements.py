"""
Credit-card PDF statements, read into the same shape as the online CSV export
so they go through the normal Import preview. Two layouts:

    HSBC UK        sterling; parse_hsbc_credit_pdf
    HSBC Turkiye   lira ("Ekstre"); parse_hsbc_tr_pdf

parse_statement_pdf() tells them apart and calls the right one.

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
anything is imported.

Only the credit-card layout is supported. Current-account statements are
laid out differently.

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

    @property
    def period(self) -> tuple[str, str] | None:
        """Posting dates the statement covers: the day after the previous
        statement date to this one (HSBC keeps the same day each month).
        None for a statement dated by transaction date - its cut-off day can
        fall either side, so a date window would not reconcile exactly."""
        if not self.statement_date or not self.by_posting_date:
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
    st = Statement(rows=pd.DataFrame(rows, columns=["date", "description",
                                                    "amount"]),
                   **summary_from_text(first))
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
    if "HSBC" in t and "Credit Card Statement" in t:
        return "HSBC UK"
    if "HSBC" in t and "Hesap Kesim Tarihi" in t:
        return "HSBC TR"
    return None


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
    if bank == "HSBC TR":
        return parse_hsbc_tr_pdf(raw)
    raise ValueError("not a statement layout Pusula knows (HSBC UK or HSBC "
                     "Turkiye credit card)")


def reconcile(stored: pd.DataFrame, debits: float, credits: float) -> dict:
    """Stored rows (signed amounts) for a statement period against the
    statement's debits and credits. Differences are stored minus statement."""
    got_d = round(-stored.loc[stored["amount"] < 0, "amount"].sum(), 2)
    got_c = round(stored.loc[stored["amount"] > 0, "amount"].sum(), 2)
    dd, dc = round(got_d - debits, 2), round(got_c - credits, 2)
    return {"debits": got_d, "credits": got_c, "debits_diff": dd,
            "credits_diff": dc, "ok": abs(dd) < 0.005 and abs(dc) < 0.005,
            "rows": len(stored)}
