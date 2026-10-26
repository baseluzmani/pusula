"""
Building blocks shared by the Spending sub-tabs: table styling, buttons, the
filter-bar field, the "assign" bar (category, sub-category, note, remember)
used by Import, Manual Queue, Transactions and Statements, and the split
editor.

Money columns in tables are numbers formatted by the table (money_col), so
they sort as numbers - never pre-formatted text, which sorts as text.
"""

from __future__ import annotations

import pandas as pd
from dash import html, dcc, callback, Input, Output, State, ctx, no_update
from dash.dash_table.Format import Format, Group, Scheme, Sign

from core import theme, spending as sp
from core.repo import spending as repo

# (1,234.50) for spending, 1,234.50 for credits - as gbp() writes it.
MONEY = Format(precision=2, scheme=Scheme.fixed, group=Group.yes,
               sign=Sign.parantheses)


def money_col(name: str, col_id: str) -> dict:
    """A DataTable column holding a number shown as money."""
    return {"name": name, "id": col_id, "type": "numeric", "format": MONEY}

LABEL = {"fontSize": "10px", "fontWeight": 600, "letterSpacing": "0.06em",
         "textTransform": "uppercase", "color": theme.SLATE,
         "marginBottom": "4px"}

INPUT = {"padding": "6px 9px", "fontSize": "12.5px", "borderRadius": "5px",
         "border": f"1px solid {theme.LINE}"}

NOTE = {"fontSize": "11.5px", "color": theme.SLATE}

# DataTable styling: dark header like the other Pusula grids, numbers in the
# tabular mono face, right-aligned.
TABLE = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": theme.INK, "color": theme.SURFACE,
                  "fontWeight": 600, "fontSize": "10.5px",
                  "letterSpacing": "0.05em", "textTransform": "uppercase",
                  "border": "none", "textAlign": "left"},
    style_cell={"padding": "5px 9px", "fontSize": "12px",
                "fontFamily": theme.FONT_UI, "textAlign": "left",
                "color": theme.TEXT, "border": "none",
                "borderBottom": f"1px solid {theme.LINE_SOFT}",
                "whiteSpace": "nowrap", "overflow": "hidden",
                "textOverflow": "ellipsis", "maxWidth": "360px"},
    css=[{"selector": ".dash-spreadsheet tr:hover td",
          "rule": f"background-color: {theme.CANVAS} !important;"}],
)


def money_cells(*cols):
    """style_cell_conditional entries for right-aligned money columns."""
    return [{"if": {"column_id": c}, "textAlign": "right",
             "fontFamily": theme.FONT_NUM,
             "fontVariantNumeric": "tabular-nums"} for c in cols]


def btn(kind: str = "plain") -> dict:
    base = {"padding": "7px 14px", "fontSize": "12px", "fontWeight": 600,
            "borderRadius": "5px", "cursor": "pointer",
            "whiteSpace": "nowrap"}
    if kind == "primary":
        return {**base, "border": f"1px solid {theme.INK}",
                "background": theme.INK, "color": theme.SURFACE}
    if kind == "go":
        return {**base, "border": f"1px solid {theme.POSITIVE}",
                "background": theme.POSITIVE, "color": theme.SURFACE}
    return {**base, "border": f"1px solid {theme.LINE}",
            "background": theme.SURFACE, "color": theme.TEXT}


def field(label: str, component, width: str | None = None):
    return html.Div([html.Div(label, style=LABEL), component],
                    style={"width": width} if width else {})


def bar(*children, **style):
    """A row of filter fields / buttons, aligned on their bottom edge."""
    return html.Div(list(children), style={
        "display": "flex", "gap": "12px", "flexWrap": "wrap",
        "alignItems": "flex-end", **style})


def header(title: str, subtitle: str):
    return html.Div([
        html.Div(title, style={"fontSize": "17px", "fontWeight": 700,
                               "color": theme.INK}),
        html.Div(subtitle, style={"fontSize": "12px", "color": theme.SLATE,
                                  "marginTop": "2px"}),
    ], style={"marginBottom": "14px"})


def message(text: str, ok: bool = True):
    if not text:
        return ""
    return html.Span(text, style={"fontSize": "12px",
                                  "color": theme.POSITIVE if ok
                                  else theme.NEGATIVE})


