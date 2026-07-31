"""
Heatmap tab.

Merges the old Markets Heat Map and Holdings Heat Map into one page: the
shared universe selector chooses the instrument set (so "Holdings Heat Map"
is just this page with the My-holdings radio), and a sort dropdown reorders
rows by any period. A Group-by-Type toggle sections the grid by asset_type.

Fast because it renders straight from repo.period_returns (one grouped pass)
rather than the original per-cell recompute that made both pages crawl.
"""

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd

from core import theme, config
from core.repo import market as repo
from core.repo import settings
from ui import universe

# Columns shown in the grid, in order.
HEAT_COLS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "Since"]


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Heatmap", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Returns across periods for the selected universe. "
                         "Green is up, red is down.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Div([
                    html.Label("Sort by", style={"fontSize": "11px",
                               "color": theme.SLATE, "marginRight": "6px"}),
                    dcc.Dropdown(id="hm-sort", clearable=False,
                                 value="YTD", style={"width": "110px"},
                                 options=[{"label": c, "value": c}
                                          for c in HEAT_COLS]),
                ], style={"display": "flex", "alignItems": "center",
                          "marginRight": "18px"}),
                dcc.Checklist(id="hm-group",
                              options=[{"label": " Group by type", "value": "on"}],
                              value=["on"],
                              inputStyle={"marginRight": "6px"},
                              labelStyle={"fontSize": "12px",
                                          "color": theme.TEXT}),
                html.Div([
                    html.Label("Since", style={"fontSize": "11px",
                               "color": theme.SLATE, "margin": "0 6px 0 18px"}),
                    dcc.DatePickerSingle(id="hm-since",
                                         date=settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01"),
                                         display_format="DD MMM YYYY"),
                ], style={"display": "flex", "alignItems": "center"}),
            ], style={"display": "flex", "alignItems": "center",
                      "flexWrap": "wrap"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "14px"}),

        html.Div(html.Div(id="hm-grid"),
                 style={**theme.CARD, "overflow": "auto",
                        "maxHeight": "calc(100vh - 190px)"}),
    ])


@callback(
    Output("hm-grid", "children"),
    Input(universe.STORE_ID, "data"),
    Input("hm-sort", "value"),
    Input("hm-group", "value"),
    Input("hm-since", "date"),
)
def _grid(store, sort_col, group, since):
    since = since or settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01")
    sort_col = sort_col if sort_col in HEAT_COLS else "YTD"
    grouped = "on" in (group or [])

    ids = universe.resolve_ids(store)
    if not ids:
        return _empty("No instruments in this universe.")

    table = repo.period_returns(fund_ids=ids, since_date=since)
    if table.empty:
        return _empty("No returns to show.")

    since_label = pd.Timestamp(since).strftime("%d %b %y")
    header = _header_row(since_label, sort_col)

    if grouped:
        blocks = []
        # Order type groups by their best value in the sort column, so the
        # strongest sector floats to the top too.
        order = (table.groupby("Type")[sort_col].max()
                 .sort_values(ascending=False).index.tolist())
        for atype in order:
            grp = table[table["Type"] == atype]
            grp = grp.sort_values(sort_col, ascending=False, na_position="last")
            blocks.append(html.Div([
                html.Div(str(atype).upper(), style={
                    "fontSize": "9px", "fontWeight": 700,
                    "letterSpacing": "0.06em", "color": theme.SLATE,
                    "padding": "10px 0 4px"}),
                html.Table([header, html.Tbody([_row(r, sort_col)
                            for r in grp.to_dict("records")])],
                    style={"width": "100%", "borderCollapse": "collapse"}),
            ]))
        return html.Div(blocks)

    table = table.sort_values(sort_col, ascending=False, na_position="last")
    return html.Table([header, html.Tbody([_row(r, sort_col)
                       for r in table.to_dict("records")])],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _header_row(since_label, sort_col):
    cells = [html.Th("Instrument", style=_th("left"))]
    for c in HEAT_COLS:
        label = since_label if c == "Since" else c
        active = c == sort_col
        cells.append(html.Th(
            (label + (" \u25BC" if active else "")),
            style={**_th("right"),
                   "background": theme.NEEDLE if active else theme.INK}))
    return html.Thead(html.Tr(cells))


def _th(align):
    return {"background": theme.INK, "color": "#fff", "padding": "6px 8px",
            "fontSize": "10px", "fontWeight": 600, "textAlign": align,
            "whiteSpace": "nowrap", "position": "sticky", "top": 0,
            "zIndex": 1}


def _row(r, sort_col):
    cells = [html.Td(html.Span(_short(r["Fund"]), title=str(r["Fund"])),
                     style={"padding": "4px 8px", "fontSize": "11px",
                            "fontWeight": 500, "color": theme.INK,
                            "whiteSpace": "nowrap", "maxWidth": "230px",
                            "overflow": "hidden", "textOverflow": "ellipsis",
                            "position": "sticky", "left": 0,
                            "background": "white"})]
    for c in HEAT_COLS:
        val = r.get(c)
        has = pd.notna(val)
        emphasise = c == sort_col
        cells.append(html.Td(
            f"{val:+.1f}%" if has else "\u2014",
            style={"padding": "4px 8px", "fontSize": "10.5px",
                   "textAlign": "right", "fontWeight": 700 if emphasise else 600,
                   **theme.NUM, "color": theme.INK,
                   "background": theme.heat_rgb(val if has else None),
                   "outline": (f"2px solid {theme.NEEDLE}" if emphasise and has
                               else "none"),
                   "outlineOffset": "-2px"}))
    return html.Tr(cells, style={"borderBottom": f"1px solid {theme.LINE}"})


def _short(name, n=32):
    s = str(name)
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def _empty(msg):
    return html.Div(msg, style={"color": theme.NEUTRAL, "fontSize": "12px",
                                "padding": "18px"})
