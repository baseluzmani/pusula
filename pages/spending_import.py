"""
Spending -> Import.

Two kinds of upload, several files at once:

Statements (PDF) - the source of truth
    HSBC UK credit card, HSBC UK current account, HSBC Turkiye credit card
    (core/statements.py). The card comes from the account on the statement:
    the first statement of an account asks (it is the card selected), and
    the account's last four digits are remembered for that card.

    Each statement line is paired with what is stored (core.spending.
    match_statements), so categories you set by hand survive a reload:
        matched      the same purchase as a stored line - word for word, or
                     the same shop within 5p and 3 days (the 8052 history
                     rounded amounts to 10p, and stored some refunds as
                     spending) - linked and hidden; the line takes the
                     statement's exact amount, sign and date
        to check     paired with a stored line whose wording differs more -
                     the statement's version wins, the old wording is kept
                     as a note. Exclude keeps them apart: the line comes in
                     as new and the stored one goes.
        new          not stored - comes in, categorised by the rules
        not on the statement
                     stored lines inside the statement's period that it
                     does not have - duplicates, or rows from an old
                     history. Deleted on import unless you exclude them.
    Every statement is checked against its own totals before import, and
    against the stored lines and the previous statement's closing balance
    after.

CSV exports (HSBC online) - for the weeks the statement has not arrived
    Rows dated inside a stored statement's period are left out (the
    statement already has them). The rest come in as provisional lines and
    pair with their statement when it is uploaded. The card is the one whose
    stored rows the file overlaps most, else the only card in its currency,
    else the last one imported.

Lira (HSBC Turkiye) is converted to pounds at each day's GBP/TRY close from
the prices the portfolio keeps (YF:GBPTRY=X); a line the bank noted as paid
in pounds keeps that figure; the lira amount is stored too, and is what
re-uploads are compared on.

In the preview, rows no rule matched come first. Tick rows to set a
category and note; Remember turns the keyword into a rule - for Amazon,
PayPal and cash lines, the keyword with the exact amount.

Above the upload, Cards shows each card's last posting, last statement, any
stretch with no transactions and the last import.
"""

from __future__ import annotations

import base64

import pandas as pd
from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)

from core import theme, spending as sp, statements
from core.repo import settings
from core.repo import spending as repo
from ui.spend import (TABLE, money_cells, money_col, btn, field, bar, header,
                      message, gbp, assign_bar, register_subcategories,
                      select_rows, remember_rule, INPUT, NOTE)

P = "sp-im"

COLUMNS = [
    {"name": "In", "id": "in"},
    {"name": "Date", "id": "date"},
    {"name": "Description", "id": "description"},
    money_col("Amount", "gbp"),
    {"name": "Category", "id": "category"},
    {"name": "Sub-category", "id": "subcategory"},
    {"name": "Matched by", "id": "matched"},
    {"name": "Note", "id": "flag"},
]
# Shown after Amount when the files are not in pounds.
ORIGINAL = {"name": "Original", "id": "orig_fmt"}

# Display order of preview rows, fixed when the preview is built so rows do
# not jump around while you categorise them.
ORDER = {"orphan": 0, "check": 1, "no rule": 2, "near": 3, "manual": 4,
         "rule": 5}
YES, NO = "✓", "—"
# What the In column says for the statement-only row kinds.
IN_TEXT = {"check": ("Pair", "Apart"), "orphan": ("Delete", "Keep")}

# A converted row whose rate is older than this many days gets a warning:
# the prices have not been refreshed up to the statement.
STALE_RATE_DAYS = 4

# (key, default, type, description) - created on first open if missing.
SETTINGS = [
    ("SPEND_GAP_DAYS", 10, "int",
     "Spending: flag a card that has no transactions for more than this "
     "many days in a row"),
    ("SPEND_STALE_DAYS", 14, "int",
     "Spending: a card is due for an import this many days after its last "
     "posted transaction"),
]