def select_rows(data, ids):
    """(selected_rows, selected_row_ids) for a DataTable. A DataTable draws
    its tick boxes from selected_rows - indices into data - so setting only
    the ids leaves the boxes as they were."""
    ids = list(ids or [])
    wanted = set(ids)
    return [i for i, r in enumerate(data or []) if r["id"] in wanted], ids


def category_options():
    return [{"label": c, "value": c} for c in repo.categories()]


def gbp(value) -> str:
    """Signed money for tables: -12.5 -> '(12.50)', 3439 -> '3,439.00'."""
    if value is None or value != value:
        return ""
    return theme.money(float(value), currency="", dp=2)


# --- Assign bar ---------------------------------------------------------------

def assign_bar(prefix: str, button_label: str = "Assign to selected",
               remember: bool = True, extra=None, note: bool = False):
    """Category / sub-category pickers, an optional note box, an optional
    Remember tick with its keyword, and an apply button. Ids:
    {prefix}-cat, -sub, -note, -remember, -kw, -apply. Call
    register_subcategories(prefix) once at import.

    Remember saves a keyword rule; for Amazon, PayPal and cash lines it
    saves the keyword with the line's exact amount (core.spending.
    remember_plan), so the same subscription is placed next time."""
    remember_part = []
    if remember:
        remember_part = [
            html.Div([
                dcc.Checklist(id=f"{prefix}-remember",
                              options=[{"label": " Remember as a rule",
                                        "value": "y"}],
                              value=[], inputStyle={"marginRight": "6px"},
                              labelStyle={"fontSize": "12px",
                                          "color": theme.TEXT}),
                dcc.Input(id=f"{prefix}-kw", type="text", debounce=False,
                          placeholder="keyword", style={**INPUT,
                          "width": "220px", "marginTop": "4px",
                          "fontFamily": theme.FONT_NUM}),
            ]),
        ]
    note_part = [field("Note", dcc.Input(
        id=f"{prefix}-note", type="text", debounce=False,
        placeholder="optional", style={**INPUT, "width": "170px"}))] \
        if note else []
    return bar(
        field("Category", dcc.Dropdown(id=f"{prefix}-cat",
                                       options=category_options(),
                                       clearable=False,
                                       style={"fontSize": "12.5px"}),
              width="190px"),
        field("Sub-category", dcc.Dropdown(id=f"{prefix}-sub", options=[],
                                           clearable=False,
                                           style={"fontSize": "12.5px"}),
              width="190px"),
        *note_part,
        *remember_part,
        html.Button(button_label, id=f"{prefix}-apply", n_clicks=0,
                    style=btn("primary")),
        *(extra or []),
    )


def register_subcategories(prefix: str):
    """Sub-category options follow the category. The current sub-category is
    kept if it belongs to the new category (a form filled in one go sets
    both), otherwise the first one is picked."""
    @callback(Output(f"{prefix}-sub", "options"),
              Output(f"{prefix}-sub", "value"),
              Input(f"{prefix}-cat", "value"),
              State(f"{prefix}-sub", "value"))
    def _subs(cat, current):
        subs = repo.categories().get(cat, []) if cat else []
        value = current if current in subs else (subs[0] if subs else None)
        return [{"label": s, "value": s} for s in subs], value
    return _subs



def remember_rule(rows, keyword: str, cat: str, sub: str,
                  note: str | None = None) -> tuple[dict | None, str]:
    """Save the rule Remember asks for: (plan or None, message)."""
    plan, problem = sp.remember_plan(rows, keyword)
    if problem:
        return None, "Not remembered: " + problem
    label = repo.save_rule(plan["keyword"], cat, sub,
                           amount_kind=plan["amount_kind"],
                           amount=plan["amount"], note=note)
    return plan, f"Rule '{label}' saved."


# --- Split editor --------------------------------------------------------------

SPLIT_ROWS = 6


