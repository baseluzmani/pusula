"""
Spending -> Manual Queue.

Pending rows: Amazon, PayPal and cash lines (they vary per purchase, so no
keyword can place them), plus any imported line no rule matched and you left
uncategorised. Only lines from SPEND_QUEUE_FROM (Data -> Config, 1 Dec 2024)
are shown; older ones stay as they are unless you tick "Include older".

One by one
    The newest pending line, with one-click buttons:
        From your history   how you placed lines like it before - the same
                            description and amount, or for cash and Amazon
                            the same amount - including a split template you
                            used
        Templates           your split templates (cash: cleaning + tutor)
        Quick               the categories you use most for this kind of line
    or pick a category, add a note, Split… it, and Save & next. Remember
    saves a rule: for Amazon / PayPal / cash lines the keyword with this
    exact amount, so the same subscription is placed on import next time.
    Skip leaves a line for later.

All pending lines
    The table below: filter, tick rows (or Select all), Assign in bulk.
"""

from __future__ import annotations

import pandas as pd
from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  ALL, no_update)

from core import theme, spending as sp
from core.repo import settings
from core.repo import spending as repo
from ui.spend import (TABLE, money_cells, money_col, btn, field, bar, header,
                      message, gbp, assign_bar, register_subcategories,
                      select_rows, split_panel, register_split, remember_rule,
                      category_options, INPUT, NOTE, LABEL)

P = "sp-mq"
CUTOFF_KEY = "SPEND_QUEUE_FROM"
CUTOFF_DEFAULT = "2024-12-01"

COLUMNS = [
    {"name": "Date", "id": "date"},
    {"name": "Card", "id": "card"},
    {"name": "Description", "id": "description"},
    money_col("Amount", "amount"),
    {"name": "Group", "id": "group"},
    {"name": "Note", "id": "note"},
]

CHIP = {"padding": "6px 11px", "fontSize": "12px", "borderRadius": "14px",
        "border": f"1px solid {theme.LINE}", "background": theme.SURFACE,
        "color": theme.TEXT, "cursor": "pointer", "marginRight": "8px",
        "marginBottom": "8px"}
CHIP_STRONG = {**CHIP, "border": f"1px solid {theme.POSITIVE}",
               "color": theme.POSITIVE, "fontWeight": 600}


def render():
    settings.ensure(CUTOFF_KEY, CUTOFF_DEFAULT, "date",
                    "Spending: the Manual Queue shows pending lines from this "
                    "date (older ones are left as they are)")
    cutoff = settings.get(CUTOFF_KEY, CUTOFF_DEFAULT)
    drop = {"fontSize": "12.5px"}
    return html.Div([
        header("Manual Queue", "Lines no rule can place. Work through them "
               "one by one with the suggestions, or tick rows below and "
               "assign in bulk."),
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
                dcc.Checklist(id=f"{P}-old", value=[], options=[
                    {"label": f" Include lines before "
                              f"{pd.Timestamp(cutoff):%d %b %Y}",
                     "value": "y"}], inputStyle={"marginRight": "6px"},
                    labelStyle={"fontSize": "12px"}),
            ),
            html.Div(id=f"{P}-info", style={**NOTE, "marginTop": "10px"}),
        ], style=theme.CARD),

        html.Div([
            html.Div("One by one", style=theme.CARD_TITLE),
            html.Div(id=f"{P}-rv-line"),
            html.Div(id=f"{P}-rv-chips", style={"marginTop": "12px"}),
            html.Div([
                bar(
                    field("Category", dcc.Dropdown(
                        id=f"{P}-rv-cat", options=category_options(),
                        clearable=False, style=drop), width="190px"),
                    field("Sub-category", dcc.Dropdown(
                        id=f"{P}-rv-sub", options=[], clearable=False,
                        style=drop), width="190px"),
                    field("Note", dcc.Input(
                        id=f"{P}-rv-note", type="text", debounce=False,
                        placeholder="e.g. what you bought",
                        style={**INPUT, "width": "200px"})),
                    html.Div([
                        dcc.Checklist(id=f"{P}-rv-remember", value=[],
                                      options=[{"label": " Remember as a rule",
                                                "value": "y"}],
                                      inputStyle={"marginRight": "6px"},
                                      labelStyle={"fontSize": "12px"}),
                        dcc.Input(id=f"{P}-rv-kw", type="text",
                                  debounce=False, placeholder="keyword",
                                  style={**INPUT, "width": "200px",
                                         "marginTop": "4px",
                                         "fontFamily": theme.FONT_NUM}),
                    ]),
                    html.Button("Save & next", id=f"{P}-rv-save", n_clicks=0,
                                style=btn("primary")),
                    html.Button("Split…", id=f"{P}-rv-split", n_clicks=0,
                                style=btn()),
                    html.Button("Skip", id=f"{P}-rv-skip", n_clicks=0,
                                style=btn()),
                    html.Button("Back", id=f"{P}-rv-back", n_clicks=0,
                                style=btn()),
                ),
            ], style={"marginTop": "6px"}),
            html.Div(id=f"{P}-rv-msg", style={"marginTop": "10px",
                                              "minHeight": "16px"}),
        ], id=f"{P}-review", style=theme.CARD),
        split_panel(P),

        html.Div([
            html.Div("All pending lines", style=theme.CARD_TITLE),
            assign_bar(P, "Assign to selected", note=True, extra=[
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
                style_cell_conditional=money_cells("amount") + [
                    {"if": {"column_id": "description"}, "width": "42%"}],
                **TABLE),
        ], style=theme.CARD),
        dcc.Store(id=f"{P}-refresh", data=0),
        dcc.Store(id=f"{P}-cur"),
        dcc.Store(id=f"{P}-skip", data=[]),
        dcc.Store(id=f"{P}-acts", data=[]),
    ])


