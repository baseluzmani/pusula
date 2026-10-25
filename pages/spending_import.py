"""
Spending -> Import.

Upload an HSBC export - any file name. The card is chosen for you: the one
whose stored transactions the file overlaps most (exports usually overlap the
last download), otherwise the last card you imported. Change it if it guessed
wrong; Add card creates a new one on the spot.

The preview hides rows already stored and lists the rest, rows no rule
matched first. Tick rows to set a category; Remember turns the keyword into
a rule, and the other rows in the file it matches are filled in at once.
A row matching a stored row's description and amount within three days, but
not its date, is flagged as a possible duplicate and left out unless you
include it - the July double-count would have shown up here.

Import stores the included rows. Categorised rows are confirmed; the rest go
to the Manual Queue.

An HSBC credit-card PDF statement can be uploaded instead of a CSV - useful
when the export is no longer available (a replaced card). Its lines are read
on the posting date and in the export's wording (core/statements.py), checked
against the statement's own debit and credit totals before import, and the
card is reconciled against the statement again afterwards.

Above the upload, Cards shows when each card was last posted to and any
stretch with no transactions - usually a file never imported. Below it, any
posting period can be checked against a statement's totals by hand.
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

# Display order of preview rows, fixed when the preview is built so rows do
# not jump around while you categorise them.
ORDER = {"no rule": 0, "near": 1, "manual": 2, "rule": 3}
YES, NO = "✓", "—"

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
        header("Import", "Upload an HSBC export or credit-card PDF statement "
               "- any file name. The card is picked from what the file "
               "overlaps; change it if it guessed wrong."),
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
                style_cell_conditional=money_cells("amount_fmt") + [
                    {"if": {"column_id": "in"}, "width": "34px",
                     "textAlign": "center"},
                    {"if": {"column_id": "description"}, "width": "34%"}],
                style_data_conditional=[
                    {"if": {"filter_query": '{matched} = "No rule"'},
                     "backgroundColor": theme.TINT_NEAR},
                    {"if": {"filter_query": f'{{in}} = "{NO}"'},
                     "color": theme.NEUTRAL},
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
                     "statement's date.",
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
        id=f"{P}-upload", multiple=False, accept=".csv,.pdf",
        children=html.Div(["Drop a CSV export or PDF statement here, or ",
                           html.Span("choose a file", style=link)]),
        style={"width": "300px", "padding": "18px 12px",
               "border": f"1px dashed {theme.NEUTRAL}",
               "borderRadius": "6px", "textAlign": "center",
               "fontSize": "12.5px", "color": theme.SLATE,
               "cursor": "pointer", "background": theme.CANVAS})


def _d(iso) -> str:
    return f"{pd.Timestamp(iso):%d %b %Y}" if iso else ""


# --- Upload and card ---------------------------------------------------------

@callback(
    Output(f"{P}-file", "data"),
    Output(f"{P}-card", "value"),
    Output(f"{P}-result", "children"),
    Input(f"{P}-upload", "contents"),
    State(f"{P}-upload", "filename"),
    State(f"{P}-card", "value"),
    prevent_initial_call=True,
)
def _upload(contents, filename, card):
    if not contents:
        return no_update, no_update, no_update
    statement = None
    try:
        raw = base64.b64decode(contents.split(",", 1)[1])
        if (filename or "").lower().endswith(".pdf"):
            st = statements.parse_hsbc_credit_pdf(raw)
            df = st.rows
            statement = {"date": st.statement_date, "period": st.period,
                         "debits": st.debits, "credits": st.credits,
                         "rows_debits": st.rows_debits,
                         "rows_credits": st.rows_credits,
                         "adds_up": st.adds_up, "problems": st.problems}
        else:
            df = sp.parse_bank_csv(raw)
    except Exception as exc:                       # noqa: BLE001
        return None, no_update, message(f"Could not read {filename}: {exc}",
                                        ok=False)
    if df.empty:
        return None, no_update, message(
            f"{filename}: no rows with a DD/MM/YYYY date and an amount.",
            ok=False)
    stored = repo.existing_between(df["date"].min(), df["date"].max())
    guess, n = sp.guess_card(df, stored)
    active = repo.card_names()
    if guess and guess in active:
        how = (f"Picked {guess}: {n} of the file's rows are already stored "
               "there.")
        card = guess
    else:
        how = (f"No overlap with stored rows - using {card}. Check it is "
               "the right card." if card else "Pick the card.")
    data = {"filename": filename, "rows": df.to_dict("records"), "how": how,
            "guess": guess, "statement": statement}
    return data, card, ""


@callback(
    Output(f"{P}-card", "options"),
    Output(f"{P}-card", "value", allow_duplicate=True),
    Output(f"{P}-cardmsg", "children"),
    Output(f"{P}-newcard", "value"),
    Input(f"{P}-addcard", "n_clicks"),
    State(f"{P}-newcard", "value"),
    prevent_initial_call=True,
)
def _add_card(_n, name):
    problem = repo.add_card(name)
    if problem:
        return no_update, no_update, message(problem, ok=False), no_update
    name = name.strip()
    return (repo.card_names(), name,
            message(f"Added {name} - it is selected."), "")


# --- Preview -----------------------------------------------------------------

@callback(
    Output(f"{P}-rows", "data"),
    Output(f"{P}-cardnote", "children"),
    Input(f"{P}-file", "data"),
    Input(f"{P}-card", "value"),
)
def _build(file, card):
    last = repo.card_summary()
    last = dict(zip(last["name"], last["last_date"])) if not last.empty else {}
    card_line = (f"{card}: last posted {_d(last.get(card))}."
                 if card and last.get(card) else
                 f"{card}: nothing stored yet." if card else "")
    if not file:
        return None, card_line
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
            "amount": r["amount"], "state": r["state"],
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
    """What the statement says, and whether its lines add up to it."""
    if not st:
        return None
    lo, hi = st["period"] or (None, None)
    head = (f"Statement {_d(st['date'])}, posted {_d(lo)} \u2013 {_d(hi)}: "
            f"debits {gbp(st['debits'])}, credits {gbp(st['credits'])}. "
            if st["date"] else "Statement: ")
    if st["adds_up"]:
        return html.Div(message(head + "The lines add up to both totals."),
                        style={"marginBottom": "6px"})
    return html.Div(message(head + "Check before importing: "
                            + "; ".join(st["problems"]) + ".", ok=False),
                    style={"marginBottom": "6px"})


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-summary", "children"),
    Output(f"{P}-preview", "style"),
    Output(f"{P}-gonote", "children"),
    Input(f"{P}-rows", "data"),
    State(f"{P}-file", "data"),
)
def _table(rows, file):
    if not rows:
        return [], "", {"display": "none"}, ""
    shown = sorted((r for r in rows if r["state"] != "exists"),
                   key=lambda r: r["date"], reverse=True)
    shown.sort(key=lambda r: r["order"])          # stable: newest first within
    data = [{"id": r["id"], "in": YES if r["include"] else NO,
             "date": r["date"], "description": r["description"],
             "amount_fmt": gbp(r["amount"]),
             "category": r["category"] or "",
             "subcategory": r["subcategory"] or "",
             "matched": _matched(r),
             "note": (f"Possible duplicate of {_d(r['near_date'])}"
                      if r["state"] == "near" else "")} for r in shown]

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
    head = _statement_line((file or {}).get("statement"))
    return (data, [head, html.Div(summary)] if head else summary,
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
    # Rows already stored go along too, in file order: the counted
    # de-duplication in repo.import_rows needs them to know that a second
    # identical row in the file is new.
    send = []
    for r in sorted(rows, key=lambda r: r["id"]):
        if r["state"] == "exists" or r["include"]:
            send.append({"date": r["date"], "description": r["description"],
                         "amount": r["amount"], "category": r["category"],
                         "subcategory": r["subcategory"],
                         "status": "confirmed" if r["category"]
                         else "pending"})
    st = file.get("statement")
    if not any(r["state"] != "exists" and r["include"] for r in rows):
        # Nothing new: no import is recorded, but a statement is still
        # checked against what is stored.
        parts = [message(f"Nothing new to import from {file['filename']} - "
                         "every line is already stored.")]
        if st and st.get("period") and st.get("debits") is not None:
            parts.append(_reconcile_line(card, *st["period"], st["debits"],
                                         st["credits"]))
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
    parts = [message(text.strip())]
    if st and st.get("period") and st.get("debits") is not None:
        parts.append(_reconcile_line(card, *st["period"], st["debits"],
                                     st["credits"]))
    return (html.Div([html.Div(p) for p in parts], style=theme.CARD), None,
            _upload_box(), (refresh or 0) + 1)


# --- Reconciling -------------------------------------------------------------

def _reconcile_line(card, lo, hi, debits, credits):
    r = statements.reconcile(repo.card_rows_between(card, lo, hi),
                             float(debits or 0), float(credits or 0))
    where = f"{card}, posted {_d(lo)} \u2013 {_d(hi)}"
    if r["ok"]:
        return message(f"{where}: {r['rows']} rows, debits "
                       f"{gbp(r['debits'])} and credits {gbp(r['credits'])} - "
                       "matches the statement.")
    bits = []
    if abs(r["debits_diff"]) >= 0.005:
        bits.append(f"debits {gbp(r['debits'])} against {gbp(debits)} "
                    f"({'+' if r['debits_diff'] > 0 else ''}"
                    f"{r['debits_diff']:,.2f})")
    if abs(r["credits_diff"]) >= 0.005:
        bits.append(f"credits {gbp(r['credits'])} against {gbp(credits)} "
                    f"({'+' if r['credits_diff'] > 0 else ''}"
                    f"{r['credits_diff']:,.2f})")
    hint = (" More stored than the statement usually means a duplicate; "
            "less means missing rows." )
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
        last = r["last_date"]
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
        body.append(html.Tr([
            html.Td(card, style={**td, "fontWeight": 600}),
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
