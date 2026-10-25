"""
Spending -> Import.

Upload HSBC exports or PDF statements - any file name, several at once. The
card is chosen for you: the one whose stored transactions the files overlap
most (exports usually overlap the last download), otherwise the only card in
the files' currency, otherwise the last card you imported. Change it if it
guessed wrong; Add card creates a new one on the spot, in the files'
currency.

The preview hides rows already stored and lists the rest, rows no rule
matched first. Tick rows to set a category; Remember turns the keyword into
a rule, and the other rows in the files it matches are filled in at once.
A row matching a stored row's description and amount within three days, but
not its date, is flagged as a possible duplicate and left out unless you
include it - the July double-count would have shown up here.

Import stores the included rows. Categorised rows are confirmed; the rest go
to the Manual Queue.

PDF statements (core/statements.py):
    HSBC UK credit card   read on the posting date and in the export's
                          wording - useful when the export is no longer
                          available (a replaced card). Reconciled against the
                          statement's own totals after import.
    HSBC Turkiye card     lira, dated by transaction date. Converted to
                          pounds at each day's GBP/TRY close from the prices
                          the portfolio keeps (YF:GBPTRY=X); a line the bank
                          noted as paid in pounds keeps that figure. The lira
                          amount is stored too, and re-uploads are compared
                          on it, so a statement imported twice adds nothing.
Every statement is checked against its own totals before import.

Files in different currencies cannot be imported together, and a file can
only go to a card in its own currency.

Above the upload, Cards shows when each card was last posted to and any
stretch with no transactions - usually a file never imported. Below it, any
period can be checked against a statement's totals by hand.
"""

from __future__ import annotations

import base64

import pandas as pd
from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)

from core import theme, spending as sp, statements
from core.repo import settings
from core.repo import spending as repo
from ui.spend import (TABLE, money_cells, btn, field, bar, header, message,
                      gbp, assign_bar, register_subcategories, select_rows, INPUT,
                      NOTE)

P = "sp-im"

COLUMNS = [
    {"name": "In", "id": "in"},
    {"name": "Date", "id": "date"},
    {"name": "Description", "id": "description"},
    {"name": "Amount", "id": "amount_fmt"},
    {"name": "Category", "id": "category"},
    {"name": "Sub-category", "id": "subcategory"},
    {"name": "Matched by", "id": "matched"},
    {"name": "Note", "id": "note"},
]
# Shown after Amount when the files are not in pounds.
ORIGINAL = {"name": "Original", "id": "orig_fmt"}

