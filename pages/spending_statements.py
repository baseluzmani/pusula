"""
Spending -> Statements.

The statements you have uploaded, card by card, and how each one stands:

    Reconciled   the stored lines add up to the statement's debits and
                 credits, its opening balance is the previous statement's
                 closing one, and nothing dated inside its period is on no
                 statement
    Break        one of those fails - the detail says which
    Missing      a hole between two statements of a card: one never
                 uploaded

The coverage grid shows a card's months at a glance (a statement belongs to
the month it ends in). Amber is a month with lines on no statement - CSV
rows waiting for their statement, or history from before the statements.

Click a statement (in the grid or the table) to see its totals, its checks,
its spending by category (split lines by their parts) and its lines, which
can be re-categorised, noted and split here.
"""

from __future__ import annotations

import pandas as pd
from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  ALL, no_update)

from core import theme, spending as sp
from core.repo import spending as repo
from ui.spend import (TABLE, money_cells, money_col, btn, field, bar, header,
                      message, gbp, assign_bar, register_subcategories,
                      split_panel, register_split, remember_rule, NOTE)

P = "sp-st"
LAST_24 = "Last 24 months"
STATUSES = ["All", "Reconciled", "Breaks", "Missing"]

COLUMNS = [
    {"name": "Card", "id": "card"},
    {"name": "Period", "id": "period"},
    money_col("Opening", "opening"),
    money_col("Debits / paid out", "debits"),
    money_col("Credits / paid in", "credits"),
    money_col("Closing", "closing"),
    {"name": "Lines", "id": "lines", "type": "numeric"},
    {"name": "Status", "id": "status"},
    {"name": "Why", "id": "why"},
]

LINE_COLUMNS = [
    {"name": "Date", "id": "date"},
    {"name": "Description", "id": "description"},
    money_col("Amount", "amount"),
    {"name": "Original", "id": "orig_fmt"},
    {"name": "Category", "id": "category"},
    {"name": "Sub-category", "id": "subcategory"},
    {"name": "Note", "id": "note"},
    {"name": "Status", "id": "status"},
]

# Grid cell looks, by state.
CELL = {
    "ok": {"background": theme.POSITIVE, "color": theme.SURFACE, "text": "✓"},
    "break": {"background": theme.NEGATIVE, "color": theme.SURFACE,
              "text": "!"},
    "missing": {"background": theme.SURFACE, "color": theme.NEGATIVE,
                "text": "?", "border": f"1px dashed {theme.NEGATIVE}"},
    "provisional": {"background": theme.TINT_HIT, "color": theme.NEEDLE,
                    "text": "~"},
    None: {"background": theme.LINE_SOFT, "color": theme.NEUTRAL, "text": ""},
}