register_subcategories(P)
register_subcategories(f"{P}-rv")
register_split(P, f"{P}-refresh")


def _cutoff() -> str:
    return str(settings.get(CUTOFF_KEY, CUTOFF_DEFAULT))[:10]


def _rows(group, date_from, date_to, search, old):
    df = repo.pending()
    if df.empty:
        return df.assign(group=[])
    df = df.assign(group=df["description"].map(sp.queue_group))
    if "y" not in (old or []):
        df = df[df["date"] >= _cutoff()]
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


FILTERS = [Input(f"{P}-group", "value"), Input(f"{P}-from", "date"),
           Input(f"{P}-to", "date"), Input(f"{P}-search", "value"),
           Input(f"{P}-old", "value")]


@callback(
    Output(f"{P}-table", "data"),
    Output(f"{P}-info", "children"),
    Output(f"{P}-group", "options"),
    *FILTERS,
    Input(f"{P}-refresh", "data"),
)
def _load(group, date_from, date_to, search, old, _refresh):
    allp = repo.pending()
    cutoff = _cutoff()
    older = int((allp["date"] < cutoff).sum()) if not allp.empty else 0
    if "y" not in (old or []) and not allp.empty:
        allp = allp[allp["date"] >= cutoff]
    groups = (allp["description"].map(sp.queue_group).value_counts()
              if not allp.empty else {})
    options = [{"label": f"All ({len(allp)})", "value": "All"}] + [
        {"label": f"{g} ({int(groups.get(g, 0))})", "value": g}
        for g in sp.QUEUE_GROUPS]
    df = _rows(group, date_from, date_to, search, old)
    hidden = (f" · {older:,} older line{'s' * (older != 1)} before "
              f"{pd.Timestamp(cutoff):%d %b %Y} not shown"
              if older and "y" not in (old or []) else "")
    if df.empty:
        info = ("Nothing pending - all clear." if allp.empty
                else "No pending rows match these filters.") + hidden
        return [], info, options
    data = [{"id": int(r["id"]), "date": r["date"], "card": r["card"],
             "description": r["description"], "amount": r["amount"],
             "group": r["group"],
             "note": r["note"] if isinstance(r["note"], str) else ""}
            for r in df.to_dict("records")]
    info = (f"{len(df):,} rows shown · {gbp(df['amount'].sum())} · "
            f"oldest {df['date'].min()}" + hidden)
    return data, info, options


# --- One by one -----------------------------------------------------------------

def _chip_label(s, names) -> str:
    seen = f"{s['count']}×, last {pd.Timestamp(s['last']):%b %Y}"
    if s["template"]:
        return f"Split: {names.get(s['template'], 'template')} · {seen}"
    note = f" · {s['note']}" if s["note"] else ""
    return f"{s['category']} / {s['subcategory']}{note} · {seen}"