# Display order of preview rows, fixed when the preview is built so rows do
# not jump around while you categorise them.
ORDER = {"no rule": 0, "near": 1, "manual": 2, "rule": 3}
YES, NO = "✓", "—"

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
    every = repo.card_names(active_only=False)
    num = {**INPUT, **theme.NUM, "width": "120px"}
    return html.Div([
        header("Import", "Upload HSBC exports or PDF statements (UK credit "
               "card or Türkiye) - any file name, several at once. The card "
               "is picked from what the files overlap; change it if it "
               "guessed wrong."),
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
            assign_bar(P, "Set on selected", extra=[
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
                style_cell_conditional=money_cells("amount_fmt", "orig_fmt") + [
                    {"if": {"column_id": "in"}, "width": "34px",
                     "textAlign": "center"},
                    {"if": {"column_id": "description"}, "width": "34%"}],
                style_data_conditional=[
                    {"if": {"filter_query": '{matched} = "No rule"'},
                     "backgroundColor": theme.TINT_NEAR},
                    {"if": {"filter_query": f'{{in}} = "{NO}"'},
                     "color": theme.NEUTRAL},
                    {"if": {"column_id": "orig_fmt"}, "color": theme.SLATE},
                    {"if": {"column_id": "note",
                            "filter_query": '{note} != ""'},
                     "color": theme.NEGATIVE, "fontWeight": 600}],
                **TABLE),
            html.Div([
                html.Button("Import", id=f"{P}-go", n_clicks=0,
                            style=btn("go")),
                html.Span(id=f"{P}-gonote", style=NOTE),
            ], style={"display": "flex", "gap": "14px",
                      "alignItems": "center", "marginTop": "14px"}),
        ]),

        html.Div([
            html.Div("Check a period against a statement",
                     style=theme.CARD_TITLE),
            bar(
                field("Card", dcc.Dropdown(id=f"{P}-rc-card", options=every,
                                           value=card, clearable=False,
                                           style={"fontSize": "12.5px"}),
                      width="180px"),
                field("Posted from", dcc.DatePickerSingle(
                    id=f"{P}-rc-from", display_format="DD MMM YYYY")),
                field("To", dcc.DatePickerSingle(
                    id=f"{P}-rc-to", display_format="DD MMM YYYY")),
                field("Statement debits", dcc.Input(
                    id=f"{P}-rc-debits", type="number", debounce=False,
                    placeholder="0.00", style=num)),
                field("Statement credits", dcc.Input(
                    id=f"{P}-rc-credits", type="number", debounce=False,
                    placeholder="0.00", style=num)),
                html.Button("Check", id=f"{P}-rc-go", n_clicks=0,
                            style=btn("primary")),
            ),
            html.Div("Use the statement's 'received by us' dates: from the "
                     "day after the previous statement date to this "
                     "statement's date. A card in another currency is "
                     "checked in that currency.",
                     style={**NOTE, "marginTop": "8px"}),
            html.Div(id=f"{P}-rc-result", style={"marginTop": "8px",
                                                 "minHeight": "16px"}),
        ], style=theme.CARD),

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
        children=html.Div(["Drop CSV exports or PDF statements here - "
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


def _last_posted() -> dict:
    """Card -> last posted date, for cards that have any rows."""
    df = repo.card_summary()
    return {n: d for n, d in zip(df["name"], df["last_date"])
            if isinstance(d, str)}


def _ccy(value, currency: str) -> str:
    """Money as the preview shows it; other currencies get their code."""
    return gbp(value) if currency == "GBP" else f"{gbp(value)} {currency}"


def _label(names: list[str]) -> str:
    """What an import is recorded as: the file, or the first and a count."""
    if len(names) == 1:
        return names[0]
    return f"{names[0]} + {len(names) - 1} more"


# --- Upload and card ---------------------------------------------------------

def _read(contents: str, filename: str):
    """One uploaded file -> (rows, currency, statement dict or None)."""
    raw = base64.b64decode(contents.split(",", 1)[1])
    if (filename or "").lower().endswith(".pdf"):
        st = statements.parse_statement_pdf(raw)
        info = {"file": filename, "bank": st.bank, "currency": st.currency,
                "date": st.statement_date, "period": st.period,
                "debits": st.debits, "credits": st.credits,
                "rows_debits": st.rows_debits,
                "rows_credits": st.rows_credits,
                "adds_up": st.adds_up, "problems": st.problems}
        return st.rows, st.currency, info
    return sp.parse_bank_csv(raw), "GBP", None


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
    frames, found, by_ccy = [], [], {}
    for body, name in zip(contents, filenames):
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
            found.append(info)
    if len(by_ccy) > 1:
        parts = "; ".join(f"{', '.join(v)} in {k}" for k, v in by_ccy.items())
        return None, no_update, message(
            f"These files are in different currencies ({parts}). Upload each "
            "card's files on their own.", ok=False)
    ccy = next(iter(by_ccy))
    df = sp.combine_files(frames)
    df["note"] = (df["note"].fillna("").astype(str) if "note" in df.columns
                  else "")
    fx = None
    if ccy == "GBP":
        df["gbp"] = df["amount"]
        df["rate_date"] = None
    else:
        rates = repo.gbp_rates(ccy)
        try:
            df = sp.convert_to_gbp(df, rates)
        except ValueError as exc:
            return None, no_update, message(
                f"Cannot convert {ccy} to pounds: {exc}. The rate comes from "
                f"the stored prices of YF:GBP{ccy}=X - refresh prices and "
                "upload again.", ok=False)
        lag = (pd.to_datetime(df["date"])
               - pd.to_datetime(df["rate_date"])).dt.days
        fx = {"source": f"YF:GBP{ccy}=X", "latest": rates["date"].max(),
              "paid_gbp": int((df["note"].str.endswith(" GBP")).sum()),
              "stale": int((lag > STALE_RATE_DAYS).sum())}

    # The card: most overlap among cards in this currency, else the only
    # card in it, else keep the selection.
    same = repo.cards_in(ccy, active_only=False)
    stored = repo.existing_between(df["date"].min(), df["date"].max())
    stored = stored[stored["card"].isin(same)]
    guess, n = sp.guess_card(df[["date", "description", "amount"]], stored)
    active = repo.cards_in(ccy)
    if guess and guess in active:
        how = (f"Picked {guess}: {n} of the rows are already stored there.")
        card = guess
    elif len(active) == 1:
        card = active[0]
        how = (f"Picked {card}: the only {ccy} card." if ccy != "GBP" else
               f"Picked {card}: the only card.")
        guess = card
    elif not active:
        how = (f"No card is set up for {ccy} - add one (it will be a {ccy} "
               "card).")
    else:
        how = ("No overlap with stored rows - using "
               f"{card}. Check it is the right card." if card
               else "Pick the card.")
    names = list(filenames)
    data = {"files": names, "filename": _label(names),
            "rows": df.to_dict("records"), "currency": ccy, "how": how,
            "guess": guess, "statements": found, "fx": fx}
    return data, card, ""


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
    if have == want:
        return None
    return (f"{card} is a {have} card and these files are in {want} - pick "
            f"a {want} card or add one.")


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
    df = pd.DataFrame(file["rows"])
    lo, hi = df["date"].min(), df["date"].max()
    stored = repo.existing_between(lo, hi, card) if card else df.iloc[0:0]
    df = sp.compare_with_existing(df, stored)
    rules = repo.rule_tuples()

    rows = []
    for i, r in enumerate(df.to_dict("records")):
        m = sp.classify(r["description"], rules)
        kind = ("near" if r["state"] == "near" else
                "manual" if m.rule == sp.MANUAL else
                "rule" if m.rule else "no rule")
        rows.append({
            "id": i, "date": r["date"], "description": r["description"],
            "amount": r["amount"], "gbp": r["gbp"],
            "fx_note": r.get("note") or "", "state": r["state"],
            "near_date": r["near_date"], "category": m.category,
            "subcategory": m.subcategory, "rule": m.rule,
            "edited": False, "include": r["state"] == "new",
            "order": ORDER[kind]})

    # The guess note only while the guessed card is still the one selected.
    how = (file.get("how", "")
           if card == file.get("guess") or not file.get("guess") else "")
    lines = [f"{file['filename']}: {len(df)} rows, {_d(lo)} – "
             f"{_d(hi)}.", how, card_line]
    stored_last = last.get(card)
    if stored_last and lo > stored_last:
        gap = pd.Timestamp(lo) - pd.Timestamp(stored_last)
        if gap.days > 1:
            lines.append(f"Gap: nothing on {card} between {_d(stored_last)} "
                         f"and {_d(lo)}.")
    return rows, " ".join(x for x in lines if x)


def _matched(r) -> str:
    if r["edited"]:
        return "You"
    if r["rule"] == sp.MANUAL:
        return sp.MANUAL
    return r["rule"] or sp.NO_RULE


def _statement_line(st):
    """What one statement says, and whether its lines add up to it."""
    ccy = st.get("currency", "GBP")
    name = st.get("file") or "Statement"
    if st.get("period"):
        lo, hi = st["period"]
        head = (f"{name}: statement {_d(st['date'])}, posted {_d(lo)} "
                f"– {_d(hi)}: debits {_ccy(st['debits'], ccy)}, "
                f"credits {_ccy(st['credits'], ccy)}. ")
    elif st.get("date"):
        head = (f"{name}: statement {_d(st['date'])}: spending and fees "
                f"{_ccy(st['debits'], ccy)}, payments and refunds "
                f"{_ccy(st['credits'], ccy)}. ")
    else:
        head = f"{name}: "
    if st["adds_up"]:
        return html.Div(message(head + "The lines add up to both totals."),
                        style={"marginBottom": "6px"})
    return html.Div(message(head + "Check before importing: "
                            + "; ".join(st["problems"]) + ".", ok=False),
                    style={"marginBottom": "6px"})


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
            ok=False), style={"marginBottom": "6px"})
    return html.Div(message(text), style={"marginBottom": "6px"})


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
    shown = sorted((r for r in rows if r["state"] != "exists"),
                   key=lambda r: r["date"], reverse=True)
    shown.sort(key=lambda r: r["order"])          # stable: newest first within
    data = [{"id": r["id"], "in": YES if r["include"] else NO,
             "date": r["date"], "description": r["description"],
             "amount_fmt": gbp(r["gbp"]),
             "orig_fmt": (_ccy(r["amount"], ccy)
                          + (f" · paid {r['fx_note']}"
                             if r.get("fx_note") else "")),
             "category": r["category"] or "",
             "subcategory": r["subcategory"] or "",
             "matched": _matched(r),
             "note": (f"Possible duplicate of {_d(r['near_date'])}"
                      if r["state"] == "near" else "")} for r in shown]
    columns = (COLUMNS if ccy == "GBP" else
               COLUMNS[:4] + [ORIGINAL] + COLUMNS[4:])

    n_exists = sum(r["state"] == "exists" for r in rows)
    take = [r for r in rows if r["state"] != "exists" and r["include"]]
    placed = sum(bool(r["category"]) for r in take)
    queued = len(take) - placed
    near = sum(r["state"] == "near" for r in rows)
    excluded = sum(r["state"] == "new" and not r["include"] for r in rows)
    no_rule = sum(not r["category"] and r["rule"] is None for r in take)
    summary = " · ".join(x for x in [
        f"{n_exists} already stored (hidden)" if n_exists else "",
        f"{len(take)} to import",
        f"{no_rule} with no rule (shaded)" if no_rule else "",
        f"{near} possible duplicate{'s' * (near != 1)} (left out)"
        if near else "",
        f"{excluded} excluded by you" if excluded else ""] if x)
    go = (f"Imports {len(take)} row{'s' * (len(take) != 1)}: {placed} "
          f"categorised, {queued} to the Manual Queue." if take
          else "Nothing to import.")
    heads = [_statement_line(st) for st in (file or {}).get("statements", [])]
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
            r["id"] for r in rows or [] if r["state"] != "exists"
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
        if r and sp.remembers(r["description"]):
            return sp.suggest_keyword(r["description"]), []
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
    State(f"{P}-remember", "value"),
    State(f"{P}-kw", "value"),
    prevent_initial_call=True,
)
def _edit(_a, _x, _i, selected, rows, cat, sub, remember, kw):
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
    for r in rows:
        if r["id"] in chosen:
            r.update(category=cat, subcategory=sub, edited=True)
    text = f"Set {len(chosen)} row{'s' * (len(chosen) != 1)} to {cat} / {sub}."

    if "y" in (remember or []):
        descs = [r["description"] for r in rows if r["id"] in chosen]
        problem = sp.keyword_problem(descs, kw)
        if problem:
            return rows, message(f"{text} Not remembered: {problem}",
                                 ok=False)
        keyword = repo.save_rule(kw.strip(), cat, sub)
        rules = repo.rule_tuples()
        more = 0
        for r in rows:
            if (r["state"] != "exists" and not r["edited"]
                    and r["rule"] is None):
                m = sp.classify(r["description"], rules)
                if m.rule:
                    r.update(category=m.category, subcategory=m.subcategory,
                             rule=m.rule)
                    more += 1
        text += f" Rule '{keyword}' saved"
        text += (f"; it also matched {more} more row{'s' * (more != 1)} in "
                 "this file." if more else ".")
    return rows, message(text)


