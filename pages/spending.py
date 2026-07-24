import dash
from dash import html, callback, Input, Output

from ui.layout import page_header, subtabs, placeholder
from core import theme

dash.register_page(__name__, path="/spending", name="Spending", order=3)

TABS = ["Overview", "Categories", "Manual Queue", "Cards"]

layout = html.Div([
    subtabs("sp-tabs", TABS),
    html.Div([
        page_header("Spending",
                    "Household outgoings by category, with a review queue for "
                    "transactions the rules cannot classify."),
        html.Div(id="sp-body"),
    ], style=theme.PAGE),
])


@callback(Output("sp-body", "children"), Input("sp-tabs", "value"))
def render(tab):
    return placeholder(f"{tab} - to be migrated from port 8052")