def render():
    for key, default, vtype, desc in SETTINGS:
        settings.ensure(key, default, vtype, desc)
    names = repo.card_names()
    last = repo.last_import_card()
    card = last if last in names else (names[0] if names else None)
    return html.Div([
        header("Import", "Upload statements (HSBC UK credit card or current "
               "account, HSBC Türkiye) or CSV exports - any file name, "
               "several at once. Statements are matched line by line with "
               "what is stored."),
        html.Div([html.Div("Cards", style=theme.CARD_TITLE),
                  html.Div(id=f"{P}-coverage")], style=theme.CARD),
        html.Div([
            bar(
                html.Div(_upload_box(), id=f"{P}-upload-wrap"),
                field("Card", dcc.Dropdown(
                    id=f"{P}-card", value=card, clearable=False,
                    options=names, style={"fontSize": "12.5px"}),
                    width="200px"),
                field("New card", dcc.Input(
                    id=f"{P}-newcard", type="text", debounce=False,
                    placeholder="e.g. Amex",
                    style={**INPUT, "width": "160px"})),
                html.Button("Add card", id=f"{P}-addcard", n_clicks=0,
                            style=btn()),
            ),
            html.Div(id=f"{P}-cardmsg", style={"marginTop": "8px",
                                               "minHeight": "16px"}),
            html.Div(id=f"{P}-cardnote", style={**NOTE, "marginTop": "2px"}),
        ], style=theme.CARD),

        html.Div(id=f"{P}-result", style={"marginBottom": "12px"}),

        html.Div(id=f"{P}-preview", style={"display": "none"}, children=[
            html.Div(id=f"{P}-summary", style={**NOTE, "color": theme.TEXT,
                                                "marginBottom": "12px"}),
            assign_bar(P, "Set on selected", note=True, extra=[
                html.Button("Exclude", id=f"{P}-exclude", n_clicks=0,
                            style=btn()),
                html.Button("Include", id=f"{P}-include", n_clicks=0,
                            style=btn()),
                html.Button("Select no-rule rows", id=f"{P}-norule",
                            n_clicks=0, style=btn()),
                html.Button("Clear", id=f"{P}-none", n_clicks=0,
                            style=btn()),
            ]),
            html.Div(id=f"{P}-msg", style={"margin": "10px 0 4px 0",
                                           "minHeight": "16px"}),
            dash_table.DataTable(
                id=f"{P}-table", columns=COLUMNS, data=[],
                row_selectable="multi", selected_row_ids=[],
                page_action="none",
                style_cell_conditional=money_cells("gbp", "orig_fmt") + [
                    {"if": {"column_id": "in"}, "width": "46px",
                     "textAlign": "center"},
                    {"if": {"column_id": "description"}, "width": "32%"}],
                style_data_conditional=[
                    {"if": {"filter_query": '{matched} = "No rule"'},
                     "backgroundColor": theme.TINT_NEAR},
                    {"if": {"filter_query": f'{{in}} = "{NO}" or {{in}} = '
                                            '"Keep" or {in} = "Apart"'},
                     "color": theme.NEUTRAL},
                    {"if": {"filter_query": '{in} = "Delete"'},
                     "color": theme.NEGATIVE, "textDecoration": "line-through"},
                    {"if": {"column_id": "orig_fmt"}, "color": theme.SLATE},
                    {"if": {"column_id": "flag",
                            "filter_query": '{flag} != ""'},
                     "color": theme.NEGATIVE, "fontWeight": 600,
                     "textDecoration": "none"}],
                **TABLE),
            html.Div([
                html.Button("Import", id=f"{P}-go", n_clicks=0,
                            style=btn("go")),
                html.Span(id=f"{P}-gonote", style=NOTE),
            ], style={"display": "flex", "gap": "14px",
                      "alignItems": "center", "marginTop": "14px"}),
        ]),

        dcc.Store(id=f"{P}-file"),
        dcc.Store(id=f"{P}-rows"),
        dcc.Store(id=f"{P}-refresh", data=0),
    ])


register_subcategories(P)


def _upload_box():
    """A fresh Upload. Re-mounted after each import: a browser does not fire
    'change' when the same file is picked twice in the same input."""
    link = {"color": theme.INK, "fontWeight": 600,
            "textDecoration": "underline"}
    return dcc.Upload(
        id=f"{P}-upload", multiple=True, accept=".csv,.pdf",
        children=html.Div(["Drop PDF statements or CSV exports here - "
                           "several at once is fine - or ",
                           html.Span("choose files", style=link)]),
        style={"width": "300px", "padding": "18px 12px",
               "border": f"1px dashed {theme.NEUTRAL}",
               "borderRadius": "6px", "textAlign": "center",
               "fontSize": "12.5px", "color": theme.SLATE,
               "cursor": "pointer", "background": theme.CANVAS})


def _d(iso) -> str:
    """'2026-09-25' -> '25 Sep 2026'; blank for a missing date."""
    return f"{pd.Timestamp(iso):%d %b %Y}" if isinstance(iso, str) and iso else ""


def _ccy(value, currency: str) -> str:
    """Money as the preview shows it; other currencies get their code."""
    return gbp(value) if currency == "GBP" else f"{gbp(value)} {currency}"


def _label(names: list[str]) -> str:
    """What an import is recorded as: the file, or the first and a count."""
    if len(names) == 1:
        return names[0]
    return f"{names[0]} + {len(names) - 1} more"


def _short(ref: str | None) -> str:
    """'HSBC UK:1234' -> '••1234'."""
    return f"••{ref.split(':')[-1]}" if ref else ""


def _last_posted() -> dict:
    """Card -> last posted date, for cards that have any rows."""
    df = repo.card_summary()
    return {n: d for n, d in zip(df["name"], df["last_date"])
            if isinstance(d, str)}


# --- Reading the files ---------------------------------------------------------

def _read(contents: str, filename: str):
    """One uploaded file -> (rows, currency, statement dict or None)."""
    raw = base64.b64decode(contents.split(",", 1)[1])
    if (filename or "").lower().endswith(".pdf"):
        st = statements.parse_statement_pdf(raw)
        lo, hi = st.window or (None, None)
        info = {"file": filename, "bank": st.bank, "currency": st.currency,
                "ref": st.account_ref, "date": st.statement_date,
                "period": st.period, "window": [lo, hi],
                "debits": st.debits, "credits": st.credits,
                "previous_balance": st.previous_balance,
                "new_balance": st.new_balance,
                "rows_debits": st.rows_debits,
                "rows_credits": st.rows_credits,
                "adds_up": st.adds_up, "problems": st.problems}
        return st.rows, st.currency, info
    return sp.parse_bank_csv(raw), "GBP", None


def _convert(df: pd.DataFrame, ccy: str):
    """Pounds for every row: (rows with gbp and rate_date, fx summary or
    None). Raises ValueError when there is no rate."""
    df = df.copy()
    df["note"] = (df["note"].fillna("").astype(str) if "note" in df.columns
                  else "")
    if ccy == "GBP":
        return df.assign(gbp=df["amount"], rate_date=None), None
    rates = repo.gbp_rates(ccy)
    df = sp.convert_to_gbp(df, rates)
    lag = (pd.to_datetime(df["date"]) - pd.to_datetime(df["rate_date"])).dt.days
    return df, {"source": f"YF:GBP{ccy}=X", "latest": rates["date"].max(),
                "paid_gbp": int((df["note"].str.endswith(" GBP")).sum()),
                "stale": int((lag > STALE_RATE_DAYS).sum())}