def render():
    drop = {"fontSize": "12.5px"}
    sts = repo.statements()
    years = sorted({str(d)[:4] for d in sts["period_to"]}, reverse=True) \
        if not sts.empty else []
    return html.Div([
        header("Statements", "What each uploaded statement covers and whether "
               "the stored lines reconcile to it. Click one to see its "
               "lines."),
        html.Div([
            bar(
                field("Card", dcc.Dropdown(
                    id=f"{P}-card", value=None, placeholder="All",
                    options=repo.card_names(active_only=False), style=drop),
                    width="190px"),
                field("Period", dcc.Dropdown(
                    id=f"{P}-year", value=LAST_24, clearable=False,
                    options=[LAST_24, *years], style=drop), width="170px"),
                field("Status", dcc.RadioItems(
                    id=f"{P}-status", value="All", inline=True,
                    options=STATUSES, inputStyle={"marginRight": "5px"},
                    labelStyle={"marginRight": "14px", "fontSize": "12.5px"})),
            ),
            html.Div(id=f"{P}-info", style={**NOTE, "marginTop": "10px"}),
        ], style=theme.CARD),
        html.Div([html.Div("Coverage", style=theme.CARD_TITLE),
                  html.Div(id=f"{P}-grid")], style=theme.CARD),
        html.Div([
            dash_table.DataTable(
                id=f"{P}-table", columns=COLUMNS, data=[],
                row_selectable="single", selected_row_ids=[],
                sort_action="native", page_action="native", page_size=50,
                style_cell_conditional=money_cells(
                    "opening", "debits", "credits", "closing", "lines"),
                style_data_conditional=[
                    {"if": {"filter_query": '{status} = "Break"',
                            "column_id": "status"},
                     "color": theme.NEGATIVE, "fontWeight": 600},
                    {"if": {"filter_query": '{status} = "Missing"'},
                     "color": theme.NEGATIVE},
                    {"if": {"filter_query": '{status} = "Reconciled"',
                            "column_id": "status"},
                     "color": theme.POSITIVE, "fontWeight": 600}],
                **TABLE),
        ], style=theme.CARD),
        html.Div(id=f"{P}-detail", style={**theme.CARD, "display": "none"},
                 children=[
            html.Div(id=f"{P}-head"),
            html.Div(id=f"{P}-cats", style={"margin": "12px 0 16px 0"}),
            assign_bar(P, "Set on selected", note=True, extra=[
                html.Button("Set note only", id=f"{P}-noteonly", n_clicks=0,
                            style=btn()),
                html.Button("Split…", id=f"{P}-split-open", n_clicks=0,
                            style=btn()),
            ]),
            html.Div(id=f"{P}-msg", style={"margin": "10px 0 4px 0",
                                           "minHeight": "16px"}),
            dash_table.DataTable(
                id=f"{P}-lines", columns=LINE_COLUMNS, data=[],
                row_selectable="multi", selected_row_ids=[],
                sort_action="native", page_action="none",
                style_cell_conditional=money_cells("amount", "orig_fmt") + [
                    {"if": {"column_id": "description"}, "width": "32%"}],
                style_data_conditional=[
                    {"if": {"column_id": "orig_fmt"}, "color": theme.SLATE},
                    {"if": {"filter_query": "{status} = pending"},
                     "backgroundColor": theme.TINT_NEAR}],
                **TABLE),
        ]),
        split_panel(P),
        dcc.Store(id=f"{P}-sel"),
        dcc.Store(id=f"{P}-refresh", data=0),
    ])


register_subcategories(P)
register_split(P, f"{P}-refresh")


def _d(iso) -> str:
    return f"{pd.Timestamp(iso):%d %b %Y}" if isinstance(iso, str) and iso else ""


def _months(period: str) -> list[str]:
    if period and period != LAST_24:
        return [f"{period}-{m:02d}" for m in range(1, 13)]
    now = pd.Timestamp.today().to_period("M")
    return [str(now - i) for i in range(23, -1, -1)]


def _why(r) -> str:
    if r["ok"]:
        return ""
    bits = []
    if not r["lines_ok"]:
        diff_d = round(r["lines_debits"] - (r["debits"] or 0), 2)
        diff_c = round(r["lines_credits"] - (r["credits"] or 0), 2)
        bits.append("lines differ by " + ", ".join(
            x for x in [f"debits {diff_d:+,.2f}" if abs(diff_d) >= 0.005
                        else "", f"credits {diff_c:+,.2f}"
                        if abs(diff_c) >= 0.005 else ""] if x))
    if r["chain_ok"] is False:
        bits.append("opening ≠ previous closing")
    if r["unlinked"]:
        bits.append(f"{r['unlinked']} line{'s' * (r['unlinked'] != 1)} on no "
                    "statement")
    return "; ".join(bits)


def _overview(card, period, status):
    """(statements with checks, holes, unlinked months), filtered."""
    over = repo.statement_overview()
    missing = sp.missing_periods(over) if not over.empty else []
    unl = repo.unlinked_months()
    if card:
        over = over[over["card"] == card] if not over.empty else over
        missing = [m for m in missing if m["card"] == card]
        unl = unl[unl["card"] == card]
    months = _months(period)
    lo, hi = months[0], months[-1]
    if not over.empty:
        over = over[(over["period_to"].str[:7] >= lo)
                    & (over["period_to"].str[:7] <= hi)]
    missing = [m for m in missing if m["to"][:7] >= lo and m["from"][:7] <= hi]
    return over, missing, unl, months


