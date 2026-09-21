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
from core.repo import settings
from ui import universe

RETURN_COLS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "Since"]
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
                                     date=settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01"),
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
                html.Div(id="cmp-sel-table"),
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
    since = since or settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01")
    trig = ctx.triggered_id
    if isinstance(trig, dict) and trig.get("type") == "cmp-sort":
        col = trig["col"]
        sort = ({"col": col, "asc": not sort["asc"]}
                if sort["col"] == col else {"col": col, "asc": False})

    if not universe.is_chosen(store):
        return _empty(universe.PROMPT), sort
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
            html.Td(html.Div(html.Span(_short(r["Fund"]), title=str(r["Fund"]))),
                    style={"padding": "3px 7px", "fontSize": "11px",
                           "color": theme.INK, "whiteSpace": "nowrap",
                           "maxWidth": "190px", "overflow": "hidden",
                           "textOverflow": "ellipsis"}),
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
        # Highlight is the CSS class "cmp-picked" (assets/pusula.css), so a
        # tick can flip it in place instead of rebuilding the whole table.
        body.append(html.Tr(cells, id={"type": "cmp-row", "fund_id": fid},
                            n_clicks=0,
                            className="cmp-picked" if picked else "",
                            style={"cursor": "pointer",
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

# Highlight ticked rows in place. Only the row classNames travel over the
# wire (a few KB), instead of the whole rebuilt table (~600 KB). Dash also runs
# this once whenever a rebuilt table brings in new rows, which keeps the
# highlight right even if you tick while the table is still loading.
@callback(
    Output({"type": "cmp-row", "fund_id": ALL}, "className"),
    Input("cmp-selected", "data"),
)
def _mark(selected):
    picked = set(selected or [])
    return ["cmp-picked" if o["id"]["fund_id"] in picked else ""
            for o in ctx.outputs_list]

# When the universe changes, drop any plotted lines that are not in the new
# universe. Nothing is auto-selected: the chart stays empty until you tick rows.
@callback(
    Output("cmp-selected", "data", allow_duplicate=True),
    Input(universe.STORE_ID, "data"),
    State("cmp-selected", "data"),
    prevent_initial_call=True,
)
def _prune(store, selected):
    selected = selected or []
    if not selected:
        return no_update
    keep = set(universe.resolve_ids(store))
    kept = [f for f in selected if f in keep]
    return kept if len(kept) != len(selected) else no_update


# --- chart ---------------------------------------------------------------

@callback(
    Output("cmp-chart", "figure"),
    Output("cmp-info", "children"),
    Input("cmp-selected", "data"),
    Input("cmp-since", "date"),
)
def _chart(selected, since):
    selected = selected or []
    since = since or settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01")

    if not selected:
        return _blank("Tick instruments in the table to plot them"), "none selected"

    ranked = _ranked(selected, since)
    if ranked.empty:
        return _blank("No price data for the selected instruments"), "none plotted"

    plot = ranked["fund_id"].tolist()
    # Same price window as the returns table, so the chart rebases on exactly
    # the same base price the table's Since column uses.
    px = repo.prices(plot, min_date=repo.window_start(since))
    names = ranked.set_index("fund_id")["Fund"].to_dict()
    start = pd.Timestamp(since)

    fig = go.Figure()
    for i, fid in enumerate(plot):
        g = px[px["fund_id"] == fid].sort_values("date")
        if g.empty:
            continue
        # Base = last close on or before Since (Since may be a weekend or
        # holiday). A fund that starts after Since rebases on its first close.
        before = g[g["date"] <= start]
        base_row = before.iloc[-1] if not before.empty else g.iloc[0]
        base = base_row["close"]
        if not base:
            continue
        line = g[g["date"] >= base_row["date"]].copy()
        if len(line) < 2:
            continue
        line["ret"] = ((line["close"] / base - 1) * 100).round(2)
        name = names.get(fid, fid)
        fig.add_trace(go.Scatter(
            x=line["date"], y=line["ret"], mode="lines", name=_short(name, 22),
            line=dict(width=2.2, color=LINE_COLOURS[i % len(LINE_COLOURS)]),
            hovertemplate="%{x|%d %b %Y}: %{y:+.1f}%<extra>" + name + "</extra>"))

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


# --- returns of the plotted lines -----------------------------------------

def _ranked(selected, since):
    """
    Returns rows for the plotted funds, best Since-return first.

    The one ordering used by the chart (line colours, legend) and by the table
    under it, so a colour swatch in the table always matches its line.
    """
    plot = list(selected or [])[:MAX_LINES]
    if not plot:
        return pd.DataFrame()
    table = repo.period_returns(fund_ids=plot, since_date=since)
    if table.empty:
        return table
    return (table.sort_values("Since", ascending=False, na_position="last")
                 .reset_index(drop=True))


@callback(
    Output("cmp-sel-table", "children"),
    Input("cmp-selected", "data"),
    Input("cmp-since", "date"),
)
def _selected_table(selected, since):
    since = since or settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01")
    ranked = _ranked(selected, since)
    if ranked.empty:
        return None

    since_label = pd.Timestamp(since).strftime("%d %b %y")
    head = html.Thead(html.Tr([
        html.Th(label, style={
            "background": theme.INK, "color": "#fff", "padding": "5px 7px",
            "fontSize": "10px", "fontWeight": 600, "whiteSpace": "nowrap",
            "textAlign": "left" if i == 0 else "right"})
        for i, label in enumerate(["Fund"] + RETURN_COLS[:-1] + [since_label])]))

    body = []
    for i, r in enumerate(ranked.to_dict("records")):
        swatch = LINE_COLOURS[i % len(LINE_COLOURS)]
        cells = [html.Td(html.Div([
            html.Span("\u25CF ", style={"color": swatch, "fontSize": "11px"}),
            html.Span(_short(r["Fund"], 34), title=str(r["Fund"])),
        ]), style={"padding": "3px 7px", "fontSize": "11px",
                   "color": theme.INK, "whiteSpace": "nowrap"})]
        cells += [_ret_cell(r[col]) for col in RETURN_COLS]
        body.append(html.Tr(cells, style={
            "borderBottom": f"1px solid {theme.LINE}"}))

    return html.Div([
        html.Div("Returns of plotted lines", style={**theme.CARD_TITLE,
                 "marginBottom": "6px"}),
        html.Table([head, html.Tbody(body)],
                   style={"width": "100%", "borderCollapse": "collapse"}),
    ], style={"marginTop": "10px"})


def _ret_cell(val):
    has = pd.notna(val)
    return html.Td(
        f"{val:+.1f}%" if has else "\u2014",
        style={"padding": "3px 5px", "fontSize": "10px", "textAlign": "right",
               "fontWeight": 600, **theme.NUM,
               "background": theme.heat_rgb(val if has else None),
               "color": theme.INK})


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