# --- Import ------------------------------------------------------------------

def _reconcile_all(card, file):
    """Each posting-dated statement in the files against what is stored."""
    return [_reconcile_line(card, *st["period"], st["debits"], st["credits"])
            for st in (file or {}).get("statements", [])
            if st.get("period") and st.get("debits") is not None]


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
    ccy = file.get("currency", "GBP")
    # Rows already stored go along too, in file order: the counted
    # de-duplication in repo.import_rows needs them to know that a second
    # identical row in the file is new.
    send = []
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["state"] == "exists" or r["include"]:
            row = {"date": r["date"], "description": r["description"],
                   "amount": r["gbp"], "category": r["category"],
                   "subcategory": r["subcategory"],
                   "status": "confirmed" if r["category"] else "pending"}
            if ccy != "GBP":
                row.update(amount_orig=r["amount"], currency=ccy)
            send.append(row)
    if not any(r["state"] != "exists" and r["include"] for r in rows):
        # Nothing new: no import is recorded, but a statement is still
        # checked against what is stored.
        parts = [message(f"Nothing new to import from {file['filename']} - "
                         "every line is already stored.")]
        parts += _reconcile_all(card, file)
        return (html.Div([html.Div(p) for p in parts], style=theme.CARD),
                None, _upload_box(), no_update)
    res = repo.import_rows(card, file["filename"], send, len(rows))
    queued = sum(1 for r in rows if r["state"] != "exists" and r["include"]
                 and not r["category"])
    excluded = sum(1 for r in rows if r["state"] != "exists"
                   and not r["include"])
    text = (f"Imported {res['inserted']} row"
            f"{'s' * (res['inserted'] != 1)} to {card} from "
            f"{file['filename']}"
            + (f" ({queued} to the Manual Queue)" if queued else "") + ". "
            + (f"{res['skipped']} already stored. " if res["skipped"] else "")
            + (f"{excluded} left out." if excluded else ""))
    parts = [message(text.strip())] + _reconcile_all(card, file)
    return (html.Div([html.Div(p) for p in parts], style=theme.CARD), None,
            _upload_box(), (refresh or 0) + 1)


