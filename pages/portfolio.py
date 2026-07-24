import dash
from dash import html, callback, Input, Output

from ui.layout import page_header, subtabs, placeholder
from core import theme

dash.register_page(__name__, path="/", name="Portfolio", order=1)

TABS = ["Holdings", "Allocation", "Performance", "Transactions"]

layout = html.Div([
    subtabs("pf-tabs", TABS),
    html.Div([
        page_header("Portfolio",
                    "Positions, weights and valuation across ISA, SIPP, GIA and JISA."),
        html.Div(id="pf-body"),
    ], style=theme.PAGE),
])


@callback(Output("pf-body", "children"), Input("pf-tabs", "value"))
def render(tab):
    return placeholder(f"{tab} - to be migrated from dashboard.py (8050)")