@callback(
    Output(f"{P}-file", "data"),
    Output(f"{P}-card", "value"),
    Output(f"{P}-result", "children"),
    Input(f"{P}-upload", "contents"),
    State(f"{P}-upload", "filename"),
    State(f"{P}-card", "value"),
    prevent_initial_call=True,
)
def _upload(contents, filenames, card):
    if not contents:
        return no_update, no_update, no_update
    if isinstance(contents, str):
        contents, filenames = [contents], [filenames]
    names = list(filenames)
    frames, found, by_ccy = [], [], {}
    for body, name in zip(contents, names):
        try:
            df, ccy, info = _read(body, name)
        except Exception as exc:                   # noqa: BLE001
            return None, no_update, message(f"Could not read {name}: {exc}",
                                            ok=False)
        if df.empty:
            return None, no_update, message(
                f"{name}: no transactions found.", ok=False)
        frames.append(df)
        by_ccy.setdefault(ccy, []).append(name)
        if info:
            info["rows"] = df
            found.append(info)
    if len(by_ccy) > 1:
        parts = "; ".join(f"{', '.join(v)} in {k}" for k, v in by_ccy.items())
        return None, no_update, message(
            f"These files are in different currencies ({parts}). Upload each "
            "card's files on their own.", ok=False)
    if found and len(found) != len(names):
        return None, no_update, message(
            "Upload statements and CSV exports separately.", ok=False)
    ccy = next(iter(by_ccy))
    try:
        if found:
            data = _statement_file(found, ccy, names, card)
        else:
            data = _csv_file(frames, ccy, names, card)
    except ValueError as exc:
        return None, no_update, message(
            f"Cannot convert {ccy} to pounds: {exc}. The rate comes from the "
            f"stored prices of YF:GBP{ccy}=X - refresh prices and upload "
            "again.", ok=False)
    if isinstance(data, str):
        return None, no_update, message(data, ok=False)
    return data, data["card"] or card, ""


def _statement_file(found, ccy, names, card):
    """Statements: the card from their accounts; rows converted."""
    found = sorted(found, key=lambda f: f["window"][1] or "")
    refs = sorted({f["ref"] for f in found if f["ref"]})
    known = {r: repo.card_for_ref(r) for r in refs}
    cards = sorted({c for c in known.values() if c})
    if len(cards) > 1:
        return ("These statements are for different cards (" + ", ".join(
            f"{_short(r)} is {c}" for r, c in known.items() if c)
            + "). Upload each card's statements on their own.")
    for f in found:
        if not f["window"][0]:
            return (f"{f['file']}: could not read the statement date or "
                    "period.")
    fx, out = None, []
    for f in found:
        rows, f_fx = _convert(f.pop("rows"), ccy)
        fx = f_fx or fx
        out.append({**f, "rows": rows.to_dict("records")})
    if cards:
        pick, how = cards[0], (f"{cards[0]}: the statements' account "
                               f"{', '.join(_short(r) for r in refs)}.")
        unknown = [r for r, c in known.items() if not c]
        if unknown:
            how += (f" {', '.join(_short(r) for r in unknown)} will be "
                    f"remembered as {pick} too.")
    else:
        # A first statement from this account: the card whose stored rows
        # it overlaps most, else the only card in its currency.
        same = repo.cards_in(ccy)
        lines = pd.concat([pd.DataFrame(f["rows"]) for f in out])
        stored = repo.existing_between(lines["date"].min(),
                                       lines["date"].max())
        stored = stored[stored["card"].isin(same)]
        guess, n = sp.guess_card(lines[["date", "description", "amount"]],
                                 stored)
        pick = (guess if guess else card if card in same else
                same[0] if len(same) == 1 else card)
        why = (f"{n} of its lines are already stored on {guess}" if guess
               else "check it is the right card")
        how = (f"First statement from {', '.join(_short(r) for r in refs)} - "
               f"{why}. The account is remembered as the card you import it "
               "to." if refs else f"Check the card: {why}.")
    return {"mode": "statements", "files": names, "filename": _label(names),
            "currency": ccy, "card": pick, "known": cards[0] if cards else None,
            "refs": refs, "how": how, "statements": out, "fx": fx}


def _csv_file(frames, ccy, names, card):
    """CSV exports: one set of rows; the card from what they overlap."""
    df, fx = _convert(sp.combine_files(frames), ccy)
    same = repo.cards_in(ccy, active_only=False)
    stored = repo.existing_between(df["date"].min(), df["date"].max())
    stored = stored[stored["card"].isin(same)]
    guess, n = sp.guess_card(df[["date", "description", "amount"]], stored)
    active = repo.cards_in(ccy)
    pick = card
    if guess and guess in active:
        how, pick = f"Picked {guess}: {n} of the rows are already stored there.", guess
    elif len(active) == 1:
        pick, guess = active[0], active[0]
        how = (f"Picked {pick}: the only {ccy} card." if ccy != "GBP" else
               f"Picked {pick}: the only card.")
    elif not active:
        how = f"No card is set up for {ccy} - add one (it will be a {ccy} card)."
    else:
        how = ("No overlap with stored rows - using "
               f"{card}. Check it is the right card." if card
               else "Pick the card.")
    return {"mode": "csv", "files": names, "filename": _label(names),
            "rows": df.to_dict("records"), "currency": ccy, "how": how,
            "card": pick, "guess": guess, "fx": fx, "statements": []}