# --- Reconciling -------------------------------------------------------------

def _reconcile_line(card, lo, hi, debits, credits):
    """Stored rows of a card over a period against a statement's totals - in
    the card's own currency, which is what its statements show."""
    ccy = repo.card_currency(card)
    stored = repo.card_rows_between(card, lo, hi)
    if ccy != "GBP":
        stored = stored.assign(
            amount=stored["amount_orig"].fillna(stored["amount"]))
    r = statements.reconcile(stored, float(debits or 0), float(credits or 0))
    where = f"{card}, {_d(lo)} – {_d(hi)}"
    if r["ok"]:
        return message(f"{where}: {r['rows']} rows, debits "
                       f"{_ccy(r['debits'], ccy)} and credits "
                       f"{_ccy(r['credits'], ccy)} - matches the statement.")
    bits = []
    if abs(r["debits_diff"]) >= 0.005:
        bits.append(f"debits {_ccy(r['debits'], ccy)} against "
                    f"{_ccy(debits, ccy)} "
                    f"({'+' if r['debits_diff'] > 0 else ''}"
                    f"{r['debits_diff']:,.2f})")
    if abs(r["credits_diff"]) >= 0.005:
        bits.append(f"credits {_ccy(r['credits'], ccy)} against "
                    f"{_ccy(credits, ccy)} "
                    f"({'+' if r['credits_diff'] > 0 else ''}"
                    f"{r['credits_diff']:,.2f})")
    hint = (" More stored than the statement usually means a duplicate; "
            "less means missing rows.")
    return message(f"{where}: {r['rows']} rows, " + "; ".join(bits) + "."
                   + hint, ok=False)


