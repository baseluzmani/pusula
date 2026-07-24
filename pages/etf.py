import dash
from dash import html, callback, Input, Output

from ui.layout import page_header, subtabs, placeholder
from core import theme

dash.register_page(__name__, path="/etf", name="ETF", order=4)

TABS = ["Holdings", "Overlap", "Changes", "Compare", "Ticker Map", "Sources"]

layout = html.Div([
    subtabs("etf-tabs", TABS),
    html.Div([
        page_header("ETF & Fund Holdings",
                    "Look-through holdings, overlap and identifier mapping."),
        html.Div(id="etf-body"),
    ], style=theme.PAGE),
])


@callback(Output("etf-body", "children"), Input("etf-tabs", "value"))
def render(tab):
    return placeholder(f"{tab} - to be migrated from port 8053")