def _grid(over, missing, unl, months, card, selected):
    cov = sp.coverage(months, over, missing, unl)
    cards = [card] if card else [c for c in repo.card_names(active_only=False)
                                 if c in cov or c in set(unl["card"])]
    if not cards:
        return html.Div("No statements yet - upload them on the Import tab.",
                        style=NOTE)
    th = {"fontSize": "10px", "color": theme.SLATE, "fontWeight": 600,
          "padding": "2px", "textAlign": "center"}
    head = html.Tr([html.Th("", style=th)] + [
        html.Th(pd.Period(m).strftime("%b") + (
            f" {m[2:4]}" if m.endswith("-01") or i == 0 else ""), style=th)
        for i, m in enumerate(months)])
    body = []
    for c in cards:
        cells = []
        for m in months:
            state, sid = cov.get(c, {}).get(m, (None, None))
            look = CELL[state]
            style = {"width": "28px", "height": "22px", "borderRadius": "4px",
                     "border": look.get("border", "none"),
                     "background": look["background"], "color": look["color"],
                     "fontSize": "11px", "fontWeight": 700, "padding": "0",
                     "cursor": "pointer" if sid else "default",
                     "outline": (f"2px solid {theme.INK}"
                                 if sid and sid == selected else "none")}
            title = {"ok": "reconciled", "break": "does not reconcile",
                     "missing": "statement missing",
                     "provisional": "lines on no statement",
                     None: ""}[state]
            if sid:
                cells.append(html.Td(html.Button(
                    look["text"], id={"type": "sp-st-cell", "sid": sid},
                    n_clicks=0, title=f"{c} {m}: {title}", style=style),
                    style={"padding": "2px"}))
            else:
                cells.append(html.Td(html.Div(look["text"], title=(
                    f"{c} {m}: {title}" if title else ""), style={
                    **style, "display": "flex", "alignItems": "center",
                    "justifyContent": "center"}), style={"padding": "2px"}))
        body.append(html.Tr([html.Td(c, style={
            "fontSize": "12px", "fontWeight": 600, "paddingRight": "10px",
            "whiteSpace": "nowrap"})] + cells))
    key = html.Div([
        html.Span([html.Span(CELL[s]["text"] or " ", style={
            "display": "inline-block", "width": "18px", "textAlign": "center",
            "borderRadius": "3px", "marginRight": "5px",
            "background": CELL[s]["background"], "color": CELL[s]["color"],
            "border": CELL[s].get("border", "none"), "fontWeight": 700}),
            label], style={"marginRight": "16px"})
        for s, label in [("ok", "reconciled"), ("break", "does not reconcile"),
                         ("missing", "statement missing"),
                         ("provisional", "lines on no statement")]],
        style={**NOTE, "marginTop": "10px", "fontSize": "11px"})
    return html.Div([html.Div(html.Table([html.Thead(head),
                                          html.Tbody(body)]),
                              style={"overflowX": "auto"}), key])


@callback(
    Output(f"{P}-grid", "children"),
    Output(f"{P}-table", "data"),
    Output(f"{P}-info", "children"),
    Input(f"{P}-card", "value"),
    Input(f"{P}-year", "value"),
    Input(f"{P}-status", "value"),
    Input(f"{P}-refresh", "data"),
    Input(f"{P}-sel", "data"),
)
def _load(card, period, status, _r, selected):
    over, missing, unl, months = _overview(card, period, status)
    rows = []
    for r in (over.to_dict("records") if not over.empty else []):
        rows.append({"id": int(r["id"]), "card": r["card"],
                     "period": f"{_d(r['period_from'])} – {_d(r['period_to'])}",
                     "sort": r["period_to"],
                     "opening": r["previous_balance"],
                     "debits": -r["debits"] if r["debits"] is not None else None,
                     "credits": r["credits"], "closing": r["new_balance"],
                     "lines": int(r["lines_stored"]),
                     "status": "Reconciled" if r["ok"] else "Break",
                     "why": _why(r)})
    for i, m in enumerate(missing):
        rows.append({"id": f"m{i}", "card": m["card"],
                     "period": f"{_d(m['from'])} – {_d(m['to'])}",
                     "sort": m["to"], "opening": None, "debits": None,
                     "credits": None, "closing": None, "lines": None,
                     "status": "Missing",
                     "why": "no statement uploaded for these dates"})
    want = {"Reconciled": "Reconciled", "Breaks": "Break",
            "Missing": "Missing"}.get(status)
    if want:
        rows = [r for r in rows if r["status"] == want]
    rows.sort(key=lambda r: (r["sort"], r["card"]), reverse=True)
    n_ok = sum(r["status"] == "Reconciled" for r in rows)
    n_br = sum(r["status"] == "Break" for r in rows)
    n_ms = sum(r["status"] == "Missing" for r in rows)
    info = (f"{n_ok} reconciled · {n_br} break{'s' * (n_br != 1)} · "
            f"{n_ms} missing" if rows else
            "No statements here - upload them on the Import tab.")
    return _grid(over, missing, unl, months, card, selected), rows, info