@callback(
    Output(f"{P}-rc-result", "children"),
    Input(f"{P}-rc-go", "n_clicks"),
    State(f"{P}-rc-card", "value"),
    State(f"{P}-rc-from", "date"),
    State(f"{P}-rc-to", "date"),
    State(f"{P}-rc-debits", "value"),
    State(f"{P}-rc-credits", "value"),
    prevent_initial_call=True,
)
def _check(_n, card, lo, hi, debits, credits):
    if not (card and lo and hi):
        return message("Pick the card and both dates.", ok=False)
    if debits in (None, "") and credits in (None, ""):
        return message("Type the statement's debits and credits.", ok=False)
    return _reconcile_line(card, lo[:10], hi[:10], debits or 0, credits or 0)


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
                                "Gaps in the last year", "Last import"]]))
    body = []
    for r in summary.to_dict("records"):
        card = r["name"]
        last = r["last_date"] if isinstance(r["last_date"], str) else None
        days = (today - pd.Timestamp(last)).days if last else None
        due = days is not None and days > stale_days
        since_cell = [f"{days} day{'s' * (days != 1)}" if days is not None
                      else "\u2014"]
        if due:
            since_cell.append(html.Span("Due", style={
                **pill, "background": theme.NEEDLE, "color": theme.SURFACE}))
        found = sp.gaps(dates.loc[dates["card"] == card, "date"], gap_days)
        gap_cell = ([html.Div(f"{_d(a)} \u2013 {_d(b)} ({n} days)",
                              style={"color": theme.NEGATIVE})
                     for a, b, n in found]
                    or [html.Span("none", style={"color": theme.NEUTRAL})])
        imp = (last_file.loc[card] if card in last_file.index else None)
        imp_cell = (f"{imp['filename']} \u00b7 "
                    f"{pd.Timestamp(imp['imported_at']):%d %b %Y}"
                    if imp is not None else "\u2014")
        ccy = r.get("currency") or "GBP"
        name_cell = [card] + ([html.Span(ccy, style={
            **pill, "background": theme.LINE_SOFT, "color": theme.SLATE})]
            if ccy != "GBP" else [])
        body.append(html.Tr([
            html.Td(name_cell, style={**td, "fontWeight": 600}),
            html.Td(_d(last) or "\u2014", style={**td, **theme.NUM}),
            html.Td(since_cell, style={**td, **theme.NUM}),
            html.Td(gap_cell, style=td),
            html.Td(imp_cell, style={**td, "color": theme.SLATE}),
        ]))
    note = html.Div(f"A gap is more than {gap_days} days in a row with "
                    f"nothing posted; Due is more than {stale_days} days since "
                    "the last transaction. Both are under Data \u2192 Config.",
                    style={**NOTE, "marginTop": "8px", "fontSize": "10.5px",
                           "color": theme.NEUTRAL})
    return html.Div([html.Table([head, html.Tbody(body)],
                                style={"width": "100%",
                                       "borderCollapse": "collapse"}), note])
