"""
Spending -> Transactions.

Every stored bank line, filtered. Tick rows to change their category (with
Remember for a merchant that recurs), or send them back to the Manual Queue.
Export downloads exactly what the filters show.
"""

from __future__ import annotations

from datetime import date

from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)

from core import theme, spending as sp
from core.repo import spending as repo
from ui.spend import (TABLE, money_cells, btn, field, bar, header, message,
                      gbp, assign_bar, register_subcategories, select_rows, INPUT,
                      NOTE)

P = "sp-tx"

COLUMNS = [
    {"name": "Date", "id": "date"},
    {"name": "Card", "id": "card"},
    {"name": "Description", "id": "description"},
    {"name": "Amount", "id": "amount_fmt"},
    {"name": "Category", "id": "category"},
    {"name": "Sub-category", "id": "subcategory"},
    {"name": "Status", "id": "status"},
]


def render():
    years = repo.years()
    start = f"{years[0]}-01-01" if years else f"{date.today().year}-01-01"
    drop = {"fontSize": "12.5px"}
    cats = list(repo.categories())
    return html.Div([
        header("Transactions", "Every stored bank line. Tick rows to change "
               "their category or send them back to the Manual Queue."),
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
                    width="170px"),
                field("Category", dcc.Dropdown(
                    id=f"{P}-fcat", value=None, placeholder="All",
                    options=cats, style=drop), width="170px"),
                field("Sub-category", dcc.Dropdown(
                    id=f"{P}-fsub", value=None, placeholder="All",
                    options=[], style=drop), width="170px"),
                field("Status", dcc.Dropdown(
                    id=f"{P}-status", value="confirmed", clearable=False,
                    options=[{"label": "Confirmed", "value": "confirmed"},
                             {"label": "Pending", "value": "pending"},
                             {"label": "All", "value": "all"}],
                    style=drop), width="120px"),
                field("Type", dcc.Dropdown(
                    id=f"{P}-type", value="all", clearable=False,
                    options=[{"label": "All", "value": "all"},
                             {"label": "Spending", "value": "debit"},
                             {"label": "Credits", "value": "credit"}],
                    style=drop), width="120px"),
                field("Search", dcc.Input(id=f"{P}-search", type="text",
                                          debounce=True,
                                          placeholder="description…",
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
            assign_bar(P, "Assign to selected", extra=[
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
                style_cell_conditional=money_cells("amount_fmt") + [
                    {"if": {"column_id": "description"}, "width": "36%"}],
                style_data_conditional=[
                    {"if": {"filter_query": "{status} = pending"},
                     "backgroundColor": theme.TINT_NEAR}],
                **TABLE),
        ], style=theme.CARD),
        dcc.Store(id=f"{P}-refresh", data=0),
    ])


register_subcategories(P)


@callback(Output(f"{P}-fsub", "options"), Output(f"{P}-fsub", "value"),
          Input(f"{P}-fcat", "value"))
def _filter_subs(cat):
    cats = repo.categories()
    subs = (cats.get(cat, []) if cat
            else sorted({s for v in cats.values() for s in v}))
    return subs, None


def _filtered(date_from, date_to, card, cat, sub, status, kind, search):
    df = repo.transactions(date_from or None, date_to or None,
                           None if status == "all" else status)
    if card:
        df = df[df["card"] == card]
    if cat:
        df = df[df["category"] == cat]
    if sub:
        df = df[df["subcategory"] == sub]
    if kind == "debit":
        df = df[df["amount"] < 0]
    elif kind == "credit":
        df = df[df["amount"] > 0]
    if search:
        df = df[df["description"].str.contains(search, case=False,
                                               regex=False)]
    return df


FILTERS = [Input(f"{P}-from", "date"), Input(f"{P}-to", "date"),
           Input(f"{P}-card", "value"), Input(f"{P}-fcat", "value"),
           Input(f"{P}-fsub", "value"), Input(f"{P}-status", "value"),
           Input(f"{P}-type", "value"), Input(f"{P}-search", "value")]


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-info", "children"),
    *FILTERS,
    Input(f"{P}-refresh", "data"),
)
def _load(date_from, date_to, card, cat, sub, status, kind, search, _r):
    df = _filtered(date_from, date_to, card, cat, sub, status, kind, search)
    if df.empty:
        return [], "No transactions match these filters."
    data = [{"id": int(r["id"]), "date": r["date"], "card": r["card"],
             "description": r["description"],
             "amount_fmt": gbp(r["amount"]),
             "category": r["category"] or "", "subcategory":
             r["subcategory"] or "", "status": r["status"]}
            for r in df.to_dict("records")]
    spend = df.loc[df["amount"] < 0, "amount"].sum()
    credits = df.loc[df["amount"] > 0, "amount"].sum()
    info = (f"{len(df):,} transactions · spending {gbp(spend)} · "
            f"credits {gbp(credits)} · net {gbp(spend + credits)}")
    return data, info


@callback(
    Output(f"{P}-download", "data"),
    Input(f"{P}-export", "n_clicks"),
    *[State(i.component_id, i.component_property) for i in FILTERS],
    prevent_initial_call=True,
)
def _export(_n, *filters):
    df = _filtered(*filters).drop(columns=["import_id"])
    return dcc.send_data_frame(df.to_csv, "spending_transactions.csv",
                               index=False)


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
        if r and sp.remembers(r["description"]):
            return sp.suggest_keyword(r["description"]), []
    return "", []


@callback(
    Output(f"{P}-msg", "children"),
    Output(f"{P}-refresh", "data"),
    Input(f"{P}-apply", "n_clicks"),
    Input(f"{P}-queue", "n_clicks"),
    State(f"{P}-table", "selected_row_ids"),
    State(f"{P}-table", "data"),
    State(f"{P}-cat", "value"),
    State(f"{P}-sub", "value"),
    State(f"{P}-remember", "value"),
    State(f"{P}-kw", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _apply(_a, _q, selected, data, cat, sub, remember, kw, refresh):
    if not selected:
        return message("Tick at least one row first.", ok=False), no_update
    if ctx.triggered_id == f"{P}-queue":
        n = repo.set_status(selected, "pending")
        return (message(f"Sent {n} row{'s' * (n != 1)} to the Manual Queue."),
                (refresh or 0) + 1)
    if not (cat and sub):
        return message("Pick a category and sub-category.", ok=False), no_update
    n = repo.assign(selected, cat, sub)
    text = f"Assigned {n} row{'s' * (n != 1)} to {cat} / {sub}."
    if "y" in (remember or []):
        by_id = {r["id"]: r for r in data or []}
        descs = [by_id[i]["description"] for i in selected if i in by_id]
        text += " " + _remember(descs, kw, cat, sub)
    return message(text), (refresh or 0) + 1


def _remember(descs, kw, cat, sub) -> str:
    problem = sp.keyword_problem(descs, kw)
    if problem:
        return "Not remembered: " + problem
    return (f"Rule '{repo.save_rule(kw.strip(), cat, sub)}' saved for future "
            "imports.")
