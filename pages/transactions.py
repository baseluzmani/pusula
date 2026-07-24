"""
Transactions tab - the Markets landing page.

Left column: a price chart for the selected instrument with buy (up) / sell
(down) markers, above a monthly-returns heatmap. Right column: a
period-returns table (1D..Since) grouped by asset type; clicking a row
selects that instrument for the chart.

The fund universe comes from the shared selector (ui/universe.py), so the
table lists My holdings / All instruments / a hand-picked set. Instruments
with no transactions simply draw a clean price line - no markers - which is
the agreed behaviour for showing everything, not only traded names.
"""

from datetime import datetime

import dash
from dash import html, dcc, callback, Input, Output, State, ctx, ALL, no_update
import pandas as pd
import plotly.graph_objects as go

from core import theme
from core.repo import market as repo
from ui import universe

RETURN_COLS = ["1D", "1W", "1M", "3M", "YTD", "Since"]
DEFAULT_SINCE = "2026-03-01"


def render():
    """Tab body. The universe store/selector lives above the tabs in the
    section shell, so this returns only the tab's own content."""
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Transactions", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Click an instrument to see its price with buys and "
                         "sells, and its monthly returns.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Label("Since", style={"fontSize": "11px",
                           "color": theme.SLATE, "marginRight": "8px"}),
                dcc.DatePickerSingle(id="tx-since", date=DEFAULT_SINCE,
                                     display_format="DD MMM YYYY"),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "14px"}),

        html.Div([
            # LEFT: chart + monthly heatmap
            html.Div([
                html.Div([
                    html.Div([
                        html.Span("Price", style=theme.CARD_TITLE),
                        html.Span(id="tx-chart-info", style={"fontSize": "11px",
                                  "color": theme.NEUTRAL, "marginLeft": "8px"}),
                    ], style={"display": "flex", "alignItems": "baseline",
                              "justifyContent": "space-between"}),
                    html.Div([
                        html.Label("From", style={"fontSize": "11px",
                                   "color": theme.SLATE, "marginRight": "8px"}),
                        dcc.DatePickerSingle(id="tx-chart-from", date=None,
                                             display_format="DD MMM YYYY"),
                    ], style={"display": "flex", "alignItems": "center",
                              "margin": "6px 0 8px"}),
                    dcc.Graph(id="tx-price", config={"displayModeBar": False},
                              style={"height": "320px"}),
                ], style={**theme.CARD, "marginBottom": "12px"}),

                html.Div([
                    html.Span("Monthly returns", style=theme.CARD_TITLE),
                    dcc.Graph(id="tx-monthly", config={"displayModeBar": False},
                              style={"height": "340px"}),
                ], style=theme.CARD),
            ], style={"flex": "1", "minWidth": 0, "marginRight": "12px"}),

            # RIGHT: returns table
            html.Div(html.Div(id="tx-table"),
                     style={**theme.CARD, "flex": "1", "minWidth": 0,
                            "overflow": "auto",
                            "maxHeight": "calc(100vh - 200px)"}),
        ], style={"display": "flex", "alignItems": "flex-start",
                  "width": "100%", "overflow": "hidden"}),

        dcc.Store(id="tx-selected", data=None),
        dcc.Store(id="tx-sort", data={"col": "YTD", "asc": False}),
    ])


# --- returns table -------------------------------------------------------

