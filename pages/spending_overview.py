"""
Spending -> Overview.

The 8052 Dashboard page: one year of spending as a category x month table
(click a category to open its sub-categories), or the same as a stacked
monthly chart. Income and pending rows are shown on request and never count
in the spending total. Finance is excluded by default, as it was on 8052:
card repayments and transfers live there.

Amounts are signed as stored - spending in brackets - so a refund inside a
category reduces that category's figure.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from dash import html, dcc, callback, Input, Output, State, ALL, ctx, no_update

from core import theme, spending as sp
from core.repo import spending as repo
from ui import figures
from ui.layout import placeholder
from ui.spend import bar, field, header, NOTE

MONTHS = sp.MONTHS
UNASSIGNED = "(unassigned)"


def render():
    years = repo.years()
    if not years:
        return placeholder("No spending data yet - import a bank file on the "
                           "Import tab, or run scripts/migrate_spending.py "
                           "once to bring over the 8052 history.")
    cats = list(repo.categories())
    c = repo.counts()
    last = (f"last posted {pd.Timestamp(c['last_date']):%d %b %Y}"
            if c["last_date"] else "")
    info = " · ".join(x for x in [
        f"{c['total']:,} transactions", f"{c['pending']:,} pending", last] if x)

    multi = {"fontSize": "12.5px"}
    return html.Div([
        header("Overview", "Spending by category and month. Click a category "
               "to open its sub-categories."),
        html.Div([
            bar(
                field("Year", dcc.Dropdown(
                    id="sp-ov-year", value=years[0], clearable=False,
                    options=[{"label": y, "value": y} for y in years],
                    style=multi), width="100px"),
                field("View", dcc.RadioItems(
                    id="sp-ov-view", value="Pivot", inline=True,
                    options=["Pivot", "Monthly trend"],
                    inputStyle={"marginRight": "5px"},
                    labelStyle={"marginRight": "14px", "fontSize": "12.5px"})),
                field("Show", dcc.Checklist(
                    id="sp-ov-show", value=["income"], inline=True,
                    options=[{"label": "Income", "value": "income"},
                             {"label": "Pending", "value": "pending"}],
                    inputStyle={"marginRight": "5px"},
                    labelStyle={"marginRight": "14px", "fontSize": "12.5px"})),
                field("Exclude categories", dcc.Dropdown(
                    id="sp-ov-xcat", multi=True,
                    value=["Finance"] if "Finance" in cats else [],
                    options=[x for x in cats if x != "Income"], style=multi),
                    width="260px"),
                field("Exclude sub-categories", dcc.Dropdown(
                    id="sp-ov-xsub", multi=True, value=[],
                    options=repo.used_subcategories(), style=multi),
                    width="220px"),
                field("Exclude cards", dcc.Dropdown(
                    id="sp-ov-xcard", multi=True, value=[],
                    options=repo.card_names(active_only=False), style=multi),
                    width="220px"),
            ),
            html.Div(info, style={**NOTE, "marginTop": "10px"}),
        ], style=theme.CARD),
        html.Div(id="sp-ov-body", style=theme.CARD),
        dcc.Store(id="sp-ov-open", data=[]),
    ])


# --- data ---------------------------------------------------------------------

def _frames(year, show, xcat, xsub, xcard):
    df = repo.year_rows(year)
    if xcard:
        df = df[~df["card"].isin(xcard)]
    df = df.assign(category=df["category"].fillna(UNASSIGNED),
                   subcategory=df["subcategory"].fillna(""))
    confirmed = df[df["status"] == "confirmed"]
    spend = confirmed[(confirmed["category"] != "Income")
                      & ~confirmed["category"].isin(xcat or [])
                      & ~confirmed["subcategory"].isin(xsub or [])]
    income = (confirmed[confirmed["category"] == "Income"]
              if "income" in (show or []) else confirmed.iloc[0:0])
    pending = (df[(df["status"] == "pending")
                  & ~df["category"].isin(xcat or [])]
               if "pending" in (show or []) else df.iloc[0:0])
    return spend, income, pending


def _order(cats_in_data):
    known = list(repo.categories())
    return ([c for c in known if c in cats_in_data]
            + sorted(c for c in cats_in_data if c not in known))


# --- pivot --------------------------------------------------------------------

def _fmt(v):
    v = round(float(v))
    return "" if v == 0 else theme.money(v, currency="", dp=0)


def _cells(row, style):
    return [html.Td(_fmt(row[m]), style=style) for m in [*MONTHS, "Total"]]


def _pivot(spend, income, pending, open_cats):
    th = {"background": theme.INK, "color": theme.SURFACE, "padding": "6px 8px",
          "fontSize": "10.5px", "fontWeight": 600, "textAlign": "right",
          "position": "sticky", "top": 0, "letterSpacing": "0.04em"}
    head = html.Thead(html.Tr(
        [html.Th("Category", style={**th, "textAlign": "left",
                                    "width": "180px"})]
        + [html.Th(m, style=th) for m in [*MONTHS, "Total"]]))

    cell = {"padding": "5px 8px", "fontSize": "12px",
            "borderBottom": f"1px solid {theme.LINE_SOFT}"}
    num = {**cell, **theme.NUM, "textAlign": "right"}
    body = []

    def section(df, kind):
        if df.empty:
            return
        month_rows = sp.month_table(df, ["category"]).set_index("category")
        subs = sp.month_table(df, ["category", "subcategory"])
        tint = {"pending": {"background": theme.TINT_NEAR},
                "income": {"color": theme.POSITIVE}}.get(kind, {})
        for cat in _order(set(month_rows.index)):
            row = month_rows.loc[cat]
            opened = cat in open_cats and kind != "pending"
            marker = "▾ " if opened else "▸ "
            label = cat + (" · pending" if kind == "pending" else "")
            body.append(html.Tr(
                [html.Td((marker if kind != "pending" else "") + label,
                         style={**cell, "fontWeight": 600, **tint})]
                + _cells(row, {**num, "fontWeight": 600, **tint}),
                id={"type": "sp-ov-cat", "cat": f"{kind}:{cat}"}, n_clicks=0,
                style={"cursor": "pointer" if kind != "pending" else "default"}))
            if opened:
                for r in subs[subs["category"] == cat].to_dict("records"):
                    sub_style = {**cell, "fontSize": "11.5px",
                                 "color": tint.get("color", theme.SLATE)}
                    body.append(html.Tr(
                        [html.Td(r["subcategory"] or "—",
                                 style={**sub_style, "paddingLeft": "26px"})]
                        + _cells(r, {**num, **sub_style})))

    section(spend, "spend")
    total = sp.month_table(spend.assign(all="Total"), ["all"])
    if not total.empty:
        strong = {"fontWeight": 700, "borderTop": f"2px solid {theme.INK}",
                  "background": theme.CANVAS}
        body.append(html.Tr(
            [html.Td("Total spending", style={**cell, **strong})]
            + _cells(total.iloc[0], {**num, **strong})))
    section(income, "income")
    section(pending, "pending")

    note = html.Div("Spending in brackets · refunds reduce their "
                    "category · pending rows are not in the total",
                    style={**NOTE, "marginTop": "8px", "fontSize": "10.5px",
                           "color": theme.NEUTRAL})
    return html.Div([html.Table([head, html.Tbody(body)],
                                style={"width": "100%",
                                       "borderCollapse": "collapse"}),
                     note], style={"overflowX": "auto"})


# --- trend --------------------------------------------------------------------

def _trend(spend):
    if spend.empty:
        return figures.empty("No spending for these filters", 420)
    t = sp.month_table(spend, ["category"]).set_index("category")
    fig = go.Figure()
    for i, cat in enumerate(_order(set(t.index))):
        y = [-t.loc[cat, m] for m in MONTHS]          # spending as positive
        fig.add_bar(name=cat, x=MONTHS, y=y,
                    marker_color=figures.SEQUENCE[i % len(figures.SEQUENCE)],
                    hovertemplate=f"{cat}: £%{{y:,.0f}}<extra></extra>")
    totals = [-t[m].sum() for m in MONTHS]
    fig.add_scatter(x=MONTHS, y=totals, mode="text", showlegend=False,
                    text=[f"£{v:,.0f}" if v else "" for v in totals],
                    textposition="top center", hoverinfo="skip",
                    textfont=dict(size=10, color=theme.SLATE))
    fig.update_layout(**figures.base_layout(
        barmode="relative", height=460,
        yaxis=dict(tickprefix="£", tickformat=",.0f",
                   gridcolor=theme.LINE_SOFT),
        legend=dict(orientation="h", y=1.08, x=0, font=dict(size=11)),
        margin=dict(l=50, r=20, t=40, b=30)))
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


# --- callbacks ----------------------------------------------------------------

@callback(
    Output("sp-ov-body", "children"),
    Input("sp-ov-year", "value"),
    Input("sp-ov-view", "value"),
    Input("sp-ov-show", "value"),
    Input("sp-ov-xcat", "value"),
    Input("sp-ov-xsub", "value"),
    Input("sp-ov-xcard", "value"),
    Input("sp-ov-open", "data"),
)
def _render(year, view, show, xcat, xsub, xcard, open_keys):
    if not year:
        return no_update
    spend, income, pending = _frames(year, show, xcat, xsub, xcard)
    if view == "Monthly trend":
        return _trend(spend)
    if spend.empty and income.empty and pending.empty:
        return html.Div("Nothing for these filters.", style=NOTE)
    open_cats = {k.split(":", 1)[1] for k in open_keys or []
                 if k.startswith(("spend:", "income:"))}
    return _pivot(spend, income, pending, open_cats)


@callback(
    Output("sp-ov-open", "data"),
    Input({"type": "sp-ov-cat", "cat": ALL}, "n_clicks"),
    State("sp-ov-open", "data"),
    prevent_initial_call=True,
)
def _toggle(clicks, open_keys):
    # A rebuilt table arrives with every n_clicks at 0: not a click.
    trig = ctx.triggered_id
    if not isinstance(trig, dict) or not any(clicks or []):
        return no_update
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        return no_update
    key = trig["cat"]
    if key.startswith("pending:"):
        return no_update
    open_keys = list(open_keys or [])
    if key in open_keys:
        open_keys.remove(key)
    else:
        open_keys.append(key)
    return open_keys