@callback(
    Output(f"{P}-sel", "data"),
    Input({"type": "sp-st-cell", "sid": ALL}, "n_clicks"),
    Input(f"{P}-table", "selected_row_ids"),
    prevent_initial_call=True,
)
def _pick(_clicks, selected):
    trig = ctx.triggered_id
    if isinstance(trig, dict):
        # A rebuilt grid arrives with every n_clicks at 0: not a click.
        if not ctx.triggered or not ctx.triggered[0]["value"]:
            return no_update
        return int(trig["sid"])
    if selected and isinstance(selected[0], int):
        return int(selected[0])
    return no_update


def _cats(rows: pd.DataFrame):
    """Spending by category for one statement - split lines by part."""
    parts = repo.expanded(rows)
    spend = parts[parts["amount"] < 0]
    if spend.empty:
        return html.Div("No spending on this statement.", style=NOTE)
    t = (spend.assign(category=spend["category"].fillna("(unassigned)"))
         .groupby("category")["amount"].sum().sort_values())
    total = float(t.sum())
    cell = {"padding": "3px 10px", "fontSize": "12px",
            "borderBottom": f"1px solid {theme.LINE_SOFT}"}
    num = {**cell, **theme.NUM, "textAlign": "right"}
    body = [html.Tr([html.Td(c, style=cell), html.Td(gbp(v), style=num),
                     html.Td(f"{v / total:.0%}" if total else "",
                             style={**num, "color": theme.SLATE})])
            for c, v in t.items()]
    body.append(html.Tr([html.Td("Spending", style={**cell,
                                                    "fontWeight": 700}),
                         html.Td(gbp(total), style={**num, "fontWeight": 700}),
                         html.Td("")]))
    return html.Div([html.Div("Spending by category", style=theme.CARD_TITLE),
                     html.Table(html.Tbody(body))])


def _check_items(r, ccy):
    def ok(flag, text):
        return html.Div([html.Span("✓ " if flag else "✗ ", style={
            "color": theme.POSITIVE if flag else theme.NEGATIVE,
            "fontWeight": 700}), text], style={"fontSize": "12px",
                                              "marginBottom": "3px"})
    m = (lambda v: gbp(v) if ccy == "GBP" else f"{gbp(v)} {ccy}")
    items = [ok(r["lines_ok"],
                f"Stored lines: debits {m(r['lines_debits'])}, credits "
                f"{m(r['lines_credits'])} - statement {m(r['debits'])} / "
                f"{m(r['credits'])}")]
    if r["chain_ok"] is None:
        items.append(html.Div("· No previous statement right before it to "
                              "chain to.", style={**NOTE, "marginBottom": "3px"}))
    else:
        items.append(ok(r["chain_ok"], f"Opening balance "
                        f"{m(r['previous_balance'])} is the previous "
                        "statement's closing one" if r["chain_ok"] else
                        f"Opening balance {m(r['previous_balance'])} is not "
                        "the previous statement's closing one"))
    items.append(ok(not r["unlinked"], "Every line dated in the period is on "
                    "a statement" if not r["unlinked"] else
                    f"{r['unlinked']} line{'s' * (r['unlinked'] != 1)} dated "
                    "in the period on no statement - upload this statement "
                    "again on the Import tab to sort them out"))
    return items