def split_panel(prefix: str):
    """The split editor, hidden until a line is picked. A page opens it by
    writing {'id': transaction id, 'nonce': anything new} to
    {prefix}-split-txn, and lays it out wherever it likes. Call
    register_split(prefix, refresh_id) once at import.

    Amount boxes take pounds ('120'), a share ('40%'), or nothing - the part
    that takes what is left."""
    drop = {"fontSize": "12.5px"}
    rows = []
    for i in range(SPLIT_ROWS):
        rows.append(html.Div([
            dcc.Input(id=f"{prefix}-split-a{i}", type="text", debounce=False,
                      placeholder="rest" if i else "£ or %",
                      style={**INPUT, "width": "90px",
                             "fontFamily": theme.FONT_NUM}),
            html.Div(dcc.Dropdown(id=f"{prefix}-split-c{i}",
                                  options=category_options(),
                                  placeholder="Category", style=drop),
                     style={"width": "180px"}),
            html.Div(dcc.Dropdown(id=f"{prefix}-split-s{i}", options=[],
                                  placeholder="Sub-category", style=drop),
                     style={"width": "180px"}),
            dcc.Input(id=f"{prefix}-split-n{i}", type="text", debounce=False,
                      placeholder="note", style={**INPUT, "width": "170px"}),
        ], id=f"{prefix}-split-row{i}", style={
            "display": "flex" if i < 2 else "none", "gap": "8px",
            "alignItems": "center", "marginBottom": "6px"}))
    return html.Div([
        html.Div([
            html.Div("Split", style=theme.CARD_TITLE),
            html.Div(id=f"{prefix}-split-title",
                     style={**NOTE, "color": theme.TEXT, "marginBottom": "10px"}),
        ]),
        bar(
            field("Template", dcc.Dropdown(id=f"{prefix}-split-tpl",
                                           options=[], placeholder="none",
                                           style=drop), width="240px"),
            html.Button("Fill from template", id=f"{prefix}-split-tpl-go",
                        n_clicks=0, style=btn()),
            style={"marginBottom": "10px"}),
        *rows,
        bar(
            html.Button("+ part", id=f"{prefix}-split-add", n_clicks=0,
                        style=btn()),
            html.Button("− part", id=f"{prefix}-split-del", n_clicks=0,
                        style=btn()),
            html.Span(id=f"{prefix}-split-left", style=NOTE),
            style={"margin": "4px 0 12px 0", "alignItems": "center"}),
        bar(
            html.Button("Save split", id=f"{prefix}-split-save", n_clicks=0,
                        style=btn("primary")),
            html.Button("Back to one line", id=f"{prefix}-split-remove",
                        n_clicks=0, style=btn()),
            html.Button("Close", id=f"{prefix}-split-close", n_clicks=0,
                        style=btn()),
            field("Save as template", dcc.Input(
                id=f"{prefix}-split-tname", type="text", debounce=False,
                placeholder="e.g. Cash: cleaning + tutor",
                style={**INPUT, "width": "220px"})),
            html.Button("Save template", id=f"{prefix}-split-tsave",
                        n_clicks=0, style=btn()),
            style={"alignItems": "flex-end"}),
        html.Div(id=f"{prefix}-split-msg", style={"marginTop": "8px",
                                                   "minHeight": "16px"}),
        dcc.Store(id=f"{prefix}-split-txn"),
        dcc.Store(id=f"{prefix}-split-n", data=2),
        dcc.Store(id=f"{prefix}-split-used"),
    ], id=f"{prefix}-split", style={**theme.CARD, "display": "none"})


def _tpl_options():
    t = repo.templates()
    return [{"label": f"{r['name']} ({int(r['uses'])}×)" if r["uses"]
             else r["name"], "value": int(r["id"])}
            for r in t.to_dict("records")]


def _part_inputs(values, n):
    """Rows typed into the editor -> part dicts (kind, value, category,
    subcategory, note). Raises ValueError for an unreadable amount."""
    out = []
    for i in range(n):
        a, c, s_, note = values[i]
        kind, value = sp.parse_part_amount(a)
        out.append({"kind": kind, "value": value, "category": c,
                    "subcategory": s_, "note": note})
    return out