def _chips(line, hist):
    """The one-click buttons for a line, and what each one does."""
    acts, out = [], []
    tpl = repo.templates()
    names = dict(zip(tpl["id"].astype(int), tpl["name"]))
    sugs = sp.suggestions(line["description"], line["amount"],
                          sp.history_index(hist))
    sugs = [s for s in sugs if not s["template"] or s["template"] in names]

    def row(label, buttons):
        if buttons:
            out.append(html.Div([html.Div(label, style=LABEL),
                                 html.Div(buttons, style={
                                     "display": "flex", "flexWrap": "wrap"})],
                                style={"marginBottom": "4px"}))

    btns = []
    for s in sugs:
        acts.append({"template": s["template"], "category": s["category"],
                     "subcategory": s["subcategory"], "note": s["note"]})
        btns.append(html.Button(_chip_label(s, names), n_clicks=0,
                                id={"type": "sp-mq-act", "i": len(acts) - 1},
                                style=CHIP_STRONG))
    row("From your history", btns)

    group = sp.queue_group(line["description"])
    btns = []
    for t in tpl.to_dict("records")[:6]:
        if any(a["template"] == int(t["id"]) for a in acts):
            continue
        try:
            sp.resolve_parts(line["amount"], t["parts"])
        except ValueError:
            continue                    # a template that cannot fit this line
        acts.append({"template": int(t["id"])})
        btns.append(html.Button(f"Split: {t['name']}", n_clicks=0,
                                id={"type": "sp-mq-act", "i": len(acts) - 1},
                                style=CHIP))
    row("Templates", btns)

    recent = hist[hist["date"] >= (pd.Timestamp.today()
                                   - pd.DateOffset(years=3)).strftime("%Y-%m-%d")]
    btns = []
    for cat, sub in sp.top_categories(recent, group):
        if any(a.get("category") == cat and a.get("subcategory") == sub
               and not a.get("note") for a in acts):
            continue
        acts.append({"category": cat, "subcategory": sub})
        btns.append(html.Button(f"{cat} / {sub}", n_clicks=0,
                                id={"type": "sp-mq-act", "i": len(acts) - 1},
                                style=CHIP))
    row(f"Quick ({group})", btns)
    return out, acts


@callback(
    Output(f"{P}-rv-line", "children"),
    Output(f"{P}-rv-chips", "children"),
    Output(f"{P}-cur", "data"),
    Output(f"{P}-acts", "data"),
    Output(f"{P}-rv-kw", "value"),
    Output(f"{P}-rv-note", "value"),
    Output(f"{P}-rv-remember", "value"),
    *FILTERS,
    Input(f"{P}-refresh", "data"),
    Input(f"{P}-skip", "data"),
)
def _review(group, date_from, date_to, search, old, _r, skip):
    df = _rows(group, date_from, date_to, search, old)
    skip = set(skip or [])
    todo = df[~df["id"].isin(skip)] if not df.empty else df
    if todo.empty:
        text = ("Nothing left to review here." if df.empty else
                f"All {len(df)} left are skipped - Back brings them back.")
        return (html.Div(text, style=NOTE), "", None, [], "", "", [])
    line = todo.iloc[0].to_dict()
    hist = repo.history()
    chips, acts = _chips(line, hist)
    head = html.Div([
        html.Div([
            html.Span(f"{pd.Timestamp(line['date']):%a %d %b %Y}",
                      style={"marginRight": "12px"}),
            html.Span(line["card"], style={"marginRight": "12px"}),
            html.Span(line["group"]),
            html.Span(f" · {len(todo)} to go"
                      + (f", {len(skip & set(df['id']))} skipped"
                         if skip & set(df["id"]) else ""),
                      style={"color": theme.NEUTRAL}),
        ], style={**NOTE, "marginBottom": "4px"}),
        html.Div([
            html.Span(line["description"], style={
                "fontFamily": theme.FONT_NUM, "fontSize": "14px",
                "color": theme.INK, "fontWeight": 600, "marginRight": "18px"}),
            html.Span(gbp(line["amount"]), style={
                **theme.NUM, "fontSize": "16px", "fontWeight": 700,
                "color": theme.NEGATIVE if line["amount"] < 0
                else theme.POSITIVE}),
        ]),
    ])
    note = line["note"] if isinstance(line.get("note"), str) else ""
    return (head, chips or html.Div("Nothing in your history like this one "
                                    "yet - pick a category below.",
                                    style=NOTE),
            int(line["id"]), acts, sp.remember_keyword(line["description"]),
            note, [])


def _save_line(tid, act, note, remember, kw) -> str:
    """Place one line as an action says. Returns what happened."""
    line = repo.by_ids([tid])
    if line.empty:
        return "That line is no longer there."
    r = line.iloc[0]
    if act.get("template"):
        t = repo.template(act["template"])
        try:
            parts = sp.resolve_parts(float(r["amount"]), t["parts"])
        except (ValueError, TypeError) as exc:
            return f"Template does not fit: {exc}."
        problem = repo.save_split(tid, parts, int(act["template"]))
        if problem:
            return problem
        if note:
            repo.set_note([tid], note)
        return f"Split with '{t['name']}'."
    cat, sub = act["category"], act["subcategory"]
    use_note = note or act.get("note")
    repo.assign([tid], cat, sub, note=use_note if use_note else None)
    text = f"{cat} / {sub}."
    if "y" in (remember or []):
        _, said = remember_rule([{"description": r["description"],
                                  "amount": r["amount"]}], kw, cat, sub,
                                use_note)
        text += " " + said
    return text


