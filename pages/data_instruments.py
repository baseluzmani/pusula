"""
Data - Instruments tab.

Editable metadata for every instrument. This is where the currency and
price_unit fields get fixed, which matters more than it looks: to_gbp
branches on those two values, and a NULL matches neither branch, so the
price is passed through as if it were already GBP pounds. A USD instrument
with a NULL currency is overstated by roughly a third; a pence-quoted one is
overstated a hundredfold.

The "Needs attention" filter shows exactly those rows. Nothing currently
held is affected - the incomplete ones are watchlist instruments - but they
would misprice the moment you bought one.

fund_id is shown but never editable: re-keying an instrument would orphan its
prices, transactions and snapshot rows.
"""

from __future__ import annotations

import dash
from dash import (html, dcc, dash_table, callback, Input, Output, State,
                  no_update)

from core import theme
from core.repo import standing as repo

COLUMNS = [
    {"name": "Fund ID", "id": "fund_id", "editable": False},
    {"name": "Name", "id": "name", "editable": True},
    {"name": "Type", "id": "asset_type", "editable": True},
    {"name": "Currency", "id": "currency", "editable": True},
    {"name": "Price unit", "id": "price_unit", "editable": True},
    {"name": "Category", "id": "category", "editable": True},
    {"name": "Active", "id": "active", "editable": True, "type": "numeric"},
    {"name": "Prices", "id": "price_rows", "editable": False,
     "type": "numeric"},
    {"name": "Last price", "id": "last_price", "editable": False},
]


# Fund IDs read as identifiers, so give them a mono face; counts right-align.
CELL_STYLES = [
    {"if": {"column_id": "fund_id"}, "color": theme.SLATE,
     "fontFamily": "DM Mono, ui-monospace, monospace", "fontSize": "10.5px"},
] + [
    {"if": {"column_id": c}, "textAlign": "right"}
    for c in ("active", "price_rows")
]


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Instruments", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Currency and price unit drive every GBP conversion. "
                         "Rows missing either are highlighted.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Button("Save", id="ins-save", n_clicks=0, style={
                "padding": "7px 15px", "borderRadius": "4px",
                "fontSize": "12px", "fontWeight": 600, "cursor": "pointer",
                "border": "none", "backgroundColor": theme.POSITIVE,
                "color": "#fff"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "12px"}),

        html.Div([
            dcc.Input(id="ins-search", type="text", debounce=True,
                      placeholder="Search name or fund id\u2026",
                      style={"padding": "7px 10px", "fontSize": "12.5px",
                             "borderRadius": "5px", "width": "260px",
                             "border": f"1px solid {theme.LINE}"}),
            dcc.Checklist(id="ins-filters", value=[],
                          options=[
                              {"label": " Needs attention",
                               "value": "incomplete"},
                              {"label": " Held or traded only",
                               "value": "held"},
                          ],
                          inline=True,
                          inputStyle={"marginRight": "6px"},
                          labelStyle={"marginRight": "18px",
                                      "fontSize": "12.5px",
                                      "color": theme.TEXT}),
            html.Span(id="ins-count", style={"fontSize": "11.5px",
                      "color": theme.NEUTRAL}),
        ], style={"display": "flex", "alignItems": "center", "gap": "16px",
                  "marginBottom": "12px", "flexWrap": "wrap"}),

        html.Div(id="ins-feedback", style={"marginBottom": "10px"}),

        html.Div(dash_table.DataTable(
            id="ins-table",
            columns=COLUMNS,
            data=[],
            editable=True,
            sort_action="native",
            page_size=40,
            style_table={"overflowX": "auto"},
            style_header={"backgroundColor": theme.INK, "color": "white",
                          "fontWeight": "600", "fontSize": "11px",
                          "textAlign": "left", "border": "none"},
            style_cell={"padding": "5px 9px", "fontSize": "11.5px",
                        "fontFamily": "DM Sans, system-ui, sans-serif",
                        "textAlign": "left", "color": theme.INK,
                        "border": f"1px solid {theme.LINE}",
                        "maxWidth": "260px", "overflow": "hidden",
                        "textOverflow": "ellipsis"},
            style_cell_conditional=CELL_STYLES,
            style_data_conditional=[
                {"if": {"filter_query": "{currency} is blank"},
                 "backgroundColor": "#FDECEA"},
                {"if": {"filter_query": "{price_unit} is blank"},
                 "backgroundColor": "#FDECEA"},
                {"if": {"filter_query": "{active} = 0"},
                 "color": theme.NEUTRAL},
                {"if": {"filter_query": "{price_rows} = 0"},
                 "color": theme.NEGATIVE},
            ],
        ), style=theme.CARD),
    ])


@callback(
    Output("ins-table", "data"), Output("ins-count", "children"),
    Input("ins-search", "value"), Input("ins-filters", "value"),
    Input("ins-feedback", "children"),
)
def _load(search, filters, _refresh):
    filters = filters or []
    df = repo.instruments(search=search or "",
                          incomplete_only="incomplete" in filters,
                          held_only="held" in filters)
    if df.empty:
        return [], "no instruments match"

    missing = int(((df["currency"].isna() | (df["currency"] == "")) |
                   (df["price_unit"].isna() | (df["price_unit"] == ""))).sum())
    label = f"{len(df)} shown"
    if missing:
        label += f" \u00b7 {missing} missing currency or price unit"
    return df.to_dict("records"), label


@callback(
    Output("ins-feedback", "children"),
    Input("ins-save", "n_clicks"), State("ins-table", "data"),
    prevent_initial_call=True,
)
def _save(_n, rows):
    rows = list(rows or [])
    if not rows:
        return _msg("Nothing to save.", False)
    try:
        n = repo.save_instruments(rows)
    except Exception as exc:                                   # noqa: BLE001
        return _msg(f"Save failed: {exc}", False)
    return _msg(f"Saved {n} instruments.")


def _msg(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEEDLE
    return html.Div(text, style={
        "padding": "9px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour,
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})
