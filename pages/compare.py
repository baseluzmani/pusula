"""
Compare tab.

Absorbs the old My Holdings and Market Overview pages: one rebased
multi-line chart plus a period-returns table, driven by the shared universe
selector. Multi-select in the table adds/removes lines on the chart. Lines
are rebased to the "Since" date so instruments are compared on a common
zero, regardless of absolute price.

The old Market Overview multi-select was slow and flaky because two
callbacks wrote the same store and prices were reloaded (whole table) per
click. Here the universe store has a single writer (ui/universe.py), the
selection lives in its own store, and prices for the universe are fetched
once per render and filtered in memory.
"""

import dash
from dash import html, dcc, callback, Input, Output, State, ctx, ALL, no_update
import pandas as pd
import plotly.graph_objects as go

from core import theme, config
from core.repo import market as repo
from ui import universe

RETURN_COLS = ["1D", "1W", "1M", "3M", "YTD", "Since"]
LINE_COLOURS = ["#2E6FB5", "#B8860F", "#1A7A4C", "#C0392B", "#7D5BA6",
                "#137E9E", "#D06018", "#4A6FA5"]
MAX_LINES = 8


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Compare", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Rebased returns from the Since date. Tick rows to "
                         "add or remove lines.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Label("Since", style={"fontSize": "11px",
                           "color": theme.SLATE, "marginRight": "8px"}),
                dcc.DatePickerSingle(id="cmp-since",
                                     date=config.MARKETS_SINCE_DEFAULT,
                                     display_format="DD MMM YYYY"),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "14px"}),

        html.Div([
            html.Div([
                html.Div([
                    html.Span("Rebased returns", style=theme.CARD_TITLE),
                    html.Span(id="cmp-info", style={"fontSize": "11px",
                              "color": theme.NEUTRAL, "marginLeft": "8px"}),
                ], style={"display": "flex", "alignItems": "baseline"}),
                dcc.Graph(id="cmp-chart", config={"displayModeBar": False},
                          style={"height": "440px"}),
            ], style={**theme.CARD, "flex": "1", "minWidth": 0,
                      "marginRight": "12px"}),

            html.Div(html.Div(id="cmp-table"),
                     style={**theme.CARD, "flex": "1", "minWidth": 0,
                            "overflow": "auto",
                            "maxHeight": "calc(100vh - 200px)"}),
        ], style={"display": "flex", "alignItems": "flex-start",
                  "width": "100%", "overflow": "hidden"}),

        # Selected funds for the chart. Distinct from the universe store:
        # universe = which rows exist; selection = which are plotted.
        dcc.Store(id="cmp-selected", data=[]),
        dcc.Store(id="cmp-sort", data={"col": "YTD", "asc": False}),
    ])


# --- table ---------------------------------------------------------------

@callback(
    Output("cmp-table", "children"),
    Output("cmp-sort", "data"),
    Input(universe.STORE_ID, "data"),
    Input("cmp-since", "date"),
    Input({"type": "cmp-sort", "col": ALL}, "n_clicks"),
    Input("cmp-selected", "data"),
    State("cmp-sort", "data"),
)
def _table(store, since, _sorts, selected, sort):
    since = since or config.MARKETS_SINCE_DEFAULT
    trig = ctx.triggered_id
    if isinstance(trig, dict) and trig.get("type") == "cmp-sort":
        col = trig["col"]
        sort = ({"col": col, "asc": not sort["asc"]}
                if sort["col"] == col else {"col": col, "asc": False})

    ids = universe.resolve_ids(store)
    if not ids:
        return _empty("No instruments in this universe."), sort

    table = repo.period_returns(fund_ids=ids, since_date=since)
    if table.empty:
        return _empty("No returns to show."), sort
    if sort["col"] in table.columns:
        table = table.sort_values(sort["col"], ascending=sort["asc"],
                                  na_position="last")

    selected = selected or []
    since_label = pd.Timestamp(since).strftime("%d %b %y")
    sections = []
    for atype, grp in table.groupby("Type", sort=False):
        sections.append(html.Div([
            html.Div(str(atype).upper(), style={
                "fontSize": "9px", "fontWeight": 700, "letterSpacing": "0.06em",
                "color": theme.SLATE, "borderBottom": f"1px solid {theme.LINE}",
                "padding": "8px 0 3px", "marginTop": "6px" if sections else 0}),
            _group_table(grp, since_label, sort, selected),
        ]))
    return html.Div(sections), sort