@callback(
    Output(f"{P}-card", "options"),
    Output(f"{P}-card", "value", allow_duplicate=True),
    Output(f"{P}-cardmsg", "children"),
    Output(f"{P}-newcard", "value"),
    Input(f"{P}-addcard", "n_clicks"),
    State(f"{P}-newcard", "value"),
    State(f"{P}-file", "data"),
    prevent_initial_call=True,
)
def _add_card(_n, name, file):
    """A card added here takes the loaded files' currency (pounds when no
    file is loaded)."""
    ccy = (file or {}).get("currency") or "GBP"
    problem = repo.add_card(name, ccy)
    if problem:
        return no_update, no_update, message(problem, ok=False), no_update
    name = name.strip()
    what = f" ({ccy})" if ccy != "GBP" else ""
    return (repo.card_names(), name,
            message(f"Added {name}{what} - it is selected."), "")


# --- Preview -----------------------------------------------------------------

def _mismatch(file, card) -> str | None:
    """Why these files cannot go to this card, if they cannot."""
    if not (file and card):
        return None
    have, want = repo.card_currency(card), file.get("currency", "GBP")
    if have != want:
        return (f"{card} is a {have} card and these files are in {want} - "
                f"pick a {want} card or add one.")
    known = file.get("known")
    if known and known != card:
        return (f"These statements are {known}'s account "
                f"({', '.join(_short(r) for r in file.get('refs', []))}) - "
                f"pick {known}.")
    return None


def _classified(r, rules, i, state):
    """A preview row for a line that is not stored yet."""
    m = sp.classify(r["description"], rules, r["gbp"])
    kind = ("near" if state == "near" else
            "manual" if m.rule == sp.MANUAL else
            "rule" if m.rule else "no rule")
    return {"id": i, "date": r["date"], "description": r["description"],
            "amount": r["amount"], "gbp": r["gbp"],
            "fx_note": r.get("note") or "", "state": state,
            "category": m.category, "subcategory": m.subcategory,
            "rule": m.rule, "note": m.note, "edited": False,
            "include": state == "new", "order": ORDER[kind]}


def _statement_rows(file, card):
    """Preview rows for statements: pairs, new lines and stored lines the
    statements do not have."""
    sts = file["statements"]
    sids = repo.statement_ids_for(card, [s["window"][1] for s in sts])
    lo = min(s["window"][0] for s in sts)
    hi = max(s["window"][1] for s in sts)
    pad = pd.Timedelta(days=5)
    cands = repo.match_candidates(
        card, (pd.Timestamp(lo) - pad).strftime("%Y-%m-%d"),
        (pd.Timestamp(hi) + pad).strftime("%Y-%m-%d"), sids.values())
    batch = [(pd.DataFrame(s["rows"]), tuple(s["window"])) for s in sts]
    results = sp.match_statements(batch, cands)
    by_id = {int(r["id"]): r for r in cands.to_dict("records")}
    rules = repo.rule_tuples()
    rows, n = [], 0
    for k, (s, res) in enumerate(zip(sts, results)):
        lines = s["rows"]
        for li, sid, kind in res["pairs"]:
            line, st = lines[li], by_id[sid]
            row = {"id": n, "stmt": k, "date": line["date"],
                   "description": line["description"],
                   "amount": line["amount"], "gbp": line["gbp"],
                   "fx_note": line.get("note") or "", "stored_id": sid,
                   "stored_date": st["date"],
                   "stored_desc": st["description"],
                   "stored_amount": st["amount"],
                   "amount_fixed": abs(float(st["amount"])
                                       - float(line["amount"])) >= 0.005,
                   "category": st["category"] if isinstance(
                       st["category"], str) else None,
                   "subcategory": st["subcategory"] if isinstance(
                       st["subcategory"], str) else None,
                   "rule": None, "note": None, "edited": False,
                   "include": True}
            if kind in ("exact", "shop", "sign"):
                # The same purchase: linked, and given the statement's
                # exact amount, date and wording, without asking.
                row.update(state="match", order=9)
            else:
                row.update(state="check", order=ORDER["check"],
                           new=_classified(line, rules, n, "new"))
            rows.append(row)
            n += 1
        for li in res["new"]:
            rows.append({**_classified(lines[li], rules, n, "new"),
                         "stmt": k})
            n += 1
        for sid in res["leftover"]:
            st = by_id[sid]
            rows.append({"id": n, "stmt": k, "state": "orphan",
                         "date": st["date"], "description": st["description"],
                         "amount": st["amount"], "gbp": st["gbp"],
                         "fx_note": "", "stored_id": sid,
                         "category": st["category"] if isinstance(
                             st["category"], str) else None,
                         "subcategory": st["subcategory"] if isinstance(
                             st["subcategory"], str) else None,
                         "rule": None, "note": None, "edited": False,
                         "include": True, "order": ORDER["orphan"]})
            n += 1
    return rows


def _csv_rows(file, card):
    """Preview rows for CSV exports: compared with what is stored; rows a
    stored statement already covers are left out."""
    df = pd.DataFrame(file["rows"])
    lo, hi = df["date"].min(), df["date"].max()
    stored = repo.existing_between(lo, hi, card) if card else df.iloc[0:0]
    df = sp.compare_with_existing(df, stored)
    windows = repo.statement_windows(card) if card else []
    rules = repo.rule_tuples()
    rows = []
    for i, r in enumerate(df.to_dict("records")):
        covered = any(a <= r["date"] <= b for a, b in windows)
        state = "covered" if covered and r["state"] != "exists" else r["state"]
        row = _classified(r, rules, i, state)
        row["near_date"] = r["near_date"]
        if state in ("exists", "covered"):
            row["include"] = False
        rows.append(row)
    return rows