@callback(
    Output(f"{P}-rv-msg", "children"),
    Output(f"{P}-refresh", "data", allow_duplicate=True),
    Output(f"{P}-skip", "data"),
    Input({"type": "sp-mq-act", "i": ALL}, "n_clicks"),
    Input(f"{P}-rv-save", "n_clicks"),
    Input(f"{P}-rv-skip", "n_clicks"),
    Input(f"{P}-rv-back", "n_clicks"),
    State(f"{P}-cur", "data"),
    State(f"{P}-acts", "data"),
    State(f"{P}-rv-cat", "value"),
    State(f"{P}-rv-sub", "value"),
    State(f"{P}-rv-note", "value"),
    State(f"{P}-rv-remember", "value"),
    State(f"{P}-rv-kw", "value"),
    State(f"{P}-skip", "data"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _act(_clicks, _s, _k, _b, cur, acts, cat, sub, note, remember, kw, skip,
         refresh):
    trig = ctx.triggered_id
    skip = list(skip or [])
    if trig == f"{P}-rv-back":
        if not skip:
            return message("Nothing skipped.", ok=False), no_update, no_update
        skip.pop()
        return "", no_update, skip
    if cur is None:
        return no_update, no_update, no_update
    if trig == f"{P}-rv-skip":
        return "", no_update, skip + [int(cur)]
    if isinstance(trig, dict):
        # Rebuilt buttons arrive with n_clicks 0: not a click.
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            return no_update, no_update, no_update
        i = int(trig["i"])
        if i >= len(acts or []):
            return no_update, no_update, no_update
        act = acts[i]
    else:
        if not (cat and sub):
            return (message("Pick a category and sub-category, or click a "
                            "suggestion.", ok=False), no_update, no_update)
        act = {"category": cat, "subcategory": sub}
    said = _save_line(int(cur), act, (note or "").strip(), remember, kw)
    return message("Saved: " + said), (refresh or 0) + 1, no_update


@callback(
    Output(f"{P}-split-txn", "data"),
    Input(f"{P}-rv-split", "n_clicks"),
    State(f"{P}-cur", "data"),
    prevent_initial_call=True,
)
def _open_split(n, cur):
    if cur is None:
        return no_update
    return {"id": int(cur), "nonce": n}


# --- All pending lines -------------------------------------------------------------

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
    """Pre-fill the keyword from the first selected row."""
    by_id = {r["id"]: r for r in data or []}
    for i in selected or []:
        r = by_id.get(i)
        if r:
            return sp.remember_keyword(r["description"]), []
    return "", []


@callback(
    Output(f"{P}-msg", "children"),
    Output(f"{P}-refresh", "data"),
    Input(f"{P}-apply", "n_clicks"),
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
def _apply(_n, selected, data, cat, sub, note, remember, kw, refresh):
    if not selected:
        return message("Tick at least one row first.", ok=False), no_update
    if not (cat and sub):
        return message("Pick a category and sub-category.", ok=False), no_update
    n = repo.assign(selected, cat, sub, note=note if note else None)
    parts = [f"Assigned {n} row{'s' * (n != 1)} to {cat} / {sub}."]
    if "y" in (remember or []):
        parts.append(_remember(selected, data, kw, cat, sub, note))
    return message(" ".join(p for p in parts if p)), (refresh or 0) + 1


def _remember(selected, data, kw, cat, sub, note) -> str:
    by_id = {r["id"]: r for r in data or []}
    rows = [by_id[i] for i in selected if i in by_id]
    plan, said = remember_rule(rows, kw, cat, sub, note)
    if not plan:
        return said
    # Other pending rows the new rule now places.
    rules = repo.rule_tuples()
    key = sp.rule_key(plan["keyword"], plan["amount_kind"], plan["amount"])
    more = [int(r["id"]) for r in repo.pending().to_dict("records")
            if sp.classify(r["description"], rules, r["amount"]).key == key]
    if more:
        repo.assign(more, cat, sub, note=note if note else None)
        return (f"{said[:-1]}; it also placed {len(more)} more pending "
                f"row{'s' * (len(more) != 1)}.")
    return said
