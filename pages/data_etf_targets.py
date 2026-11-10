"""
Data -> ETF targets.

The funds on ETF -> Day estimate, which are also the choices of Markets ->
Universe -> ETF. Before this tab they were rows in etf_est_targets edited in
SQL, and a new fund's holdings had to be added to instruments with
scripts/add_constituents.py.

Three parts:

    Add a fund      any fund with holdings (the Fund compare list: composites,
                    pension funds, ETFs), or an empty futures basket
    Targets grid    label, own price series, residual proxy, hedged, sort,
                    active - edited in place, written with Save. Coverage
                    columns show how much of each fund can be priced.
    Selected fund   click a row: its holdings and why each one is or is not
                    priced. "Add missing instruments" runs add_constituents
                    for that fund in the background (backup, Yahoo check,
                    insert, then Yahoo history for the new rows) and shows
                    its log here. For a basket, its futures and weights.

The target key and kind are never edited: the key is what the Day estimate
and the Markets universe store. Hide a fund with Active = 0.
"""

from __future__ import annotations

from dash import (html, dcc, dash_table, callback, Input, Output, State, ctx,
                  no_update)
from dash.dash_table.Format import Format, Scheme, Symbol

from core import background, theme
from core.repo import fundest as repo

JOB = "add_constituents"

_WT = Format(precision=2, scheme=Scheme.fixed, symbol=Symbol.yes,
             symbol_suffix="%", nully="—")

COLUMNS = [
    {"name": "Target", "id": "target", "editable": False},
    {"name": "Label", "id": "label", "editable": True},
    {"name": "Kind", "id": "kind", "editable": False},
    {"name": "Own price", "id": "actual_fund_id", "editable": True},
    {"name": "Residual proxy", "id": "residual_fund_id", "editable": True},
    {"name": "Hedged", "id": "hedged", "editable": True, "type": "numeric"},
    {"name": "Sort", "id": "sort_order", "editable": True, "type": "numeric"},
    {"name": "Active", "id": "active", "editable": True, "type": "numeric"},
    {"name": "Holdings", "id": "holdings", "editable": False,
     "type": "numeric"},
    {"name": "Priced", "id": "priced", "editable": False, "type": "numeric"},
    {"name": "Listed wt", "id": "listed", "editable": False,
     "type": "numeric", "format": _WT},
    {"name": "Priced wt", "id": "priced_weight", "editable": False,
     "type": "numeric", "format": _WT},
    {"name": "Holdings date", "id": "holdings_date", "editable": False},
]

HOLDING_COLS = [
    {"name": "Holding", "id": "name"},
    {"name": "Yahoo", "id": "fund_id"},
    {"name": "Ticker", "id": "ticker"},
    {"name": "Weight", "id": "weight_pct", "type": "numeric", "format": _WT},
    {"name": "Status", "id": "status"},
]

BASKET_COLS = [
    {"name": "Yahoo symbol (e.g. ZS=F)", "id": "fund_id", "editable": True},
    {"name": "Weight %", "id": "weight", "editable": True,
     "type": "numeric"},
]

_TABLE = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": theme.INK, "color": "white",
                  "fontWeight": "600", "fontSize": "11px",
                  "textAlign": "left", "border": "none"},
    style_cell={"padding": "5px 9px", "fontSize": "11.5px",
                "fontFamily": "DM Sans, system-ui, sans-serif",
                "textAlign": "left", "color": theme.INK,
                "border": f"1px solid {theme.LINE}",
                "maxWidth": "300px", "overflow": "hidden",
                "textOverflow": "ellipsis"},
)

_LABEL = {"fontSize": "10px", "fontWeight": 700, "letterSpacing": "0.06em",
          "textTransform": "uppercase", "color": theme.SLATE}
_INPUT = {"padding": "7px 10px", "fontSize": "12.5px", "borderRadius": "5px",
          "border": f"1px solid {theme.LINE}"}


def _button(label, id_, primary=False):
    return html.Button(label, id=id_, n_clicks=0, style={
        "padding": "7px 15px", "borderRadius": "5px", "fontSize": "12.5px",
        "fontWeight": 600, "cursor": "pointer", "whiteSpace": "nowrap",
        "border": "none" if primary else f"1px solid {theme.LINE}",
        "backgroundColor": theme.NEEDLE if primary else theme.SURFACE,
        "color": "#fff" if primary else theme.INK})


