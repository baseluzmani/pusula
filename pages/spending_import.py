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
"""

from __future__ import annotations

import base64

import pandas as pd
from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)

from core import theme, spending as sp
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


def render():
    names = repo.card_names()
    last = repo.last_import_card()
    card = last if last in names else (names[0] if names else None)
    return html.Div([
        header("Import", "Upload an HSBC export - any file name. The card is "
               "picked from what the file overlaps; change it if it guessed "
               "wrong."),
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

        dcc.Store(id=f"{P}-file"),
        dcc.Store(id=f"{P}-rows"),
    ])


register_subcategories(P)


def _upload_box():
    """A fresh Upload. Re-mounted after each import: a browser does not fire
    'change' when the same file is picked twice in the same input."""
    link = {"color": theme.INK, "fontWeight": 600,
            "textDecoration": "underline"}
    return dcc.Upload(
        id=f"{P}-upload", multiple=False, accept=".csv",
        children=html.Div(["Drop a CSV here or ",
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
    try:
        raw = base64.b64decode(contents.split(",", 1)[1])
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
            "guess": guess}
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


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-summary", "children"),
    Output(f"{P}-preview", "style"),
    Output(f"{P}-gonote", "children"),
    Input(f"{P}-rows", "data"),
)
def _table(rows):
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
    return data, summary, {**theme.CARD, "display": "block"}, go


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
    Input(f"{P}-go", "n_clicks"),
    State(f"{P}-rows", "data"),
    State(f"{P}-file", "data"),
    State(f"{P}-card", "value"),
    prevent_initial_call=True,
)
def _import(_n, rows, file, card):
    if not rows or not file:
        return no_update, no_update, no_update
    if not card:
        return message("Pick the card first.", ok=False), no_update, no_update
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
    return (html.Div(message(text.strip()), style=theme.CARD), None,
            _upload_box())