@callback(
    Output(f"{P}-rows", "data"),
    Output(f"{P}-cardnote", "children"),
    Input(f"{P}-file", "data"),
    Input(f"{P}-card", "value"),
)
def _build(file, card):
    last = _last_posted()
    card_line = (f"{card}: last posted {_d(last.get(card))}."
                 if card and last.get(card) else
                 f"{card}: nothing stored yet." if card else "")
    if not file:
        return None, card_line
    wrong = _mismatch(file, card)
    if wrong:
        return None, message(wrong, ok=False)
    if not card:
        return None, message("Pick the card.", ok=False)
    if file["mode"] == "statements":
        rows = _statement_rows(file, card)
        sts = file["statements"]
        lo, hi = sts[0]["window"][0], sts[-1]["window"][1]
        n = sum(len(s["rows"]) for s in sts)
        head = (f"{file['filename']}: {len(sts)} statement"
                f"{'s' * (len(sts) != 1)}, {n} lines, {_d(lo)} – {_d(hi)}.")
        how = file.get("how", "") if card == (file.get("known") or card) else ""
    else:
        rows = _csv_rows(file, card)
        df = pd.DataFrame(file["rows"])
        lo, hi = df["date"].min(), df["date"].max()
        head = f"{file['filename']}: {len(df)} rows, {_d(lo)} – {_d(hi)}."
        how = (file.get("how", "")
               if card == file.get("guess") or not file.get("guess") else "")
    lines = [head, how, card_line]
    stored_last = last.get(card)
    if file["mode"] == "csv" and stored_last and lo > stored_last:
        gap = pd.Timestamp(lo) - pd.Timestamp(stored_last)
        if gap.days > 1:
            lines.append(f"Gap: nothing on {card} between {_d(stored_last)} "
                         f"and {_d(lo)}.")
    return rows, " ".join(x for x in lines if x)


def _matched(r) -> str:
    if r["state"] in ("check", "orphan"):
        return "Stored"
    if r["edited"]:
        return "You"
    if r["rule"] == sp.MANUAL:
        return sp.MANUAL
    return r["rule"] or sp.NO_RULE


def _flag(r) -> str:
    """The Note column: why a row needs a look."""
    if r["state"] == "near":
        return f"Possible duplicate of {_d(r['near_date'])}"
    if r["state"] == "orphan":
        return "Not on the statement"
    if r["state"] == "check":
        bits = []
        if r["stored_date"] != r["date"]:
            bits.append(f"stored on {_d(r['stored_date'])}")
        if sp.squash(r["stored_desc"]).strip() != \
                sp.squash(r["description"]).strip():
            bits.append(f"stored as '{r['stored_desc']}'")
        if r.get("amount_fixed"):
            bits.append(f"stored {gbp(r['stored_amount'])}")
        return "Paired: " + ", ".join(bits) if bits else "Paired"
    return ""


def _in(r) -> str:
    if r["state"] in IN_TEXT:
        yes, no = IN_TEXT[r["state"]]
        return yes if r["include"] else no
    return YES if r["include"] else NO


def _statement_line(st):
    """What one statement says, and whether its lines add up to it."""
    ccy = st.get("currency", "GBP")
    name = st.get("file") or "Statement"
    lo, hi = st.get("window") or (None, None)
    paid = ("paid out", "paid in") if st.get("bank") == "HSBC UK current" \
        else ("debits", "credits")
    head = (f"{name}: {_d(lo)} – {_d(hi)}, {paid[0]} "
            f"{_ccy(st['debits'], ccy)}, {paid[1]} "
            f"{_ccy(st['credits'], ccy)}. ")
    if st.get("adds_up") and not st.get("problems"):
        return html.Div(message(head + "The lines add up to both totals."),
                        style={"marginBottom": "4px"})
    return html.Div(message(head + "Check before importing: "
                            + "; ".join(st["problems"]) + ".", ok=False),
                    style={"marginBottom": "4px"})


def _fx_line(fx, ccy):
    if not fx:
        return None
    text = (f"Converted from {ccy} at each day's close of {fx['source']} "
            f"(stored up to {_d(fx['latest'])})")
    if fx["paid_gbp"]:
        n = fx["paid_gbp"]
        verb = "keeps" if n == 1 else "keep"
        text += (f"; {n} line{'s' * (n != 1)} the bank noted as paid in "
                 f"pounds {verb} that figure")
    text += "."
    if fx["stale"]:
        n = fx["stale"]
        are = "row is" if n == 1 else "rows are"
        return html.Div(message(
            text + f" {n} {are} more than {STALE_RATE_DAYS} days past the "
            "last stored rate - refresh prices first for exact figures.",
            ok=False), style={"marginBottom": "4px"})
    return html.Div(message(text), style={"marginBottom": "4px"})


