import dash
from dash import html, callback, Input, Output

from ui.layout import page_header, subtabs, placeholder
from core import theme

dash.register_page(__name__, path="/analytics", name="Analytics", order=2)

TABS = ["Correlation", "Risk Contribution", "Drawdown", "Factor Exposure"]

layout = html.Div([
    subtabs("an-tabs", TABS),
    html.Div([
        page_header("Analytics",
                    "Market data analysis across the portfolio. Weekly returns by "
                    "default to reduce asynchronous pricing noise."),
        html.Div(id="an-body"),
    ], style=theme.PAGE),
])


@callback(Output("an-body", "children"), Input("an-tabs", "value"))
def render(tab):
    return placeholder(f"{tab} - to be migrated from port 8051")