def render():
    running = background.busy(JOB)
    return html.Div([
        html.Div([
            html.Div([
                html.Div("ETF targets", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("The funds on ETF → Day estimate and Markets "
                         "→ Universe → ETF. Edit in the grid and "
                         "Save; Active 0 hides a fund. Click a row to see "
                         "its holdings and add the missing ones.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Button("Save", id="etft-save", n_clicks=0, style={
                "padding": "7px 15px", "borderRadius": "4px",
                "fontSize": "12px", "fontWeight": 600, "cursor": "pointer",
                "border": "none", "backgroundColor": theme.POSITIVE,
                "color": "#fff"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "12px"}),

        # --- Add a fund -----------------------------------------------------
        html.Div([
            html.Div([
                html.Label("Add a fund", style={**_LABEL,
                                                "marginRight": "12px"}),
                dcc.RadioItems(
                    id="etft-kind", value="holdings", inline=True,
                    options=[{"label": "Fund with holdings",
                              "value": "holdings"},
                             {"label": "Futures basket", "value": "basket"}],
                    inputStyle={"marginRight": "5px"},
                    labelStyle={"marginRight": "16px", "fontSize": "12.5px",
                                "color": theme.TEXT}),
            ], style={"display": "flex", "alignItems": "center",
                      "marginBottom": "8px"}),
            html.Div([
                html.Div(dcc.Dropdown(id="etft-pick", options=[],
                                      placeholder="Fund…",
                                      style={"fontSize": "12.5px"}),
                         id="etft-pick-wrap", style={"width": "380px"}),
                dcc.Input(id="etft-basket-name", type="text",
                          placeholder="Basket name, e.g. AIGA2",
                          style={**_INPUT, "width": "200px",
                                 "display": "none"}),
                dcc.Input(id="etft-label", type="text", placeholder="Label",
                          style={**_INPUT, "width": "300px"}),
                dcc.Input(id="etft-actual", type="text",
                          placeholder="Own price series, e.g. YF:AIGA.L",
                          style={**_INPUT, "width": "220px"}),
                _button("Add", "etft-add", primary=True),
            ], style={"display": "flex", "alignItems": "center",
                      "gap": "10px", "flexWrap": "wrap"}),
            html.Div("A fund with holdings reads them the way Fund compare "
                     "does; label and own price series are filled in from "
                     "the fund and can be changed. A basket starts empty: "
                     "add its futures and weights below once it is selected.",
                     style={"fontSize": "11px", "color": theme.NEUTRAL,
                            "marginTop": "6px"}),
        ], style={**theme.CARD, "marginBottom": "12px"}),

        html.Div(id="etft-feedback", style={"marginBottom": "10px"}),

        # --- Targets grid ---------------------------------------------------
        html.Div(dash_table.DataTable(
            id="etft-table", columns=COLUMNS, data=[], editable=True,
            row_selectable="single", selected_rows=[],
            sort_action="native", page_action="none",
            style_cell_conditional=[
                {"if": {"column_id": "target"}, "color": theme.SLATE,
                 "fontFamily": "DM Mono, ui-monospace, monospace",
                 "fontSize": "10.5px"},
            ] + [{"if": {"column_id": c}, "textAlign": "right"} for c in
                 ("hedged", "sort_order", "active", "holdings", "priced",
                  "listed", "priced_weight")],
            style_data_conditional=[
                {"if": {"filter_query": "{active} = 0"},
                 "color": theme.NEUTRAL},
            ],
            **_TABLE,
        ), style={**theme.CARD, "marginBottom": "12px"}),

        # --- Selected fund --------------------------------------------------
        html.Div(id="etft-detail-wrap", style={"display": "none"}, children=[
            html.Div([
                html.Div(id="etft-detail-head"),

                # Basket weights (baskets only)
                html.Div(id="etft-basket-wrap", style={"display": "none"},
                         children=[
                    html.Div("Futures and weights", style={
                        **theme.CARD_TITLE, "marginTop": "10px"}),
                    dash_table.DataTable(
                        id="etft-basket", columns=BASKET_COLS, data=[],
                        editable=True, row_deletable=True,
                        page_action="none",
                        style_cell_conditional=[
                            {"if": {"column_id": "weight"},
                             "textAlign": "right"}],
                        **_TABLE),
                    html.Div([
                        _button("+ Row", "etft-basket-row"),
                        _button("Save weights", "etft-basket-save",
                                primary=True),
                        html.Span(id="etft-basket-msg", style={
                            "fontSize": "12px", "color": theme.SLATE}),
                    ], style={"display": "flex", "gap": "10px",
                              "alignItems": "center", "marginTop": "8px"}),
                ]),

                # Add missing instruments
                html.Div([
                    html.Div("Missing instruments", style={
                        **theme.CARD_TITLE, "marginTop": "14px"}),
                    html.Div([
                        html.Label("Leave out holdings below",
                                   style={"fontSize": "12px",
                                          "color": theme.SLATE}),
                        dcc.Input(id="etft-minw", type="number", value=0.1,
                                  min=0, step=0.05,
                                  style={**_INPUT, "width": "80px"}),
                        html.Span("%", style={"fontSize": "12px",
                                              "color": theme.SLATE}),
                        _button("Add missing instruments", "etft-run",
                                primary=True),
                        html.Span(id="etft-run-msg", style={
                            "fontSize": "12px", "color": theme.SLATE}),
                    ], style={"display": "flex", "gap": "8px",
                              "alignItems": "center", "flexWrap": "wrap"}),
                    html.Div("Backs up funds.db, checks each symbol on "
                             "Yahoo, adds the ones it finds (shares as "
                             "Constituent, futures as Indicator, plus any FX "
                             "cross), then fetches their Yahoo history. A "
                             "few seconds per symbol; it keeps running if "
                             "you leave the page. Holdings with no Yahoo "
                             "symbol are fixed in ETF → Ticker map.",
                             style={"fontSize": "11px",
                                    "color": theme.NEUTRAL,
                                    "marginTop": "6px"}),
                    html.Pre(id="etft-log", style={
                        "display": "none", "maxHeight": "460px",
                        "overflowY": "auto", "fontSize": "11px",
                        "background": "#F6F7F9", "padding": "8px 10px",
                        "borderRadius": "4px", "marginTop": "8px",
                        "whiteSpace": "pre-wrap"}),
                ]),

                html.Div("Holdings", style={**theme.CARD_TITLE,
                                            "marginTop": "14px"}),
                dash_table.DataTable(
                    id="etft-holdings", columns=HOLDING_COLS, data=[],
                    sort_action="native", page_action="none",
                    style_cell_conditional=[
                        {"if": {"column_id": "weight_pct"},
                         "textAlign": "right"}],
                    style_data_conditional=[
                        {"if": {"filter_query":
                                f'{{status}} = "{repo.STATUS_OK}"'},
                         "color": theme.INK},
                        {"if": {"filter_query":
                                f'{{status}} != "{repo.STATUS_OK}"'},
                         "color": theme.NEGATIVE},
                        {"if": {"filter_query":
                                f'{{status}} = "{repo.STATUS_FUND}"'},
                         "fontWeight": 700, "color": theme.INK},
                    ],
                    **_TABLE),
            ], style=theme.CARD),
        ]),

        dcc.Store(id="etft-key", data=None),
        dcc.Store(id="etft-refresh", data=0),
        dcc.Store(id="etft-job", data={"log": running} if running else None),
        dcc.Interval(id="etft-poll", interval=2000,
                     disabled=not running),
    ])


# --- Add a fund -----------------------------------------------------------------

@callback(
    Output("etft-pick-wrap", "style"),
    Output("etft-basket-name", "style"),
    Output("etft-pick", "options"),
    Input("etft-kind", "value"),
    Input("etft-refresh", "data"),
)
def _kind(kind, _refresh):
    if kind == "basket":
        return ({"display": "none"}, {**_INPUT, "width": "200px"},
                no_update)
    return ({"width": "380px"}, {**_INPUT, "width": "200px",
                                 "display": "none"},
            repo.candidate_options())


@callback(
    Output("etft-label", "value"),
    Output("etft-actual", "value"),
    Input("etft-pick", "value"),
    prevent_initial_call=True,
)
def _prefill(selection):
    if not selection:
        return "", ""
    hint = repo.suggested(selection)
    return hint.get("label") or "", hint.get("actual_fund_id") or ""


@callback(
    Output("etft-feedback", "children", allow_duplicate=True),
    Output("etft-key", "data", allow_duplicate=True),
    Output("etft-refresh", "data", allow_duplicate=True),
    Output("etft-pick", "value"),
    Output("etft-basket-name", "value"),
    Input("etft-add", "n_clicks"),
    State("etft-kind", "value"), State("etft-pick", "value"),
    State("etft-basket-name", "value"), State("etft-label", "value"),
    State("etft-actual", "value"), State("etft-refresh", "data"),
    prevent_initial_call=True,
)
def _add(_n, kind, pick, basket_name, label, actual, refresh):
    selection = basket_name if kind == "basket" else pick
    ok, message, key = repo.add_target(selection, label, actual, kind)
    if not ok:
        return _msg(message, False), no_update, no_update, no_update, \
            no_update
    return _msg(message), key, (refresh or 0) + 1, None, ""


# --- Targets grid -----------------------------------------------------------------

@callback(
    Output("etft-table", "data"),
    Output("etft-table", "selected_rows"),
    Input("etft-refresh", "data"),
    State("etft-key", "data"),
)
def _load(_refresh, key):
    df = repo.all_targets()
    if df.empty:
        return [], []
    rows = []
    for r in df.to_dict("records"):
        _, info = repo.members(r["target"])
        rows.append({**r, "holdings": info.get("holdings"),
                     "priced": info.get("priced"),
                     "listed": round(info.get("listed") or 0, 2),
                     "priced_weight": round(info.get("priced_weight") or 0,
                                            2),
                     "holdings_date": info.get("holdings_date") or ""})
    keys = [r["target"] for r in rows]
    return rows, ([keys.index(key)] if key in keys else [])


@callback(
    Output("etft-feedback", "children"),
    Output("etft-refresh", "data", allow_duplicate=True),
    Input("etft-save", "n_clicks"),
    State("etft-table", "data"), State("etft-refresh", "data"),
    prevent_initial_call=True,
)
def _save(_n, rows, refresh):
    try:
        n = repo.save_targets(rows)
    except Exception as exc:                                   # noqa: BLE001
        return _msg(f"Save failed: {exc}", False), no_update
    return _msg(f"Saved {n} targets."), (refresh or 0) + 1


@callback(
    Output("etft-key", "data"),
    Input("etft-table", "selected_rows"),
    State("etft-table", "data"),
    State("etft-key", "data"),
    prevent_initial_call=True,
)
def _select(selected, rows, current):
    if not selected or not rows or selected[0] >= len(rows):
        return no_update
    key = rows[selected[0]]["target"]
    return no_update if key == current else key


# --- Selected fund ------------------------------------------------------------------

@callback(
    Output("etft-detail-wrap", "style"),
    Output("etft-detail-head", "children"),
    Output("etft-holdings", "data"),
    Output("etft-basket-wrap", "style"),
    Output("etft-basket", "data"),
    Input("etft-key", "data"),
    Input("etft-refresh", "data"),
)
def _detail(key, _refresh):
    if not key:
        return {"display": "none"}, None, [], {"display": "none"}, []
    row = repo.target(key)
    if row is None:
        return {"display": "none"}, None, [], {"display": "none"}, []
    members, info = repo.members(key)
    missing = members[members["status"].isin(
        [repo.STATUS_MISSING, repo.STATUS_NO_PRICES])] \
        if not members.empty else members
    unmapped = members[members["status"] == repo.STATUS_UNMAPPED] \
        if not members.empty else members

    line = (f"{info.get('holdings', 0)} holdings, "
            f"{info.get('listed', 0):.1f}% of the fund on file · "
            f"{info.get('priced', 0)} priced "
            f"({info.get('priced_weight', 0):.1f}%) · "
            f"{len(missing)} to add or fetch · "
            f"{len(unmapped)} with no Yahoo symbol")
    if info.get("holdings_date"):
        line += f" · holdings as of {info['holdings_date']}"
    head = html.Div([
        html.Div(row["label"], style={"fontSize": "15px", "fontWeight": 700,
                                      "color": theme.INK}),
        html.Div(f"{key} · {row['kind']}", style={
            "fontSize": "11px", "color": theme.NEUTRAL,
            "fontFamily": "DM Mono, ui-monospace, monospace"}),
        html.Div(line, style={"fontSize": "12px", "color": theme.SLATE,
                              "marginTop": "4px"}),
    ])

    # Problems first, then the priced holdings by weight.
    order = {repo.STATUS_FUND: 0, repo.STATUS_MISSING: 1,
             repo.STATUS_NO_PRICES: 2, repo.STATUS_UNMAPPED: 3,
             repo.STATUS_OK: 4}
    hold = members.assign(_o=members["status"].map(order),
                          _w=-members["weight_pct"].fillna(0)) \
        .sort_values(["_o", "_w"]) if not members.empty else members
    hold_rows = [{"name": r["name"], "fund_id": r["fund_id"] or "—",
                  "ticker": r["ticker"] or "", "weight_pct": r["weight_pct"],
                  "status": r["status"]}
                 for r in hold.to_dict("records")]

    if row["kind"] == "basket":
        b = repo.basket(key)
        brows = [{"fund_id": f[3:] if f.startswith("YF:") else f,
                  "weight": w} for f, w in zip(b["fund_id"], b["weight"])]
        return {"display": "block"}, head, hold_rows, \
            {"display": "block"}, brows
    return {"display": "block"}, head, hold_rows, {"display": "none"}, []


@callback(
    Output("etft-basket", "data", allow_duplicate=True),
    Input("etft-basket-row", "n_clicks"),
    State("etft-basket", "data"),
    prevent_initial_call=True,
)
def _basket_row(_n, rows):
    return list(rows or []) + [{"fund_id": "", "weight": None}]


@callback(
    Output("etft-basket-msg", "children"),
    Output("etft-refresh", "data", allow_duplicate=True),
    Input("etft-basket-save", "n_clicks"),
    State("etft-key", "data"), State("etft-basket", "data"),
    State("etft-refresh", "data"),
    prevent_initial_call=True,
)
def _basket_save(_n, key, rows, refresh):
    if not key:
        return "Select a basket first.", no_update
    _, message = repo.save_basket(key, rows)
    return message, (refresh or 0) + 1


# --- Add missing instruments (background job) -----------------------------------------

@callback(
    Output("etft-job", "data"),
    Output("etft-poll", "disabled"),
    Output("etft-run-msg", "children"),
    Input("etft-run", "n_clicks"),
    State("etft-key", "data"), State("etft-minw", "value"),
    prevent_initial_call=True,
)
def _run(_n, key, min_weight):
    if not key:
        return no_update, no_update, "Select a fund first."
    running = background.busy(JOB)
    if running:
        return {"log": running}, False, "A run is already going - " \
                                        "following it below."
    try:
        minw = max(0.0, float(min_weight if min_weight is not None else 0.1))
    except (TypeError, ValueError):
        minw = 0.1
    log = background.start(
        ["scripts/add_constituents.py", "--apply", "--activate",
         "--history", "--target", key, "--min-weight", f"{minw:g}"], JOB)
    return {"log": log, "key": key}, False, f"Started for {key}."


@callback(
    Output("etft-log", "children"),
    Output("etft-log", "style"),
    Output("etft-poll", "disabled", allow_duplicate=True),
    Output("etft-run-msg", "children", allow_duplicate=True),
    Output("etft-refresh", "data", allow_duplicate=True),
    Input("etft-poll", "n_intervals"),
    Input("etft-job", "data"),
    State("etft-log", "style"), State("etft-refresh", "data"),
    prevent_initial_call="initial_duplicate",
)
def _poll(_n, job, style, refresh):
    style = dict(style or {})
    if not job or not job.get("log"):
        return no_update, no_update, True, no_update, no_update
    s = background.status(job["log"], tail=30)
    style["display"] = "block"
    who = f"{job['key']}: " if job.get("key") else ""
    if not s["finished"]:
        return s["text"], style, False, f"{who}running…", no_update
    if s["stalled"]:
        msg = "the log stopped changing - the run may have been killed."
    else:
        msg = "finished." if s["ok"] else "finished with errors - see log."
    msg = f"{who}{msg}" if who else msg[0].upper() + msg[1:]
    # One refresh at the end so the coverage numbers update.
    return s["text"], style, True, msg, (refresh or 0) + 1


def _msg(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEEDLE
    return html.Div(text, style={
        "padding": "9px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour,
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})