def _counts(rows) -> dict:
    c = {k: 0 for k in ("match", "check", "new", "orphan", "exists", "near",
                        "covered", "excluded", "take", "placed",
                        "no_rule", "delete")}
    for r in rows:
        s = r["state"]
        c[s] = c.get(s, 0) + 1
        if s in ("new", "near") and r["include"]:
            c["take"] += 1
            c["placed"] += bool(r["category"])
            c["no_rule"] += (not r["category"] and r["rule"] is None)
        if s == "new" and not r["include"]:
            c["excluded"] += 1
        if s == "check" and not r["include"]:
            c["take"] += 1                      # comes in as a new line
            c["delete"] += 1                    # and the stored one goes
        if s == "orphan" and r["include"]:
            c["delete"] += 1
    return c


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-table", "columns"),
    Output(f"{P}-summary", "children"),
    Output(f"{P}-preview", "style"),
    Output(f"{P}-gonote", "children"),
    Input(f"{P}-rows", "data"),
    State(f"{P}-file", "data"),
)
def _table(rows, file):
    if not rows:
        return [], COLUMNS, "", {"display": "none"}, ""
    ccy = (file or {}).get("currency", "GBP")
    shown = sorted((r for r in rows
                    if r["state"] not in ("exists", "match", "covered")),
                   key=lambda r: r["date"], reverse=True)
    shown.sort(key=lambda r: r["order"])          # stable: newest first within
    data = [{"id": r["id"], "in": _in(r), "date": r["date"],
             "description": r["description"], "gbp": r["gbp"],
             "orig_fmt": (_ccy(r["amount"], ccy)
                          + (f" · paid {r['fx_note']}" if r.get("fx_note")
                             else "")),
             "category": r["category"] or "",
             "subcategory": r["subcategory"] or "",
             "matched": _matched(r), "flag": _flag(r)} for r in shown]
    columns = (COLUMNS if ccy == "GBP" else
               COLUMNS[:4] + [ORIGINAL] + COLUMNS[4:])

    c = _counts(rows)
    if file and file.get("mode") == "statements":
        fixed = sum(1 for r in rows if r["state"] == "match"
                    and r.get("amount_fixed"))
        summary = " · ".join(x for x in [
            f"{c['match']} already stored and matched (hidden)"
            + (f", {fixed} of them taking the statement's exact amount"
               if fixed else "") if c["match"] else "",
            f"{c['check']} paired with a stored line that differs"
            if c["check"] else "",
            f"{c['new']} new" if c["new"] else "",
            f"{c['no_rule']} with no rule (shaded)" if c["no_rule"] else "",
            f"{c['orphan']} stored line{'s' * (c['orphan'] != 1)} not on the "
            "statements" if c["orphan"] else "",
            f"{c['excluded']} excluded by you" if c["excluded"] else ""] if x)
        linked = c["match"] + sum(1 for r in rows if r["state"] == "check"
                                  and r["include"])
        go = (f"Stores the statements: links {linked} stored line"
              f"{'s' * (linked != 1)}, adds {c['take']}, deletes "
              f"{c['delete']}.")
        heads = [_statement_line(st) for st in file.get("statements", [])]
    else:
        summary = " · ".join(x for x in [
            f"{c['exists']} already stored (hidden)" if c["exists"] else "",
            f"{c['covered']} inside a stored statement's period (left out)"
            if c["covered"] else "",
            f"{c['take']} to import",
            f"{c['no_rule']} with no rule (shaded)" if c["no_rule"] else "",
            f"{c['near']} possible duplicate{'s' * (c['near'] != 1)} "
            "(left out)" if c["near"] else "",
            f"{c['excluded']} excluded by you" if c["excluded"] else ""] if x)
        queued = c["take"] - c["placed"]
        go = (f"Imports {c['take']} row{'s' * (c['take'] != 1)}: "
              f"{c['placed']} categorised, {queued} to the Manual Queue."
              if c["take"] else "Nothing to import.")
        heads = []
    heads.append(_fx_line((file or {}).get("fx"), ccy))
    heads = [h for h in heads if h is not None]
    return (data, columns, heads + [html.Div(summary)] if heads else summary,
            {**theme.CARD, "display": "block"}, go)


# --- Editing the preview -----------------------------------------------------

@callback(
    Output(f"{P}-table", "selected_rows"),
    Output(f"{P}-table", "selected_row_ids"),
    Input(f"{P}-norule", "n_clicks"),
    Input(f"{P}-none", "n_clicks"),
    Input(f"{P}-table", "data"),
    State(f"{P}-rows", "data"),
    prevent_initial_call=True,
)
def _select(_norule, _none, data, rows):
    """Every change to the preview clears the ticks, so the next edit starts
    from nothing; Select no-rule rows ticks the ones still to do."""
    if ctx.triggered_id == f"{P}-norule":
        return select_rows(data, [
            r["id"] for r in rows or [] if r["state"] in ("new", "near")
            and not r["edited"] and r["rule"] is None and r["include"]])
    return [], []


@callback(
    Output(f"{P}-kw", "value"),
    Output(f"{P}-remember", "value"),
    Input(f"{P}-table", "selected_row_ids"),
    State(f"{P}-rows", "data"),
)
def _suggest(selected, rows):
    by_id = {r["id"]: r for r in rows or []}
    for i in selected or []:
        r = by_id.get(i)
        if r and r["state"] in ("new", "near"):
            return sp.remember_keyword(r["description"]), []
    return "", []


@callback(
    Output(f"{P}-rows", "data", allow_duplicate=True),
    Output(f"{P}-msg", "children"),
    Input(f"{P}-apply", "n_clicks"),
    Input(f"{P}-exclude", "n_clicks"),
    Input(f"{P}-include", "n_clicks"),
    State(f"{P}-table", "selected_row_ids"),
    State(f"{P}-rows", "data"),
    State(f"{P}-cat", "value"),
    State(f"{P}-sub", "value"),
    State(f"{P}-note", "value"),
    State(f"{P}-remember", "value"),
    State(f"{P}-kw", "value"),
    prevent_initial_call=True,
)
def _edit(_a, _x, _i, selected, rows, cat, sub, note, remember, kw):
    if not rows:
        return no_update, ""
    if not selected:
        return no_update, message("Tick at least one row first.", ok=False)
    chosen = set(selected)
    trig = ctx.triggered_id
    if trig in (f"{P}-exclude", f"{P}-include"):
        flag = trig == f"{P}-include"
        for r in rows:
            if r["id"] in chosen:
                r["include"] = flag
        word = "Included" if flag else "Excluded"
        return rows, message(f"{word} {len(chosen)} row"
                             f"{'s' * (len(chosen) != 1)}.")

    if not (cat and sub):
        return no_update, message("Pick a category and sub-category.",
                                  ok=False)
    picked = [r for r in rows if r["id"] in chosen
              and r["state"] in ("new", "near", "check")]
    for r in picked:
        r.update(category=cat, subcategory=sub, edited=True)
        if note:
            r["note"] = note.strip()
    text = (f"Set {len(picked)} row{'s' * (len(picked) != 1)} to {cat} / "
            f"{sub}.")
    if len(picked) < len(chosen):
        text += " Lines not on the statement keep theirs."

    if "y" in (remember or []):
        plan, said = remember_rule(
            [{"description": r["description"], "amount": r["gbp"]}
             for r in picked], kw, cat, sub, note)
        if not plan:
            return rows, message(f"{text} {said}", ok=False)
        rules = repo.rule_tuples()
        more = 0
        for r in rows:
            if (r["state"] in ("new", "near") and not r["edited"]
                    and r["rule"] in (None, sp.MANUAL)):
                m = sp.classify(r["description"], rules, r["gbp"])
                if m.rule and m.rule != sp.MANUAL:
                    r.update(category=m.category, subcategory=m.subcategory,
                             rule=m.rule, note=m.note)
                    more += 1
        text += f" {said[:-1]}"
        text += (f"; it also matched {more} more row{'s' * (more != 1)} here."
                 if more else ".")
    return rows, message(text)


