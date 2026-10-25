"""
Spending -> Rules.

Four small tools, as on the 8052 Rules page plus cards:

    Rules        keyword -> category / sub-category. On import the first rule
                 whose keyword appears in the description wins, lowest
                 priority number first. Click a rule to edit it; a blank
                 priority puts a new rule at the end of the list.
    Categories   add or remove categories and sub-categories. Removing one
                 does not change stored transactions - re-map them first.
    Re-map       move every transaction in one category / sub-category to
                 another.
    Cards        rename a card (its history moves with it), retire one you no
                 longer use (hidden from the Import dropdown), or add one.
"""

from __future__ import annotations

from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  ALL, no_update)

from core import theme
from core.repo import spending as repo
from ui.layout import card
from ui.spend import (TABLE, btn, field, bar, header, message, INPUT, NOTE,
                      category_options, register_subcategories)

P = "sp-ru"
DROP = {"fontSize": "12.5px"}


def render():
    cats = category_options()
    return html.Div([
        header("Rules", "How imports are categorised, the category lists, and "
               "your cards."),
        card("Keyword rules",
             bar(field("Show category", dcc.Dropdown(
                     id=f"{P}-fcat", options=cats, placeholder="All",
                     style=DROP), width="200px"),
                 field("Search", dcc.Input(
                     id=f"{P}-search", type="text", debounce=True,
                     placeholder="keyword…",
                     style={**INPUT, "width": "200px"})),
                 html.Div(id=f"{P}-count", style={**NOTE,
                          "paddingBottom": "7px"})),
             html.Div(dash_table.DataTable(
                 id=f"{P}-table", data=[], row_selectable="single",
                 selected_row_ids=[], sort_action="native",
                 page_action="native", page_size=25,
                 columns=[{"name": "Priority", "id": "priority"},
                          {"name": "Keyword", "id": "keyword"},
                          {"name": "Category", "id": "category"},
                          {"name": "Sub-category", "id": "subcategory"}],
                 style_cell_conditional=[
                     {"if": {"column_id": "keyword"},
                      "fontFamily": theme.FONT_NUM, "whiteSpace": "pre"},
                     {"if": {"column_id": "priority"}, "width": "70px",
                      "textAlign": "right", "fontFamily": theme.FONT_NUM}],
                 **TABLE), style={"margin": "12px 0"}),
             bar(field("Keyword", dcc.Input(
                     id=f"{P}-kw", type="text", debounce=False,
                     placeholder="e.g. NETFLIX",
                     style={**INPUT, "width": "220px",
                            "fontFamily": theme.FONT_NUM})),
                 field("Category", dcc.Dropdown(id=f"{P}-cat", options=cats,
                                                clearable=False, style=DROP),
                       width="180px"),
                 field("Sub-category", dcc.Dropdown(id=f"{P}-sub", options=[],
                                                    clearable=False,
                                                    style=DROP),
                       width="180px"),
                 field("Priority", dcc.Input(
                     id=f"{P}-pri", type="number", debounce=False,
                     placeholder="end", style={**INPUT, "width": "80px"})),
                 html.Button("Save rule", id=f"{P}-save", n_clicks=0,
                             style=btn("primary")),
                 html.Button("Delete", id=f"{P}-delete", n_clicks=0,
                             style=btn()),
                 html.Button("New", id=f"{P}-new", n_clicks=0, style=btn())),
             html.Div(id=f"{P}-msg", style={"marginTop": "8px",
                                            "minHeight": "16px"})),

        card("Categories",
             bar(field("Category", dcc.Dropdown(
                     id=f"{P}-ccat", options=cats,
                     value=cats[0]["value"] if cats else None,
                     clearable=False, style=DROP), width="200px"),
                 html.Button("Delete category", id=f"{P}-cdel", n_clicks=0,
                             style=btn()),
                 field("New category", dcc.Input(
                     id=f"{P}-cnew", type="text", debounce=False,
                     style={**INPUT, "width": "180px"})),
                 html.Button("Add category", id=f"{P}-cadd", n_clicks=0,
                             style=btn())),
             html.Div(id=f"{P}-subs", style={"margin": "14px 0 10px 0"}),
             bar(field("New sub-category", dcc.Input(
                     id=f"{P}-snew", type="text", debounce=False,
                     style={**INPUT, "width": "180px"})),
                 html.Button("Add sub-category", id=f"{P}-sadd", n_clicks=0,
                             style=btn())),
             html.Div(id=f"{P}-cmsg", style={"marginTop": "8px",
                                             "minHeight": "16px"})),

        card("Re-map transactions",
             bar(field("From category", dcc.Dropdown(
                     id=f"{P}-mfrom", options=cats + [
                         {"label": f"{c} (not in list)", "value": c}
                         for c in repo.categories_in_use()
                         if c not in {o["value"] for o in cats}],
                     style=DROP),
                     width="180px"),
                 field("From sub-category", dcc.Dropdown(
                     id=f"{P}-mfromsub", options=[], placeholder="(any)",
                     style=DROP), width="180px"),
                 html.Div("→", style={"paddingBottom": "8px",
                                           "color": theme.SLATE}),
                 field("To category", dcc.Dropdown(
                     id=f"{P}-mto", options=cats, style=DROP),
                     width="180px"),
                 field("To sub-category", dcc.Dropdown(
                     id=f"{P}-mtosub", options=[], style=DROP),
                     width="180px"),
                 html.Button("Re-map", id=f"{P}-mgo", n_clicks=0,
                             style=btn("primary"))),
             html.Div(id=f"{P}-mmsg", style={**NOTE, "marginTop": "8px",
                                             "minHeight": "16px"})),

        card("Cards",
             html.Div(dash_table.DataTable(
                 id=f"{P}-cards", data=[], row_selectable="single",
                 selected_row_ids=[],
                 columns=[{"name": "Card", "id": "name"},
                          {"name": "In use", "id": "in_use"},
                          {"name": "Rows", "id": "rows"},
                          {"name": "First", "id": "first_date"},
                          {"name": "Last posted", "id": "last_date"}],
                 style_data_conditional=[
                     {"if": {"filter_query": '{in_use} = "Retired"'},
                      "color": theme.NEUTRAL}],
                 **TABLE), style={"marginBottom": "12px"}),
             bar(field("Rename selected to", dcc.Input(
                     id=f"{P}-rname", type="text", debounce=False,
                     style={**INPUT, "width": "180px"})),
                 html.Button("Rename", id=f"{P}-rgo", n_clicks=0,
                             style=btn()),
                 html.Button("Retire / restore", id=f"{P}-retire",
                             n_clicks=0, style=btn()),
                 field("New card", dcc.Input(
                     id=f"{P}-newcard", type="text", debounce=False,
                     style={**INPUT, "width": "160px"})),
                 html.Button("Add card", id=f"{P}-addcard", n_clicks=0,
                             style=btn())),
             html.Div(id=f"{P}-kmsg", style={"marginTop": "8px",
                                             "minHeight": "16px"})),

        dcc.Store(id=f"{P}-refresh", data=0),
    ])


