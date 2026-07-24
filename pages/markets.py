"""Markets section — registered page. Renders the shared universe selector
above the Transactions / Compare / Heatmap tabs."""
import dash
from dash import html, dcc, callback, Input, Output

from ui import universe
from pages import transactions

dash.register_page(__name__, path="/markets", name="Markets", order=2)

TABS = {
    "Transactions": transactions.render,
    # "Compare": compare.render,
    # "Heatmap": heatmap.render,
}


def layout():
    names = list(TABS)
    return html.Div([
        universe.selector_bar(),
        dcc.Tabs(id="markets-tabs", value=names[0],
                 children=[dcc.Tab(label=n, value=n) for n in names],
                 style={"marginBottom": "14px"}),
        html.Div(id="markets-body"),
    ], style={"maxWidth": "1500px", "margin": "0 auto", "padding": "18px"})


@callback(Output("markets-body", "children"), Input("markets-tabs", "value"))
def _render(tab):
    return TABS.get(tab, lambda: html.Div())()