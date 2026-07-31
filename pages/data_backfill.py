"""
Data - Backfill tab.

Fetches a date range from Yahoo and stores anything not already held. For
filling gaps: a ticker that stopped updating, a newly added instrument that
needs history, or a stretch Yahoo missed and later published.

Existing dates are skipped rather than overwritten, so running the same range
twice is harmless.

The run is synchronous - the page waits for it. A single ticker over a year is
a few seconds; every ticker over several years is minutes. The gap summary
below the form is there so you can aim at what actually needs filling instead
of refetching everything.
"""

from __future__ import annotations

from datetime import date, timedelta

import dash
from dash import html, dcc, callback, Input, Output, State, no_update

import pandas as pd

from core import db, theme, valuation as val
from core.repo import tickers as ticker_repo
from importers import backfill


def _ticker_options():
    return [{"label": f"{t[1]} ({t[0]})", "value": t[0]}
            for t in ticker_repo.yahoo_tickers()]


def render():
    today = date.today()
    return html.Div([
        html.Div([
            html.Div("Backfill prices", style={"fontSize": "17px",
                     "fontWeight": 700, "color": theme.INK}),
            html.Div("Fetch a date range from Yahoo. Dates already held are "
                     "skipped, so re-running a range is safe.",
                     style={"fontSize": "12px", "color": theme.SLATE,
                            "marginTop": "2px"}),
        ], style={"marginBottom": "12px"}),

        html.Div([
            html.Div([
                _field("Scope", dcc.RadioItems(
                    id="bf-scope", value="all", inline=True,
                    options=[{"label": " All tracked tickers", "value": "all"},
                             {"label": " One ticker", "value": "one"}],
                    inputStyle={"marginRight": "5px"},
                    labelStyle={"marginRight": "18px", "fontSize": "12.5px",
                                "color": theme.TEXT, "cursor": "pointer"}),
                       None),
            ]),
            html.Div(id="bf-ticker-wrap", style={"display": "none"},
                     children=_field("Ticker", dcc.Dropdown(
                         id="bf-ticker", options=_ticker_options(),
                         placeholder="Choose a ticker\u2026",
                         style={"fontSize": "12.5px"}), "320px")),
            html.Div([
                _field("From", dcc.DatePickerSingle(
                    id="bf-from", display_format="DD MMM YYYY",
                    date=(today - timedelta(days=90)).strftime("%Y-%m-%d")),
                       None),
                _field("To", dcc.DatePickerSingle(
                    id="bf-to", display_format="DD MMM YYYY",
                    date=today.strftime("%Y-%m-%d")), None),
                html.Div(html.Button("Run backfill", id="bf-run", n_clicks=0,
                         style={"padding": "8px 16px", "borderRadius": "5px",
                                "fontSize": "12.5px", "fontWeight": 600,
                                "cursor": "pointer", "border": "none",
                                "backgroundColor": theme.POSITIVE,
                                "color": "#fff"}),
                         style={"marginBottom": "2px"}),
            ], style={"display": "flex", "gap": "12px",
                      "alignItems": "flex-end", "marginTop": "10px"}),
        ], style={**theme.CARD, "marginBottom": "12px"}),

        dcc.Loading(html.Div(id="bf-result"), type="default"),

        html.Div([
            html.Div("Where the gaps are", style=theme.CARD_TITLE),
            html.Div("Tracked tickers whose latest price is not recent. "
                     "A market holiday explains a day or two; weeks means "
                     "something is wrong.",
                     style={"fontSize": "11.5px", "color": theme.NEUTRAL,
                            "marginBottom": "10px"}),
            html.Div(id="bf-gaps"),
        ], style=theme.CARD),
    ])


def _field(label, control, width):
    style = {"width": width} if width else {}
    return html.Div([
        html.Label(label, style={"display": "block", "fontSize": "10px",
                   "fontWeight": 700, "letterSpacing": "0.06em",
                   "textTransform": "uppercase", "color": theme.SLATE,
                   "marginBottom": "5px"}),
        control,
    ], style=style)


@callback(Output("bf-ticker-wrap", "style"), Input("bf-scope", "value"))
def _toggle(scope):
    return {"display": "block", "marginTop": "10px"} if scope == "one" \
        else {"display": "none"}


@callback(
    Output("bf-result", "children"), Output("bf-gaps", "children"),
    Input("bf-run", "n_clicks"),
    State("bf-scope", "value"), State("bf-ticker", "value"),
    State("bf-from", "date"), State("bf-to", "date"),
    prevent_initial_call=True,
)
def _run(_n, scope, ticker, start, end):
    if scope == "one" and not ticker:
        return _msg("Choose a ticker, or switch to all.", False), no_update
    if not start:
        return _msg("A start date is required.", False), no_update

    try:
        result = backfill.run(ticker=ticker if scope == "one" else None,
                              start=start, end=end)
    except Exception as exc:                                   # noqa: BLE001
        return _msg(f"Backfill failed: {exc}", False), no_update

    return _msg(result.get("message", "Done."),
                result.get("saved", 0) > 0), _gaps()


@callback(Output("bf-gaps", "children", allow_duplicate=True),
          Input("bf-scope", "value"), prevent_initial_call="initial_duplicate")
def _initial_gaps(_scope):
    return _gaps()


def _gaps():
    """Tracked tickers whose most recent price is more than a few days old."""
    df = db.query("""
        SELECT i.source_id AS ticker, i.name,
               MAX(p.date) AS last_price,
               julianday('now') - julianday(MAX(p.date)) AS days_behind
        FROM instruments i
        LEFT JOIN prices p ON p.fund_id = i.fund_id
        WHERE i.source = 'yahoo' AND COALESCE(i.active, 1) = 1
        GROUP BY i.fund_id
        HAVING last_price IS NULL OR days_behind > 5
        ORDER BY days_behind DESC NULLS FIRST
        LIMIT 40
    """)
    if df.empty:
        return html.P("Every tracked ticker priced within the last few days.",
                      style={"color": theme.POSITIVE, "fontSize": "12px"})

    head = html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "5px 8px", "fontSize": "10px", "fontWeight": 600,
                "textAlign": "left" if i < 2 else "right"})
        for i, c in enumerate(["Ticker", "Name", "Last price", "Days"])]))

    rows = []
    for r in df.to_dict("records"):
        # SQL NULL arrives as NaN, which is truthy and is not None - so both
        # `or` and `is not None` let it through and it renders as "nan".
        days = val.clean(r["days_behind"])
        last = r["last_price"]
        last = "never" if last is None or pd.isna(last) else str(last)
        colour = (theme.NEGATIVE if days is None or days > 30
                  else theme.NEEDLE)
        rows.append(html.Tr([
            html.Td(r["ticker"] or "\u2014",
                    style={"padding": "4px 8px", "fontSize": "11px",
                           **theme.NUM, "color": theme.INK}),
            html.Td(r["name"] or "", style={"padding": "4px 8px",
                    "fontSize": "11px", "color": theme.SLATE}),
            html.Td(last,
                    style={"padding": "4px 8px", "fontSize": "11px",
                           "textAlign": "right", **theme.NUM,
                           "color": theme.SLATE}),
            html.Td(f"{days:,.0f}" if days is not None else "\u2014",
                    style={"padding": "4px 8px", "fontSize": "11px",
                           "textAlign": "right", **theme.NUM,
                           "fontWeight": 600, "color": colour}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))

    return html.Table([head, html.Tbody(rows)],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _msg(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEEDLE
    return html.Div(text, style={
        "padding": "10px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour, "marginBottom": "12px",
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})