register_subcategories(P)


# --- Rules ---------------------------------------------------------------------

@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-count", "children"),
    Input(f"{P}-fcat", "value"),
    Input(f"{P}-search", "value"),
    Input(f"{P}-refresh", "data"),
)
def _rules(cat, search, _r):
    df = repo.rules()
    total = len(df)
    if cat:
        df = df[df["category"] == cat]
    if search:
        df = df[df["keyword"].str.contains(search.upper(), regex=False)]
    return df.to_dict("records"), f"{len(df)} of {total} rules"


@callback(
    Output(f"{P}-kw", "value"),
    Output(f"{P}-cat", "value"),
    Output(f"{P}-sub", "options", allow_duplicate=True),
    Output(f"{P}-sub", "value", allow_duplicate=True),
    Output(f"{P}-pri", "value"),
    Input(f"{P}-table", "selected_row_ids"),
    State(f"{P}-table", "data"),
    prevent_initial_call=True,
)
def _load_rule(selected, data):
    """Clicking a rule loads it into the form; no selection empties it. The
    sub-category options are set here too - a dropdown drops a value that
    is not among its options."""
    if not selected:
        return "", None, [], None, None
    r = next((x for x in data or [] if x["id"] == selected[0]), None)
    if not r:
        return (no_update,) * 5
    subs = repo.categories().get(r["category"], [])
    if r["subcategory"] not in subs:
        subs = subs + [r["subcategory"]]
    return (r["keyword"], r["category"], subs, r["subcategory"],
            r["priority"])


@callback(
    Output(f"{P}-table", "selected_row_ids", allow_duplicate=True),
    Input(f"{P}-new", "n_clicks"),
    prevent_initial_call=True,
)
def _new_rule(_n):
    """New: drop the selection, so Save adds a rule instead of editing."""
    return []