def register_split(prefix: str, refresh_id: str):
    """Callbacks for split_panel(prefix). refresh_id: the page's store to
    bump after a save, so its table reloads."""
    rows = range(SPLIT_ROWS)
    row_values = ([Output(f"{prefix}-split-a{i}", "value") for i in rows]
                  + [Output(f"{prefix}-split-c{i}", "value") for i in rows]
                  + [Output(f"{prefix}-split-s{i}", "value") for i in rows]
                  + [Output(f"{prefix}-split-n{i}", "value") for i in rows])
    row_states = ([State(f"{prefix}-split-a{i}", "value") for i in rows]
                  + [State(f"{prefix}-split-c{i}", "value") for i in rows]
                  + [State(f"{prefix}-split-s{i}", "value") for i in rows]
                  + [State(f"{prefix}-split-n{i}", "value") for i in rows])

    def flat(parts):
        """Part dicts -> the 4 x SPLIT_ROWS values the outputs expect."""
        parts = list(parts)[:SPLIT_ROWS]
        pad = parts + [{}] * (SPLIT_ROWS - len(parts))
        return ([sp.part_text(p.get("kind", "rest"), p.get("value"))
                 if p else "" for p in pad]
                + [p.get("category") for p in pad]
                + [p.get("subcategory") for p in pad]
                + [p.get("note") or "" for p in pad])

    def grouped(values):
        n = SPLIT_ROWS
        return [(values[i], values[n + i], values[2 * n + i],
                 values[3 * n + i]) for i in range(n)]

    @callback(
        Output(f"{prefix}-split", "style"),
        Output(f"{prefix}-split-title", "children"),
        Output(f"{prefix}-split-n", "data"),
        Output(f"{prefix}-split-tpl", "options"),
        Output(f"{prefix}-split-used", "data"),
        Output(f"{prefix}-split-msg", "children"),
        *row_values,
        Input(f"{prefix}-split-txn", "data"),
        prevent_initial_call=True,
    )
    def _load(txn):
        hidden = {**theme.CARD, "display": "none"}
        if not txn:
            return (hidden, "", 2, [], None, "", *flat([]))
        line = repo.by_ids([txn["id"]])
        if line.empty:
            return (hidden, "", 2, [], None, "", *flat([]))
        r = line.iloc[0]
        have = repo.splits_for([txn["id"]])
        parts = [{"kind": "fixed", "value": abs(p["amount"]),
                  "category": p["category"], "subcategory": p["subcategory"],
                  "note": p["note"] if isinstance(p["note"], str) else ""}
                 for p in have.to_dict("records")]
        if parts:
            parts[-1]["kind"] = "rest"           # shown blank: takes the rest
        else:
            parts = [{}, {}]
        title = (f"{pd_date(r['date'])} · {r['card']} · {r['description']} · "
                 f"{gbp(r['amount'])}")
        return ({**theme.CARD, "display": "block"}, title,
                max(2, len(have)), _tpl_options(), None, "", *flat(parts))

    @callback(
        *[Output(f"{prefix}-split-row{i}", "style") for i in rows],
        Input(f"{prefix}-split-n", "data"),
    )
    def _visible(n):
        n = int(n or 2)
        return [{"display": "flex" if i < n else "none", "gap": "8px",
                 "alignItems": "center", "marginBottom": "6px"} for i in rows]

    @callback(
        Output(f"{prefix}-split-n", "data", allow_duplicate=True),
        Input(f"{prefix}-split-add", "n_clicks"),
        Input(f"{prefix}-split-del", "n_clicks"),
        State(f"{prefix}-split-n", "data"),
        prevent_initial_call=True,
    )
    def _count(_a, _d, n):
        n = int(n or 2)
        if ctx.triggered_id == f"{prefix}-split-add":
            return min(SPLIT_ROWS, n + 1)
        return max(2, n - 1)

    @callback(
        Output(f"{prefix}-split-n", "data", allow_duplicate=True),
        Output(f"{prefix}-split-used", "data", allow_duplicate=True),
        Output(f"{prefix}-split-msg", "children", allow_duplicate=True),
        *[Output(o.component_id, o.component_property, allow_duplicate=True)
          for o in row_values],
        Input(f"{prefix}-split-tpl-go", "n_clicks"),
        State(f"{prefix}-split-tpl", "value"),
        prevent_initial_call=True,
    )
    def _template(_n, tpl_id):
        t = repo.template(tpl_id) if tpl_id is not None else None
        if not t:
            return (no_update, no_update,
                    message("Pick a template first.", ok=False),
                    *[no_update] * (4 * SPLIT_ROWS))
        parts = t["parts"]
        return (max(2, len(parts)), int(tpl_id), "", *flat(parts))

    for i in rows:
        @callback(
            Output(f"{prefix}-split-s{i}", "options"),
            Output(f"{prefix}-split-s{i}", "value", allow_duplicate=True),
            Input(f"{prefix}-split-c{i}", "value"),
            State(f"{prefix}-split-s{i}", "value"),
            prevent_initial_call=True,
        )
        def _subs(cat, current):
            subs = repo.categories().get(cat, []) if cat else []
            value = current if current in subs else (subs[0] if subs else None)
            return [{"label": x, "value": x} for x in subs], value

    @callback(
        Output(f"{prefix}-split-left", "children"),
        *[Input(f"{prefix}-split-a{i}", "value") for i in rows],
        Input(f"{prefix}-split-n", "data"),
        State(f"{prefix}-split-txn", "data"),
    )
    def _left(*args):
        amounts, n, txn = args[:SPLIT_ROWS], int(args[-2] or 2), args[-1]
        if not txn:
            return ""
        line = repo.by_ids([txn["id"]])
        if line.empty:
            return ""
        total = float(line["amount"].iloc[0])
        try:
            parts = [{"kind": k, "value": v} for k, v in
                     (sp.parse_part_amount(a) for a in amounts[:n])]
            resolved = sp.resolve_parts(total, parts)
        except ValueError as exc:
            return html.Span(str(exc).capitalize() + ".",
                             style={"color": theme.NEGATIVE})
        return " + ".join(gbp(p["amount"]) for p in resolved) + \
            f" = {gbp(total)}"

    @callback(
        Output(f"{prefix}-split", "style", allow_duplicate=True),
        Output(f"{prefix}-split-msg", "children", allow_duplicate=True),
        Output(refresh_id, "data", allow_duplicate=True),
        Input(f"{prefix}-split-save", "n_clicks"),
        Input(f"{prefix}-split-remove", "n_clicks"),
        Input(f"{prefix}-split-close", "n_clicks"),
        State(f"{prefix}-split-txn", "data"),
        State(f"{prefix}-split-n", "data"),
        State(f"{prefix}-split-used", "data"),
        State(refresh_id, "data"),
        *row_states,
        prevent_initial_call=True,
    )
    def _finish(_s, _r, _c, txn, n, used, refresh, *values):
        hidden = {**theme.CARD, "display": "none"}
        trig = ctx.triggered_id
        if trig == f"{prefix}-split-close" or not txn:
            return hidden, "", no_update
        bump = (refresh or 0) + 1
        if trig == f"{prefix}-split-remove":
            repo.remove_split(txn["id"])
            return hidden, "", bump
        line = repo.by_ids([txn["id"]])
        if line.empty:
            return hidden, "", bump
        try:
            parts = sp.resolve_parts(float(line["amount"].iloc[0]),
                                     _part_inputs(grouped(values),
                                                  int(n or 2)))
        except ValueError as exc:
            return no_update, message(str(exc).capitalize() + ".",
                                      ok=False), no_update
        problem = repo.save_split(txn["id"], parts, used)
        if problem:
            return no_update, message(problem, ok=False), no_update
        return hidden, "", bump

    @callback(
        Output(f"{prefix}-split-msg", "children", allow_duplicate=True),
        Output(f"{prefix}-split-tpl", "options", allow_duplicate=True),
        Output(f"{prefix}-split-used", "data", allow_duplicate=True),
        Input(f"{prefix}-split-tsave", "n_clicks"),
        State(f"{prefix}-split-tname", "value"),
        State(f"{prefix}-split-n", "data"),
        *row_states,
        prevent_initial_call=True,
    )
    def _save_template(_n, name, n, *values):
        try:
            parts = _part_inputs(grouped(values), int(n or 2))
        except ValueError as exc:
            return (message(str(exc).capitalize() + ".", ok=False), no_update,
                    no_update)
        problem = repo.save_template(name, parts)
        if problem:
            return message(problem, ok=False), no_update, no_update
        t = repo.templates()
        tid = t.loc[t["name"] == name.strip(), "id"]
        # The split being edited counts as made from it.
        return (message(f"Template '{name.strip()}' saved - pick it in the "
                        "queue or here next time."), _tpl_options(),
                int(tid.iloc[0]) if len(tid) else no_update)


def pd_date(iso) -> str:
    """'2026-09-25' -> '25 Sep 2026'; blank for a missing date."""
    return f"{pd.Timestamp(iso):%d %b %Y}" if isinstance(iso, str) and iso \
        else ""
