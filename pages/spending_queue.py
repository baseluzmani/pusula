"""
Spending -> Manual Queue.

Pending rows: Amazon, PayPal and cash lines (they vary per purchase, so no
rule can place them), plus any imported line no rule matched and you left
uncategorised. Newest first, so recent items get done and the old backlog can
wait.

Tick rows - or filter, then Select all - pick a category and Assign. For a
"No rule" row, Remember saves a keyword rule; any other pending No-rule rows
the new rule matches are assigned at the same time.
"""

from __future__ import annotations

from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)

from core import theme, spending as sp
from core.repo import spending as repo
from ui.spend import (TABLE, money_cells, btn, field, bar, header, message,
                      gbp, assign_bar, register_subcategories, select_rows, INPUT,
                      NOTE)

P = "sp-mq"

COLUMNS = [
    {"name": "Date", "id": "date"},
    {"name": "Card", "id": "card"},
    {"name": "Description", "id": "description"},
    {"name": "Amount", "id": "amount_fmt"},
    {"name": "Group", "id": "group"},
]


def render():
    return html.Div([
        header("Manual Queue", "Lines no rule can place. Tick rows (or filter "
               "and Select all), pick a category, Assign."),
        html.Div([
            bar(
                field("Group", dcc.RadioItems(
                    id=f"{P}-group", value="All", inline=True,
                    options=["All", *sp.QUEUE_GROUPS],
                    inputStyle={"marginRight": "5px"},
                    labelStyle={"marginRight": "14px", "fontSize": "12.5px"})),
                field("From", dcc.DatePickerSingle(
                    id=f"{P}-from", display_format="DD MMM YYYY",
                    clearable=True, placeholder="Any")),
                field("To", dcc.DatePickerSingle(
                    id=f"{P}-to", display_format="DD MMM YYYY",
                    clearable=True, placeholder="Any")),
                field("Search", dcc.Input(id=f"{P}-search", type="text",
                                          debounce=True,
                                          placeholder="description…",
                                          style={**INPUT, "width": "200px"})),
            ),
            html.Div(id=f"{P}-info", style={**NOTE, "marginTop": "10px"}),
        ], style=theme.CARD),
        html.Div([
            assign_bar(P, "Assign to selected", extra=[
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
                    {"if": {"column_id": "description"}, "width": "45%"}],
                **TABLE),
        ], style=theme.CARD),
        dcc.Store(id=f"{P}-refresh", data=0),
    ])


register_subcategories(P)


def _rows(group, date_from, date_to, search):
    df = repo.pending()
    if df.empty:
        return df.assign(group=[])
    df = df.assign(group=df["description"].map(sp.queue_group))
    if group and group != "All":
        df = df[df["group"] == group]
    if date_from:
        df = df[df["date"] >= date_from]
    if date_to:
        df = df[df["date"] <= date_to]
    if search:
        df = df[df["description"].str.contains(search, case=False,
                                               regex=False)]
    return df


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-info", "children"),
    Output(f"{P}-group", "options"),
    Input(f"{P}-group", "value"),
    Input(f"{P}-from", "date"),
    Input(f"{P}-to", "date"),
    Input(f"{P}-search", "value"),
    Input(f"{P}-refresh", "data"),
)
def _load(group, date_from, date_to, search, _refresh):
    allp = repo.pending()
    groups = (allp["description"].map(sp.queue_group).value_counts()
              if not allp.empty else {})
    options = [{"label": f"All ({len(allp)})", "value": "All"}] + [
        {"label": f"{g} ({int(groups.get(g, 0))})", "value": g}
        for g in sp.QUEUE_GROUPS]
    df = _rows(group, date_from, date_to, search)
    if df.empty:
        info = ("Nothing pending - all clear." if allp.empty
                else "No pending rows match these filters.")
        return [], info, options
    data = [{"id": int(r["id"]), "date": r["date"], "card": r["card"],
             "description": r["description"], "amount": r["amount"],
             "amount_fmt": gbp(r["amount"]), "group": r["group"]}
            for r in df.to_dict("records")]
    info = (f"{len(df):,} rows shown · {gbp(df['amount'].sum())} · "
            f"oldest {df['date'].min()}")
    return data, info, options


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
    """Pre-fill the keyword from the first selected row that can be
    remembered; nothing for Amazon / PayPal / cash lines."""
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
    State(f"{P}-table", "selected_row_ids"),
    State(f"{P}-table", "data"),
    State(f"{P}-cat", "value"),
    State(f"{P}-sub", "value"),
    State(f"{P}-remember", "value"),
    State(f"{P}-kw", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _apply(_n, selected, data, cat, sub, remember, kw, refresh):
    if not selected:
        return message("Tick at least one row first.", ok=False), no_update
    if not (cat and sub):
        return message("Pick a category and sub-category.", ok=False), no_update
    n = repo.assign(selected, cat, sub)
    parts = [f"Assigned {n} row{'s' * (n != 1)} to {cat} / {sub}."]
    if "y" in (remember or []):
        parts.append(_remember(selected, data, kw, cat, sub))
    return message(" ".join(p for p in parts if p)), (refresh or 0) + 1


def _remember(selected, data, kw, cat, sub) -> str:
    by_id = {r["id"]: r for r in data or []}
    descs = [by_id[i]["description"] for i in selected if i in by_id]
    problem = sp.keyword_problem(descs, kw)
    if problem:
        return "Not remembered: " + problem
    kw = repo.save_rule(kw.strip(), cat, sub)
    # Other pending rows the new rule now places. classify() never matches a
    # manual-queue line, so Amazon / cash rows are left alone.
    rules = repo.rule_tuples()
    more = [int(r["id"]) for r in repo.pending().to_dict("records")
            if sp.classify(r["description"], rules).rule == kw]
    if more:
        repo.assign(more, cat, sub)
        return (f"Rule '{kw}' saved; it also placed {len(more)} more "
                f"pending row{'s' * (len(more) != 1)}.")
    return f"Rule '{kw}' saved."