@callback(
    Output(f"{P}-msg", "children"),
    Output(f"{P}-refresh", "data"),
    Output(f"{P}-table", "selected_row_ids"),
    Input(f"{P}-save", "n_clicks"),
    Input(f"{P}-delete", "n_clicks"),
    State(f"{P}-table", "selected_row_ids"),
    State(f"{P}-kw", "value"),
    State(f"{P}-cat", "value"),
    State(f"{P}-sub", "value"),
    State(f"{P}-pri", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _save_rule(_s, _d, selected, kw, cat, sub, pri, refresh):
    if ctx.triggered_id == f"{P}-delete":
        if not selected:
            return message("Click a rule first.", ok=False), no_update, \
                no_update
        repo.delete_rule(selected[0])
        return message("Rule deleted."), (refresh or 0) + 1, []
    if not (kw or "").strip():
        return message("Type a keyword.", ok=False), no_update, no_update
    if not (cat and sub):
        return message("Pick a category and sub-category.", ok=False), \
            no_update, no_update
    pri = int(pri) if pri not in (None, "") else None
    if selected:
        # Editing the clicked rule - keyword changes included.
        problem = repo.update_rule(selected[0], kw, cat, sub, pri)
        if problem:
            return message(problem, ok=False), no_update, no_update
        saved = kw.upper()
    else:
        saved = repo.save_rule(kw, cat, sub, pri)
    return (message(f"Saved: {saved.strip()} → {cat} / {sub}."),
            (refresh or 0) + 1, no_update)


# --- Categories ----------------------------------------------------------------

@callback(
    Output(f"{P}-subs", "children"),
    Input(f"{P}-ccat", "value"),
    Input(f"{P}-refresh", "data"),
)
def _subs(cat, _r):
    subs = repo.categories().get(cat, []) if cat else []
    if not subs:
        return html.Div("No sub-categories.", style=NOTE)
    chip = {"display": "inline-flex", "alignItems": "center", "gap": "6px",
            "padding": "3px 4px 3px 10px", "margin": "0 6px 6px 0",
            "border": f"1px solid {theme.LINE}", "borderRadius": "12px",
            "fontSize": "12px", "background": theme.CANVAS}
    x = {"border": "none", "background": "none", "cursor": "pointer",
         "color": theme.SLATE, "fontSize": "12px", "padding": "0 4px"}
    return html.Div([
        html.Span([s, html.Button("×", n_clicks=0, title=f"Remove {s}",
                                  id={"type": f"{P}-sdel", "sub": s},
                                  style=x)], style=chip)
        for s in subs])


@callback(
    Output(f"{P}-cmsg", "children"),
    Output(f"{P}-refresh", "data", allow_duplicate=True),
    Output(f"{P}-ccat", "options"),
    Output(f"{P}-ccat", "value"),
    Output(f"{P}-cnew", "value"),
    Output(f"{P}-snew", "value"),
    Input(f"{P}-cadd", "n_clicks"),
    Input(f"{P}-cdel", "n_clicks"),
    Input(f"{P}-sadd", "n_clicks"),
    Input({"type": f"{P}-sdel", "sub": ALL}, "n_clicks"),
    State(f"{P}-ccat", "value"),
    State(f"{P}-cnew", "value"),
    State(f"{P}-snew", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _categories(_ca, _cd, _sa, sdel, cat, cnew, snew, refresh):
    trig = ctx.triggered_id
    skip = (no_update,) * 5
    if isinstance(trig, dict):
        # A rebuilt chip list arrives with every n_clicks at 0: not a click.
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            return (no_update, *skip)
        repo.delete_subcategory(cat, trig["sub"])
        return (message(f"Removed {trig['sub']} from {cat}."),
                (refresh or 0) + 1, no_update, no_update, no_update,
                no_update)
    if trig == f"{P}-cadd":
        if not (cnew or "").strip():
            return (message("Type a category name.", ok=False), *skip)
        repo.add_category(cnew)
        name = cnew.strip()
        return (message(f"Added {name}."), (refresh or 0) + 1,
                category_options(), name, "", no_update)
    if trig == f"{P}-cdel":
        if not cat:
            return (no_update, *skip)
        repo.delete_category(cat)
        opts = category_options()
        return (message(f"Deleted {cat} from the lists. Its transactions are "
                        "unchanged - re-map them if needed."),
                (refresh or 0) + 1, opts,
                opts[0]["value"] if opts else None, no_update, no_update)
    if trig == f"{P}-sadd":
        if not (cat and (snew or "").strip()):
            return (message("Type a sub-category name.", ok=False), *skip)
        repo.add_subcategory(cat, snew)
        return (message(f"Added {snew.strip()} to {cat}."),
                (refresh or 0) + 1, no_update, no_update, no_update, "")
    return (no_update, *skip)


# --- Re-map --------------------------------------------------------------------

@callback(Output(f"{P}-mfromsub", "options"), Output(f"{P}-mfromsub", "value"),
          Input(f"{P}-mfrom", "value"))
def _from_subs(cat):
    if not cat:
        return [], None
    listed = repo.categories().get(cat, [])
    stray = [s for s in repo.subcategories_in_use(cat) if s not in listed]
    return listed + [{"label": f"{s} (not in list)", "value": s}
                     for s in stray], None


@callback(Output(f"{P}-mtosub", "options"), Output(f"{P}-mtosub", "value"),
          Input(f"{P}-mto", "value"))
def _to_subs(cat):
    subs = repo.categories().get(cat, []) if cat else []
    return subs, subs[0] if subs else None


@callback(
    Output(f"{P}-mmsg", "children"),
    Input(f"{P}-mfrom", "value"),
    Input(f"{P}-mfromsub", "value"),
    Input(f"{P}-mto", "value"),
    Input(f"{P}-mtosub", "value"),
    Input(f"{P}-mgo", "n_clicks"),
    prevent_initial_call=True,
)
def _remap(old_cat, old_sub, new_cat, new_sub, _go):
    if not old_cat:
        return ""
    if ctx.triggered_id == f"{P}-mgo":
        if not (new_cat and new_sub):
            return message("Pick where to move them.", ok=False)
        n = repo.remap(old_cat, old_sub, new_cat, new_sub)
        return message(f"Moved {n} transactions to {new_cat} / {new_sub}.")
    df = repo.transactions()
    hit = df[df["category"] == old_cat]
    if old_sub:
        hit = hit[hit["subcategory"] == old_sub]
    which = f"{old_cat} / {old_sub}" if old_sub else f"all of {old_cat}"
    return f"{len(hit):,} transactions in {which} would move."


# --- Cards ---------------------------------------------------------------------

@callback(
    Output(f"{P}-cards", "data"),
    Input(f"{P}-refresh", "data"),
)
def _cards(_r):
    df = repo.card_summary()
    return [{"id": r["name"], "name": r["name"],
             "in_use": "Yes" if r["active"] else "Retired",
             "rows": f"{int(r['rows']):,}", "first_date": r["first_date"] or "",
             "last_date": r["last_date"] or ""}
            for r in df.to_dict("records")]


@callback(
    Output(f"{P}-kmsg", "children"),
    Output(f"{P}-refresh", "data", allow_duplicate=True),
    Output(f"{P}-rname", "value"),
    Output(f"{P}-newcard", "value"),
    Input(f"{P}-rgo", "n_clicks"),
    Input(f"{P}-retire", "n_clicks"),
    Input(f"{P}-addcard", "n_clicks"),
    State(f"{P}-cards", "selected_row_ids"),
    State(f"{P}-cards", "data"),
    State(f"{P}-rname", "value"),
    State(f"{P}-newcard", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _card_actions(_r, _t, _a, selected, data, rname, newcard, refresh):
    trig = ctx.triggered_id
    bump = (refresh or 0) + 1
    if trig == f"{P}-addcard":
        problem = repo.add_card(newcard)
        if problem:
            return message(problem, ok=False), no_update, no_update, no_update
        return message(f"Added {newcard.strip()}."), bump, no_update, ""
    if not selected:
        return (message("Tick a card first.", ok=False), no_update,
                no_update, no_update)
    name = selected[0]
    if trig == f"{P}-rgo":
        problem = repo.rename_card(name, rname)
        if problem:
            return message(problem, ok=False), no_update, no_update, no_update
        return (message(f"Renamed {name} to {rname.strip()} - its "
                        "transactions moved with it."), bump, "", no_update)
    row = next((r for r in data or [] if r["id"] == name), None)
    retire = bool(row and row["in_use"] == "Yes")
    repo.set_card_active(name, not retire)
    return (message(f"{name} {'retired - hidden from Import' if retire else 'restored'}."),
            bump, no_update, no_update)