@callback(
    Output(f"{P}-detail", "style"),
    Output(f"{P}-head", "children"),
    Output(f"{P}-cats", "children"),
    Output(f"{P}-lines", "data"),
    Input(f"{P}-sel", "data"),
    Input(f"{P}-refresh", "data"),
)
def _detail(sid, _r):
    hidden = {**theme.CARD, "display": "none"}
    if not sid:
        return hidden, "", "", []
    over = repo.statement_overview()
    hit = over[over["id"] == int(sid)] if not over.empty else over
    if hit.empty:
        return hidden, "", "", []
    r = hit.iloc[0].to_dict()
    ccy = r.get("currency") or "GBP"
    rows = repo.statement_rows(sid)
    splits = repo.splits_for(rows["id"]) if not rows.empty else None
    split_text = {}
    if splits is not None and not splits.empty:
        for tid, g in splits.groupby("transaction_id"):
            split_text[int(tid)] = " · ".join(
                f"{p['subcategory']} {gbp(p['amount'])}"
                for p in g.to_dict("records"))
    data = []
    for t in rows.to_dict("records"):
        tid = int(t["id"])
        orig = ("" if pd.isna(t.get("amount_orig")) or not t.get("currency")
                else f"{gbp(t['amount_orig'])} {t['currency']}")
        data.append({"id": tid, "date": t["date"],
                     "description": t["description"], "amount": t["amount"],
                     "orig_fmt": orig,
                     "category": ("Split" if tid in split_text
                                  else t["category"] or ""),
                     "subcategory": split_text.get(tid, t["subcategory"] or ""),
                     "note": t["note"] if isinstance(t["note"], str) else "",
                     "status": t["status"]})
    title = html.Div([
        html.Div(f"{r['card']} · {_d(r['period_from'])} – "
                 f"{_d(r['period_to'])}", style={"fontSize": "15px",
                                                  "fontWeight": 700,
                                                  "color": theme.INK}),
        html.Div(f"{r['filename']} · imported "
                 f"{pd.Timestamp(r['imported_at']):%d %b %Y}"
                 + (f" · account {r['account_ref'].split(':')[-1]}"
                    if isinstance(r.get("account_ref"), str) else ""),
                 style={**NOTE, "marginBottom": "10px"}),
        *_check_items(r, ccy),
    ])
    return ({**theme.CARD, "display": "block"}, title, _cats(rows), data)


# --- Editing lines -------------------------------------------------------------

@callback(
    Output(f"{P}-lines", "selected_rows"),
    Output(f"{P}-lines", "selected_row_ids"),
    Input(f"{P}-lines", "data"),
    prevent_initial_call=True,
)
def _clear(_data):
    return [], []


@callback(
    Output(f"{P}-kw", "value"),
    Output(f"{P}-remember", "value"),
    Input(f"{P}-lines", "selected_row_ids"),
    State(f"{P}-lines", "data"),
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
    Input(f"{P}-noteonly", "n_clicks"),
    State(f"{P}-lines", "selected_row_ids"),
    State(f"{P}-lines", "data"),
    State(f"{P}-cat", "value"),
    State(f"{P}-sub", "value"),
    State(f"{P}-note", "value"),
    State(f"{P}-remember", "value"),
    State(f"{P}-kw", "value"),
    State(f"{P}-refresh", "data"),
    prevent_initial_call=True,
)
def _apply(_a, _n, selected, data, cat, sub, note, remember, kw, refresh):
    if not selected:
        return message("Tick at least one line first.", ok=False), no_update
    bump = (refresh or 0) + 1
    if ctx.triggered_id == f"{P}-noteonly":
        n = repo.set_note(selected, note)
        return message(f"Note set on {n} line{'s' * (n != 1)}."), bump
    if not (cat and sub):
        return message("Pick a category and sub-category.", ok=False), \
            no_update
    n = repo.assign(selected, cat, sub, note=note if note else None)
    text = f"Assigned {n} line{'s' * (n != 1)} to {cat} / {sub}."
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
    State(f"{P}-lines", "selected_row_ids"),
    prevent_initial_call=True,
)
def _open_split(n, selected):
    if not selected or len(selected) != 1:
        return no_update, message("Tick one line to split.", ok=False)
    return {"id": int(selected[0]), "nonce": n}, ""