# --- Import ------------------------------------------------------------------

def _new_row(r, ccy):
    row = {"date": r["date"], "description": r["description"],
           "amount": r["gbp"], "category": r["category"],
           "subcategory": r["subcategory"], "note": r.get("note"),
           "status": "confirmed" if r["category"] else "pending"}
    if ccy != "GBP":
        row.update(amount_orig=r["amount"], currency=ccy)
    return row


def _import_statements(rows, file, card):
    ccy = file.get("currency", "GBP")
    batches = []
    for k, st in enumerate(file["statements"]):
        lo, hi = st["window"]
        rec = {"bank": st["bank"], "account_ref": st["ref"], "currency": ccy,
               "period_from": lo, "period_to": hi,
               "statement_date": st["date"],
               "previous_balance": st["previous_balance"],
               "new_balance": st["new_balance"], "debits": st["debits"],
               "credits": st["credits"], "lines": len(st["rows"]),
               "filename": st["file"]}
        b = {"record": rec, "ref": st["ref"], "link": [], "insert": [],
             "delete": []}
        for r in (x for x in rows if x.get("stmt") == k):
            s = r["state"]
            if s in ("match", "check") and r["include"]:
                # The statement's amount, to the penny, always wins. Spacing
                # alone is not a re-wording: the stored line keeps its export
                # wording. The old text goes to the note only when it said
                # more than the statement (a note typed into it), not for
                # ')))', a trailing amount or a dropped city.
                same = sp.squash(r["stored_desc"]).strip() == \
                    sp.squash(r["description"]).strip()
                link = {"id": r["stored_id"], "date": r["date"],
                        "description": (r["stored_desc"] if same
                                        else r["description"]),
                        "amount": r["gbp"],
                        "old_description": (
                            r["stored_desc"] if sp.extra_wording(
                                r["stored_desc"], r["description"])
                            else None)}
                if ccy != "GBP":
                    link["amount_orig"] = r["amount"]
                if r["edited"]:
                    link.update(category=r["category"],
                                subcategory=r["subcategory"])
                b["link"].append(link)
            elif s == "check":                  # kept apart
                b["insert"].append(_new_row(r["new"], ccy))
                b["delete"].append(r["stored_id"])
            elif s == "new" and r["include"]:
                b["insert"].append(_new_row(r, ccy))
            elif s == "orphan" and r["include"]:
                b["delete"].append(r["stored_id"])
        batches.append(b)
    n_lines = sum(len(s["rows"]) for s in file["statements"])
    res = repo.apply_statements(card, file["filename"], batches, n_lines)
    text = (f"{card}: {len(batches)} statement{'s' * (len(batches) != 1)} "
            f"stored. {res['linked']} stored line{'s' * (res['linked'] != 1)} "
            f"linked" + (f" ({res['amounts']} given the statement's exact "
                         "amount)" if res["amounts"] else "")
            + (f", {res['reworded']} with their old wording kept as a note"
               if res["reworded"] else "")
            + f", {res['inserted']} added, {res['deleted']} deleted.")
    over = repo.statement_overview()
    over = over[over["id"].isin(res["statements"])]
    parts = [message(text)] + [_check_line(r, ccy) for r in
                               over.to_dict("records")]
    return parts


def _check_line(r, ccy):
    where = f"{r['card']}, {_d(r['period_from'])} – {_d(r['period_to'])}"
    if r["ok"]:
        return message(f"{where}: reconciled - {r['lines_stored']} lines add "
                       "up to the statement" + (", and its opening balance is "
                                                "the previous closing one"
                                                if r["chain_ok"] else "")
                       + ".")
    bits = []
    if not r["lines_ok"]:
        bits.append(f"stored lines {_ccy(-r['lines_debits'], ccy)} / "
                    f"{_ccy(r['lines_credits'], ccy)} against the statement's "
                    f"{_ccy(-r['debits'], ccy)} / {_ccy(r['credits'], ccy)}")
    if r["chain_ok"] is False:
        bits.append("opening balance is not the previous closing one")
    if r["unlinked"]:
        bits.append(f"{r['unlinked']} line{'s' * (r['unlinked'] != 1)} in the "
                    "period on no statement")
    return message(f"{where}: " + "; ".join(bits) + ".", ok=False)