def _group_table(df, since_label, sort, selected):
    def arrow(col):
        if sort["col"] == col:
            return " \u25B2" if sort["asc"] else " \u25BC"
        return " \u21C5"

    cols = [("Fund", "Fund", "left"), ("Type", "Type", "center"),
            ("Price", "Price", "right")]
    cols += [(c, c, "right") for c in RETURN_COLS[:-1]]
    cols += [(since_label, "Since", "right")]

    head = html.Thead(html.Tr([
        html.Th(f"{label}{arrow(key)}",
                id={"type": "cmp-sort", "col": key}, n_clicks=0,
                style={"background": theme.INK, "color": "#fff",
                       "padding": "5px 7px", "fontSize": "10px",
                       "fontWeight": 600, "textAlign": align,
                       "whiteSpace": "nowrap", "cursor": "pointer",
                       "userSelect": "none"})
        for label, key, align in cols]))

    body = []
    for r in df.to_dict("records"):
        fid = r["fund_id"]
        picked = fid in selected
        cells = [
            html.Td(html.Div([
                html.Span("\u25CF " if picked else "",
                          style={"color": theme.NEEDLE, "fontSize": "8px"}),
                html.Span(_short(r["Fund"]), title=str(r["Fund"])),
            ]), style={"padding": "3px 7px", "fontSize": "11px",
                       "fontWeight": 600 if picked else 400, "color": theme.INK,
                       "whiteSpace": "nowrap", "maxWidth": "190px",
                       "overflow": "hidden", "textOverflow": "ellipsis"}),
            html.Td(r["Type"], style={"padding": "3px 5px", "fontSize": "9px",
                    "textAlign": "center", "color": theme.SLATE}),
            html.Td(f"{r['Price']:.1f}" if pd.notna(r["Price"]) else "\u2014",
                    style={"padding": "3px 7px", "fontSize": "11px",
                           "textAlign": "right", **theme.NUM, "color": theme.TEXT}),
        ]
        for col in RETURN_COLS:
            val = r[col]
            has = pd.notna(val)
            cells.append(html.Td(
                f"{val:+.1f}%" if has else "\u2014",
                style={"padding": "3px 5px", "fontSize": "10px",
                       "textAlign": "right", "fontWeight": 600, **theme.NUM,
                       "background": theme.heat_rgb(val if has else None),
                       "color": theme.INK}))
        body.append(html.Tr(cells, id={"type": "cmp-row", "fund_id": fid},
                            n_clicks=0, style={
                                "cursor": "pointer",
                                "background": "#EEF4FB" if picked else "transparent",
                                "borderBottom": f"1px solid {theme.LINE}"}))

    return html.Table([head, html.Tbody(body)],
                      style={"width": "100%", "borderCollapse": "collapse"})


@callback(
    Output("cmp-selected", "data"),
    Input({"type": "cmp-row", "fund_id": ALL}, "n_clicks"),
    State("cmp-selected", "data"),
    prevent_initial_call=True,
)
def _toggle(clicks, selected):
    if not any(clicks or []):
        return no_update
    trig = ctx.triggered_id
    if not trig:
        return no_update
    fid = trig["fund_id"]
    selected = list(selected or [])
    if fid in selected:
        selected.remove(fid)
    else:
        selected.append(fid)
    return selected


# Reset the plotted selection when the universe changes, so lines from a
# previous universe don't linger. Seeds with the top-4 by YTD as a useful
# default (mirrors the old pages' behaviour).
@callback(
    Output("cmp-selected", "data", allow_duplicate=True),
    Input(universe.STORE_ID, "data"),
    State("cmp-since", "date"),
    prevent_initial_call=True,
)
def _seed(store, since):
    ids = universe.resolve_ids(store)
    if not ids:
        return []
    table = repo.period_returns(fund_ids=ids,
                                since_date=since or config.MARKETS_SINCE_DEFAULT)
    if table.empty:
        return []
    return table.sort_values("YTD", ascending=False,
                             na_position="last")["fund_id"].head(4).tolist()


# --- chart ---------------------------------------------------------------

@callback(
    Output("cmp-chart", "figure"),
    Output("cmp-info", "children"),
    Input("cmp-selected", "data"),
    Input("cmp-since", "date"),
    State(universe.STORE_ID, "data"),
)
def _chart(selected, since, store):
    selected = selected or []
    since = since or config.MARKETS_SINCE_DEFAULT

    if not selected:
        return _blank("Tick instruments in the table to plot them"), "none selected"

    plot = selected[:MAX_LINES]
    px = repo.prices(plot, min_date=since)
    names = repo.instruments(plot).set_index("fund_id")["name"].to_dict()
    start = pd.Timestamp(since)

    # Order legend by performance, best first.
    order = []
    for fid in plot:
        g = px[px["fund_id"] == fid]
        r = repo._pct_at_or_before(
            g.set_index("date")["close"], start, g["close"].iloc[-1]) if not g.empty else None
        order.append((fid, r if r is not None else -1e9))
    order.sort(key=lambda x: x[1], reverse=True)

    fig = go.Figure()
    for i, (fid, _) in enumerate(order):
        g = px[px["fund_id"] == fid].sort_values("date")
        base_rows = g[g["date"] <= start]
        base = base_rows.iloc[-1]["close"] if not base_rows.empty else (
            g.iloc[0]["close"] if not g.empty else None)
        if not base:
            continue
        line = g[g["date"] >= start].copy()
        if len(line) < 2:
            continue
        line["ret"] = (line["close"] / base - 1) * 100
        name = names.get(fid, fid)
        fig.add_trace(go.Scatter(
            x=line["date"], y=line["ret"], mode="lines", name=_short(name, 22),
            line=dict(width=2.2, color=LINE_COLOURS[i % len(LINE_COLOURS)]),
            hovertemplate="%{x|%d %b %Y}: %{y:+.1f}%%<extra>" + name + "</extra>"))

    fig.update_layout(height=430, hovermode="x unified",
                      margin=dict(l=44, r=20, t=8, b=70),
                      plot_bgcolor="white", paper_bgcolor="white",
                      yaxis_ticksuffix="%",
                      legend=dict(orientation="h", y=-0.18, x=0,
                                  font=dict(size=10)))
    fig.update_xaxes(showgrid=True, gridcolor="#F0F2F5", tickfont=dict(size=10))
    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5", zeroline=True,
                     zerolinecolor="#CBD2DA", tickfont=dict(size=10))

    n = len(selected)
    info = f"{n} selected" + (f", showing first {MAX_LINES}" if n > MAX_LINES else "")
    return fig, info


# --- helpers -------------------------------------------------------------

def _short(name, n=26):
    s = str(name)
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def _empty(msg):
    return html.Div(msg, style={"color": theme.NEUTRAL, "fontSize": "12px",
                                "padding": "18px"})


def _blank(msg):
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=12, color=theme.NEUTRAL))
    fig.update_layout(height=430, plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=40, r=40, t=10, b=40))
    return fig
