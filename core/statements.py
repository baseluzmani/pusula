"""
HSBC credit-card PDF statements, read into the same shape as the online CSV
export so they go through the normal Import preview.

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

Only the HSBC credit-card layout is supported. Current-account statements are
laid out differently.
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
    rows: pd.DataFrame                  # date, description, amount
    statement_date: str | None = None   # YYYY-MM-DD
    previous_balance: float | None = None
    debits: float | None = None
    credits: float | None = None
    new_balance: float | None = None
    problems: list = field(default_factory=list)

    @property
    def period(self) -> tuple[str, str] | None:
        """Posting dates the statement covers: the day after the previous
        statement date to this one (HSBC keeps the same day each month)."""
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
        if "HSBC" not in first or "Credit Card Statement" not in first:
            raise ValueError("not an HSBC credit-card statement")
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


def reconcile(stored: pd.DataFrame, debits: float, credits: float) -> dict:
    """Stored rows (signed amounts) for a statement period against the
    statement's debits and credits. Differences are stored minus statement."""
    got_d = round(-stored.loc[stored["amount"] < 0, "amount"].sum(), 2)
    got_c = round(stored.loc[stored["amount"] > 0, "amount"].sum(), 2)
    dd, dc = round(got_d - debits, 2), round(got_c - credits, 2)
    return {"debits": got_d, "credits": got_c, "debits_diff": dd,
            "credits_diff": dc, "ok": abs(dd) < 0.005 and abs(dc) < 0.005,
            "rows": len(stored)}