@callback(
    Output(f"{P}-result", "children", allow_duplicate=True),
    Output(f"{P}-file", "data", allow_duplicate=True),
    Output(f"{P}-upload-wrap", "children"),
    Output(f"{P}-refresh", "data"),
    Input(f"{P}-go", "n_clicks"),
    State(f"{P}-rows", "data"),
    State(f"{P}-file", "data"),
    State(f"{P}-card", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _import(_n, rows, file, card, refresh):
    if not rows or not file:
        return no_update, no_update, no_update, no_update
    if not card:
        return (message("Pick the card first.", ok=False), no_update,
                no_update, no_update)
    wrong = _mismatch(file, card)
    if wrong:
        return message(wrong, ok=False), no_update, no_update, no_update
    if file["mode"] == "statements":
        parts = _import_statements(rows, file, card)
        return (html.Div([html.Div(p) for p in parts], style=theme.CARD),
                None, _upload_box(), (refresh or 0) + 1)

    ccy = file.get("currency", "GBP")
    # Rows already stored go along too, in file order: the counted
    # de-duplication in repo.import_rows needs them to know that a second
    # identical row in the file is new.
    send = [_new_row(r, ccy) for r in sorted(rows, key=lambda r: r["id"])
            if r["state"] == "exists" or (r["state"] in ("new", "near")
                                          and r["include"])]
    if not any(r["state"] in ("new", "near") and r["include"] for r in rows):
        return (html.Div(message(f"Nothing new to import from "
                                 f"{file['filename']}."), style=theme.CARD),
                None, _upload_box(), no_update)
    res = repo.import_rows(card, file["filename"], send, len(rows))
    c = _counts(rows)
    queued = c["take"] - c["placed"]
    text = (f"Imported {res['inserted']} row"
            f"{'s' * (res['inserted'] != 1)} to {card} from "
            f"{file['filename']}"
            + (f" ({queued} to the Manual Queue)" if queued else "") + ". "
            + (f"{res['skipped']} already stored. " if res["skipped"] else "")
            + (f"{c['covered']} inside a stored statement's period left out. "
               if c["covered"] else "")
            + (f"{c['excluded']} left out by you." if c["excluded"] else ""))
    return (html.Div(message(text.strip()), style=theme.CARD), None,
            _upload_box(), (refresh or 0) + 1)


# --- Card coverage -----------------------------------------------------------

@callback(
    Output(f"{P}-coverage", "children"),
    Input(f"{P}-refresh", "data"),
)
def _coverage(_r):
    gap_days = int(settings.get("SPEND_GAP_DAYS", 10))
    stale_days = int(settings.get("SPEND_STALE_DAYS", 14))
    summary = repo.card_summary()
    summary = summary[summary["active"] == 1]
    if summary.empty:
        return html.Div("No cards yet - add one below.", style=NOTE)
    today = pd.Timestamp.today().normalize()
    since = (today - pd.DateOffset(years=1)).strftime("%Y-%m-%d")
    dates = repo.posting_dates(since)
    last_file = repo.last_imports().set_index("card")
    sts = repo.statements()
    last_st = (sts.groupby("card")["period_to"].max().to_dict()
               if not sts.empty else {})

    th = {"padding": "6px 9px", "fontSize": "10.5px", "fontWeight": 600,
          "letterSpacing": "0.05em", "textTransform": "uppercase",
          "color": theme.SURFACE, "background": theme.INK, "textAlign": "left"}
    td = {"padding": "6px 9px", "fontSize": "12px",
          "borderBottom": f"1px solid {theme.LINE_SOFT}", "color": theme.TEXT,
          "verticalAlign": "top"}
    pill = {"padding": "1px 7px", "borderRadius": "9px", "fontSize": "10.5px",
            "fontWeight": 600, "marginLeft": "6px"}
    head = html.Thead(html.Tr([html.Th(h, style=th) for h in
                               ["Card", "Last posted", "Since",
                                "Last statement", "Gaps in the last year",
                                "Last import"]]))
    body = []
    for r in summary.to_dict("records"):
        card = r["name"]
        last = r["last_date"] if isinstance(r["last_date"], str) else None
        days = (today - pd.Timestamp(last)).days if last else None
        due = days is not None and days > stale_days
        since_cell = [f"{days} day{'s' * (days != 1)}" if days is not None
                      else "—"]
        if due:
            since_cell.append(html.Span("Due", style={
                **pill, "background": theme.NEEDLE, "color": theme.SURFACE}))
        found = sp.gaps(dates.loc[dates["card"] == card, "date"], gap_days)
        gap_cell = ([html.Div(f"{_d(a)} – {_d(b)} ({n} days)",
                              style={"color": theme.NEGATIVE})
                     for a, b, n in found]
                    or [html.Span("none", style={"color": theme.NEUTRAL})])
        imp = (last_file.loc[card] if card in last_file.index else None)
        imp_cell = (f"{imp['filename']} · "
                    f"{pd.Timestamp(imp['imported_at']):%d %b %Y}"
                    if imp is not None else "—")
        ccy = r.get("currency") or "GBP"
        name_cell = [card] + ([html.Span(ccy, style={
            **pill, "background": theme.LINE_SOFT, "color": theme.SLATE})]
            if ccy != "GBP" else [])
        body.append(html.Tr([
            html.Td(name_cell, style={**td, "fontWeight": 600}),
            html.Td(_d(last) or "—", style={**td, **theme.NUM}),
            html.Td(since_cell, style={**td, **theme.NUM}),
            html.Td(_d(last_st.get(card)) or "—", style={**td, **theme.NUM}),
            html.Td(gap_cell, style=td),
            html.Td(imp_cell, style={**td, "color": theme.SLATE}),
        ]))
    note = html.Div(f"A gap is more than {gap_days} days in a row with "
                    f"nothing posted; Due is more than {stale_days} days since "
                    "the last transaction. Both are under Data → Config. The "
                    "Statements tab shows which months are reconciled.",
                    style={**NOTE, "marginTop": "8px", "fontSize": "10.5px",
                           "color": theme.NEUTRAL})
    return html.Div([html.Table([head, html.Tbody(body)],
                                style={"width": "100%",
                                       "borderCollapse": "collapse"}), note])