@callback(
    Output("tx-table", "children"),
    Output("tx-sort", "data"),
    Input(universe.STORE_ID, "data"),
    Input("tx-since", "date"),
    Input({"type": "tx-sort", "col": ALL}, "n_clicks"),
    Input("tx-selected", "data"),
    State("tx-sort", "data"),
)
def _table(store, since, _sorts, selected, sort):
    since = since or DEFAULT_SINCE
    trig = ctx.triggered_id
    if isinstance(trig, dict) and trig.get("type") == "tx-sort":
        col = trig["col"]
        sort = ({"col": col, "asc": not sort["asc"]}
                if sort["col"] == col else {"col": col, "asc": False})

    ids = universe.resolve_ids(store)
    if not ids:
        return _empty("No instruments in this universe."), sort

    px = repo.prices(ids)
    if px.empty:
        return _empty("No price data."), sort

    table = repo.period_returns(price_df=px, since_date=since)
    if table.empty:
        return _empty("No returns to show."), sort

    if sort["col"] in table.columns:
        table = table.sort_values(sort["col"], ascending=sort["asc"],
                                  na_position="last")

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
    cols += [(f"{c}%" if c not in ("YTD",) else "YTD",
              c, "right") for c in RETURN_COLS[:-1]]
    cols += [(since_label, "Since", "right")]

    head = html.Thead(html.Tr([
        html.Th(f"{label}{arrow(key)}" if align == "right" or key in
                ("Fund", "Type", "Price") else label,
                id={"type": "tx-sort", "col": key}, n_clicks=0,
                style={"background": theme.INK, "color": "#fff",
                       "padding": "5px 7px", "fontSize": "10px",
                       "fontWeight": 600, "textAlign": align,
                       "whiteSpace": "nowrap", "cursor": "pointer",
                       "userSelect": "none"})
        for label, key, align in cols]))

    body = []
    # Iterate as dicts: return columns like "1D" aren't valid attribute names
    # on itertuples' namedtuples, so getattr(row, "1D") would fail.
    for r in df.to_dict("records"):
        fid = r["fund_id"]
        picked = fid == selected
        cells = [
            html.Td(html.Span(_short(r["Fund"]), title=str(r["Fund"])),
                    style={"padding": "3px 7px", "fontSize": "11px",
                           "fontWeight": 600 if picked else 400,
                           "color": theme.INK, "whiteSpace": "nowrap",
                           "maxWidth": "190px", "overflow": "hidden",
                           "textOverflow": "ellipsis"}),
            html.Td(r["Type"], style={"padding": "3px 5px", "fontSize": "9px",
                    "textAlign": "center", "color": theme.SLATE}),
            html.Td(f"{r['Price']:.1f}" if pd.notna(r["Price"]) else "\u2014",
                    className="num", style={"padding": "3px 7px",
                    "fontSize": "11px", "textAlign": "right", **theme.NUM,
                    "color": theme.TEXT}),
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
        body.append(html.Tr(cells, id={"type": "tx-row", "fund_id": fid},
                            n_clicks=0, style={
                                "cursor": "pointer",
                                "background": "#EEF4FB" if picked else "transparent",
                                "borderBottom": f"1px solid {theme.LINE}"}))

    return html.Table([head, html.Tbody(body)],
                      style={"width": "100%", "borderCollapse": "collapse"})


@callback(
    Output("tx-selected", "data"),
    Input({"type": "tx-row", "fund_id": ALL}, "n_clicks"),
    State("tx-selected", "data"),
    prevent_initial_call=True,
)
def _select(clicks, current):
    if not any(clicks or []):
        return no_update
    trig = ctx.triggered_id
    if not trig:
        return no_update
    fid = trig["fund_id"]
    return None if fid == current else fid


# --- price chart ---------------------------------------------------------

@callback(
    Output("tx-price", "figure"),
    Output("tx-chart-info", "children"),
    Output("tx-chart-from", "date"),
    Input("tx-selected", "data"),
    Input("tx-chart-from", "date"),
)
def _chart(fid, start):
    if not fid:
        return _blank("Select an instrument to see its price and transactions"), "", start

    inst = repo.instruments([fid])
    name = inst["name"].iloc[0] if not inst.empty else fid
    px = repo.prices([fid])
    if px.empty:
        return _blank("No price data"), name, start

    p = px[px["fund_id"] == fid].sort_values("date")
    if start is None:
        start = p["date"].min().strftime("%Y-%m-%d")
    start_dt = pd.Timestamp(start)
    p = p[p["date"] >= start_dt]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=p["date"], y=p["close"], mode="lines", name=name,
                             line=dict(color=theme.INK, width=2),
                             hovertemplate="%{x|%d %b %Y}: %{y:.2f}"
                                           f"<extra>{name}</extra>"))

    tx = repo.transactions(fid)
    buys = _markers(tx, "BUY", p, start_dt)
    sells = _markers(tx, "SELL", p, start_dt)
    if buys:
        fig.add_trace(go.Scatter(x=buys[0], y=buys[1], mode="markers", name="Buy",
                      marker=dict(color=theme.POSITIVE, size=11,
                                  symbol="triangle-up",
                                  line=dict(width=1, color="white")),
                      text=buys[2],
                      hovertemplate="%{text}<br>%{x|%d %b %Y}<extra></extra>"))
    if sells:
        fig.add_trace(go.Scatter(x=sells[0], y=sells[1], mode="markers", name="Sell",
                      marker=dict(color=theme.NEGATIVE, size=11,
                                  symbol="triangle-down",
                                  line=dict(width=1, color="white")),
                      text=sells[2],
                      hovertemplate="%{text}<br>%{x|%d %b %Y}<extra></extra>"))

    fig.update_layout(hovermode="x unified", height=310,
                      margin=dict(l=42, r=18, t=8, b=60),
                      plot_bgcolor="white", paper_bgcolor="white",
                      legend=dict(orientation="h", y=-0.2, x=0,
                                  font=dict(size=10)))
    fig.update_xaxes(showgrid=True, gridcolor="#F0F2F5", tickfont=dict(size=10))
    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5", tickfont=dict(size=10))

    nb = int((tx["type"] == "BUY").sum()) if not tx.empty else 0
    ns = int((tx["type"] == "SELL").sum()) if not tx.empty else 0
    info = f"{name} \u2014 {nb} buys, {ns} sells" if (nb or ns) else name
    return fig, info, start


