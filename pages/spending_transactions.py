"""
Spending -> Transactions.

Every stored bank line, filtered. Tick rows to change their category (with
Remember for a merchant that recurs - for Amazon, PayPal and cash lines, the
keyword with the amount), set a note, split a line into parts, or send rows
back to the Manual Queue. Export downloads exactly what the filters show.

Amounts are in pounds and sort as numbers. A line from a card in another
currency also shows what the statement said, under Original. Statement shows
which statement a line is on ("provisional": a CSV row whose statement has
not been uploaded yet). A split line shows its parts; the category filter and
the totals use the parts.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)

from core import theme
from core.repo import spending as repo
from core import spending as sp
from ui.spend import (TABLE, money_cells, money_col, btn, field, bar, header,
                      message, gbp, assign_bar, register_subcategories,
                      select_rows, split_panel, register_split, remember_rule,
                      INPUT, NOTE)

P = "sp-tx"

COLUMNS = [
    {"name": "Date", "id": "date"},
    {"name": "Card", "id": "card"},
    {"name": "Description", "id": "description"},
    money_col("Amount", "amount"),
    {"name": "Original", "id": "orig_fmt"},
    {"name": "Category", "id": "category"},
    {"name": "Sub-category", "id": "subcategory"},
    {"name": "Note", "id": "note"},
    {"name": "Statement", "id": "statement"},
    {"name": "Status", "id": "status"},
]
ON_STATEMENT = [{"label": "All", "value": "all"},
                {"label": "On a statement", "value": "yes"},
                {"label": "Provisional", "value": "no"}]


def render():
    years = repo.years()
    start = f"{years[0]}-01-01" if years else f"{date.today().year}-01-01"
    drop = {"fontSize": "12.5px"}
    cats = list(repo.categories())
    return html.Div([
        header("Transactions", "Every stored bank line. Tick rows to change "
               "their category, add a note, split one, or send them back to "
               "the Manual Queue."),
        html.Div([
            bar(
                field("From", dcc.DatePickerSingle(
                    id=f"{P}-from", date=start, display_format="DD MMM YYYY",
                    clearable=True, placeholder="Any")),
                field("To", dcc.DatePickerSingle(
                    id=f"{P}-to", date=date.today().isoformat(),
                    display_format="DD MMM YYYY", clearable=True,
                    placeholder="Any")),
                field("Card", dcc.Dropdown(
                    id=f"{P}-card", value=None, placeholder="All",
                    options=repo.card_names(active_only=False), style=drop),
                    width="160px"),
                field("Category", dcc.Dropdown(
                    id=f"{P}-fcat", value=None, placeholder="All",
                    options=cats, style=drop), width="160px"),
                field("Sub-category", dcc.Dropdown(
                    id=f"{P}-fsub", value=None, placeholder="All",
                    options=[], style=drop), width="160px"),
                field("Status", dcc.Dropdown(
                    id=f"{P}-status", value="confirmed", clearable=False,
                    options=[{"label": "Confirmed", "value": "confirmed"},
                             {"label": "Pending", "value": "pending"},
                             {"label": "All", "value": "all"}],
                    style=drop), width="110px"),
                field("Type", dcc.Dropdown(
                    id=f"{P}-type", value="all", clearable=False,
                    options=[{"label": "All", "value": "all"},
                             {"label": "Spending", "value": "debit"},
                             {"label": "Credits", "value": "credit"}],
                    style=drop), width="110px"),
                field("Statement", dcc.Dropdown(
                    id=f"{P}-onst", value="all", clearable=False,
                    options=ON_STATEMENT, style=drop), width="140px"),
                field("Search", dcc.Input(id=f"{P}-search", type="text",
                                          debounce=True,
                                          placeholder="description or note…",
                                          style={**INPUT, "width": "180px"})),
            ),
            html.Div([
                html.Div(id=f"{P}-info", style=NOTE),
                html.Button("Export CSV", id=f"{P}-export", n_clicks=0,
                            style=btn()),
                dcc.Download(id=f"{P}-download"),
            ], style={"display": "flex", "alignItems": "center",
                      "justifyContent": "space-between", "marginTop": "10px"}),
        ], style=theme.CARD),
        html.Div([
            assign_bar(P, "Assign to selected", note=True, extra=[
                html.Button("Set note only", id=f"{P}-noteonly", n_clicks=0,
                            style=btn()),
                html.Button("Split…", id=f"{P}-split-open", n_clicks=0,
                            style=btn()),
                html.Button("Send to Manual Queue", id=f"{P}-queue",
                            n_clicks=0, style=btn()),
                html.Button("Select all", id=f"{P}-all", n_clicks=0,
                            style=btn()),
                html.Button("Clear", id=f"{P}-none", n_clicks=0, style=btn()),
            ]),
            html.Div(id=f"{P}-msg", style={"margin": "10px 0 4px 0",
                                           "minHeight": "16px"}),
            dash_table.DataTable(
                id=f"{P}-table", columns=COLUMNS, data=[],
                row_selectable="multi", selected_row_ids=[],
                sort_action="native", page_action="native", page_size=100,
                style_cell_conditional=money_cells("amount", "orig_fmt") + [
                    {"if": {"column_id": "description"}, "width": "30%"}],
                style_data_conditional=[
                    {"if": {"column_id": "orig_fmt"}, "color": theme.SLATE},
                    {"if": {"column_id": "statement",
                            "filter_query": '{statement} = "provisional"'},
                     "color": theme.NEEDLE},
                    {"if": {"filter_query": '{category} = "Split"',
                            "column_id": "subcategory"},
                     "color": theme.SLATE},
                    {"if": {"filter_query": "{status} = pending"},
                     "backgroundColor": theme.TINT_NEAR}],
                **TABLE),
        ], style=theme.CARD),
        split_panel(P),
        dcc.Store(id=f"{P}-refresh", data=0),
    ])


register_subcategories(P)
register_split(P, f"{P}-refresh")


@callback(Output(f"{P}-fsub", "options"), Output(f"{P}-fsub", "value"),
          Input(f"{P}-fcat", "value"))
def _filter_subs(cat):
    cats = repo.categories()
    subs = (cats.get(cat, []) if cat
            else sorted({s for v in cats.values() for s in v}))
    return subs, None


def _filtered(date_from, date_to, card, cat, sub, status, kind, onst,
              search):
    """(lines to show, the amounts the totals count). With a category
    filter a split line shows when one of its parts matches, and only the
    matching parts count."""
    df = repo.transactions(date_from or None, date_to or None,
                           None if status == "all" else status)
    if card:
        df = df[df["card"] == card]
    if kind == "debit":
        df = df[df["amount"] < 0]
    elif kind == "credit":
        df = df[df["amount"] > 0]
    if onst == "yes":
        df = df[df["statement_id"].notna()]
    elif onst == "no":
        df = df[df["statement_id"].isna()]
    if search:
        hit = df["description"].str.contains(search, case=False, regex=False)
        hit |= df["note"].fillna("").str.contains(search, case=False,
                                                  regex=False)
        df = df[hit]
    parts = repo.expanded(df)
    if cat:
        parts = parts[parts["category"] == cat]
    if sub:
        parts = parts[parts["subcategory"] == sub]
    if cat or sub:
        df = df[df["id"].isin(parts["id"])]
    return df, parts


FILTERS = [Input(f"{P}-from", "date"), Input(f"{P}-to", "date"),
           Input(f"{P}-card", "value"), Input(f"{P}-fcat", "value"),
           Input(f"{P}-fsub", "value"), Input(f"{P}-status", "value"),
           Input(f"{P}-type", "value"), Input(f"{P}-onst", "value"),
           Input(f"{P}-search", "value")]


def _statement_labels() -> dict:
    sts = repo.statements()
    return {int(r["id"]): f"{pd.Timestamp(r['period_to']):%b %Y}"
            for r in sts.to_dict("records")}


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-info", "children"),
    *FILTERS,
    Input(f"{P}-refresh", "data"),
)
def _load(date_from, date_to, card, cat, sub, status, kind, onst, search,
          _r):
    df, parts = _filtered(date_from, date_to, card, cat, sub, status, kind,
                          onst, search)
    if df.empty:
        return [], "No transactions match these filters."
    labels = _statement_labels()
    splits = parts[parts["split_of"].notna()] if "split_of" in parts else \
        parts.iloc[0:0]
    if not (cat or sub):
        splits = repo.splits_for(df["id"]).rename(
            columns={"transaction_id": "split_of"})
    split_text = {}
    for tid, g in splits.groupby("split_of"):
        split_text[int(tid)] = " · ".join(f"{p['subcategory']} "
                                          f"{gbp(p['amount'])}"
                                          for p in g.to_dict("records"))
    data = []
    for r in df.to_dict("records"):
        tid = int(r["id"])
        sid = r["statement_id"]
        orig = ("" if pd.isna(r.get("amount_orig")) or not r.get("currency")
                else f"{gbp(r['amount_orig'])} {r['currency']}")
        data.append({"id": tid, "date": r["date"], "card": r["card"],
                     "description": r["description"], "amount": r["amount"],
                     "orig_fmt": orig,
                     "category": ("Split" if tid in split_text
                                  else r["category"] or ""),
                     "subcategory": split_text.get(tid,
                                                   r["subcategory"] or ""),
                     "note": r["note"] if isinstance(r["note"], str) else "",
                     "statement": ("provisional" if pd.isna(sid)
                                   else labels.get(int(sid), "")),
                     "status": r["status"]})
    amounts = parts["amount"]
    spend, credits = amounts[amounts < 0].sum(), amounts[amounts > 0].sum()
    info = (f"{len(df):,} transactions · spending {gbp(spend)} · "
            f"credits {gbp(credits)} · net {gbp(spend + credits)}"
            + (" (matching split parts only)" if (cat or sub) and
               not splits.empty else ""))
    return data, info


@callback(
    Output(f"{P}-download", "data"),
    Input(f"{P}-export", "n_clicks"),
    *[State(i.component_id, i.component_property) for i in FILTERS],
    prevent_initial_call=True,
)
def _export(_n, *filters):
    df, _ = _filtered(*filters)
    return dcc.send_data_frame(df.drop(columns=["import_id"]).to_csv,
                               "spending_transactions.csv", index=False)


@callback(
    Output(f"{P}-table", "selected_rows"),
    Output(f"{P}-table", "selected_row_ids"),
    Input(f"{P}-all", "n_clicks"),
    Input(f"{P}-none", "n_clicks"),
    Input(f"{P}-table", "data"),
    State(f"{P}-table", "derived_virtual_data"),
    prevent_initial_call=True,
)
def _select(_all, _none, data, visible):
    """Select all = every row the table's sort and paging can show. Both
    props are set: the tick boxes follow selected_rows, not the ids."""
    if ctx.triggered_id == f"{P}-all":
        return select_rows(data, [r["id"] for r in visible or []])
    return [], []


@callback(
    Output(f"{P}-kw", "value"),
    Output(f"{P}-remember", "value"),
    Input(f"{P}-table", "selected_row_ids"),
    State(f"{P}-table", "data"),
)
def _suggest(selected, data):
    by_id = {r["id"]: r for r in data or []}
    for i in selected or []:
        r = by_id.get(i)
        if r:
            return sp.remember_keyword(r["description"]), []
    return "", []


@callback(
    Output(f"{P}-msg", "children"),
    Output(f"{P}-refresh", "data", allow_duplicate=True),
    Input(f"{P}-apply", "n_clicks"),
    Input(f"{P}-queue", "n_clicks"),
    Input(f"{P}-noteonly", "n_clicks"),
    State(f"{P}-table", "selected_row_ids"),
    State(f"{P}-table", "data"),
    State(f"{P}-cat", "value"),
    State(f"{P}-sub", "value"),
    State(f"{P}-note", "value"),
    State(f"{P}-remember", "value"),
    State(f"{P}-kw", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _apply(_a, _q, _n, selected, data, cat, sub, note, remember, kw,
           refresh):
    if not selected:
        return message("Tick at least one row first.", ok=False), no_update
    bump = (refresh or 0) + 1
    trig = ctx.triggered_id
    if trig == f"{P}-queue":
        n = repo.set_status(selected, "pending")
        return (message(f"Sent {n} row{'s' * (n != 1)} to the Manual Queue."),
                bump)
    if trig == f"{P}-noteonly":
        n = repo.set_note(selected, note)
        return message(f"Note set on {n} row{'s' * (n != 1)}."), bump
    if not (cat and sub):
        return message("Pick a category and sub-category.", ok=False), no_update
    n = repo.assign(selected, cat, sub, note=note if note else None)
    text = f"Assigned {n} row{'s' * (n != 1)} to {cat} / {sub}."
    if "y" in (remember or []):
        by_id = {r["id"]: r for r in data or []}
        _, said = remember_rule([by_id[i] for i in selected if i in by_id],
                                kw, cat, sub, note)
        text += " " + said
    return message(text), bump


@callback(
    Output(f"{P}-split-txn", "data"),
    Output(f"{P}-msg", "children", allow_duplicate=True),
    Input(f"{P}-split-open", "n_clicks"),
    State(f"{P}-table", "selected_row_ids"),
    prevent_initial_call=True,
)
def _open_split(n, selected):
    if not selected or len(selected) != 1:
        return no_update, message("Tick one row to split.", ok=False)
    return {"id": int(selected[0]), "nonce": n}, ""
