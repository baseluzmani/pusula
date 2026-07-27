"""
Portfolio section shell — registered page.

Hosts the Portfolio tabs (P&L first; Transactions, Accounts, Charts,
Portfolio, Summary slot in as built). Migrated from the legacy 8050
dashboard. All P&L maths goes through core.finance, which is parity-verified
against the legacy engine.
"""
import dash
from dash import html, dcc, callback, Input, Output

from pages import (portfolio_holdings, portfolio_pnl, portfolio_txns,
                   portfolio_accounts, portfolio_charts, portfolio_summary)

dash.register_page(__name__, path="/", name="Portfolio", order=1)

TABS = {
    "Portfolio": portfolio_holdings.render,
    "P&L": portfolio_pnl.render,
    "Transactions": portfolio_txns.render,
    "Accounts": portfolio_accounts.render,
    "Portfolio": portfolio_holdings.render,
    "Charts": portfolio_charts.render,
    "Summary": portfolio_summary.render,
}


def layout():
    names = list(TABS)
    return html.Div([
        dcc.Tabs(id="pf-tabs", value=names[0],
                 children=[dcc.Tab(label=n, value=n) for n in names],
                 style={"marginBottom": "14px"}),
        html.Div(id="pf-body"),
    ], style={"maxWidth": "1600px", "margin": "0 auto", "padding": "18px"})


@callback(Output("pf-body", "children"), Input("pf-tabs", "value"))
def _render(tab):
    return TABS.get(tab, lambda: html.Div())()