def _markers(tx, side, prices_df, start_dt):
    if tx.empty:
        return None
    dates, values, texts = [], [], []
    for t in tx.itertuples():
        if t.type != side or t.trade_date < start_dt:
            continue
        after = prices_df[prices_df["date"] >= t.trade_date]
        if after.empty:
            continue
        dates.append(after.iloc[0]["date"])
        values.append(after.iloc[0]["close"])
        texts.append(f"{side} {t.quantity:g} @ {t.price:.2f}")
    return (dates, values, texts) if dates else None


# --- monthly heatmap -----------------------------------------------------

@callback(Output("tx-monthly", "figure"), Input("tx-selected", "data"))
def _monthly(fid):
    if not fid:
        return _blank("Select an instrument to see monthly returns")
    piv = repo.monthly_returns(fid)
    if piv.empty:
        return _blank("Not enough data for monthly returns")

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Year"]
    z, text = [], []
    for year in piv.index:
        row, trow = [], []
        for mnum in list(range(1, 13)) + ["Year"]:
            val = piv.loc[year, mnum] if mnum in piv.columns else None
            val = None if (val is None or pd.isna(val)) else float(val)
            row.append(val)
            trow.append(f"{val:+.1f}" if val is not None else "")
        z.append(row)
        text.append(trow)

    cap = 12
    colorscale = [[0.0, "#C0392B"], [0.5, "#F7F7F7"], [1.0, "#1A7A4C"]]
    fig = go.Figure(go.Heatmap(
        z=z, x=months, y=[str(y) for y in piv.index],
        zmin=-cap, zmax=cap, colorscale=colorscale, showscale=False,
        text=text, texttemplate="%{text}",
        textfont=dict(size=9, family="DM Mono, monospace"),
        hovertemplate="%{y} %{x}: %{z:+.1f}%<extra></extra>", xgap=2, ygap=2))
    fig.update_layout(
        height=max(300, 70 + len(piv) * 30),
        margin=dict(l=48, r=10, t=26, b=8),
        plot_bgcolor="white", paper_bgcolor="white",
        xaxis=dict(side="top", tickfont=dict(size=10)),
        yaxis=dict(tickfont=dict(size=11), autorange="reversed",
                   dtick=1, tickmode="linear"))
    return fig


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
    fig.update_layout(height=300, plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=40, r=40, t=10, b=40))
    return fig
