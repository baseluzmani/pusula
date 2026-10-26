import dash
from dash import html, callback, Input, Output

from ui.layout import page_header, subtabs
from core import theme
from pages import (spending_allowances, spending_overview,
                   spending_transactions, spending_statements, spending_queue,
                   spending_import, spending_rules)

dash.register_page(__name__, path="/spending", name="Spending", order=3)

TABS = {
    "Overview": spending_overview.render,
    "Transactions": spending_transactions.render,
    "Statements": spending_statements.render,
    "Manual Queue": spending_queue.render,
    "Import": spending_import.render,
    "Rules": spending_rules.render,
    "Allowances": spending_allowances.render,
}

layout = html.Div([
    subtabs("sp-tabs", list(TABS)),
    html.Div([
        page_header("Spending",
                    "Household outgoings by category, with a review queue for "
                    "transactions the rules cannot classify."),
        html.Div(id="sp-body"),
    ], style=theme.PAGE),
])


@callback(Output("sp-body", "children"), Input("sp-tabs", "value"))
def render(tab):
    # A data problem shows inside the tab instead of blanking the page.
    try:
        return TABS.get(tab, spending_overview.render)()
    except Exception as exc:                       # noqa: BLE001 - deliberate
        return html.Div(f"{tab} could not load: {type(exc).__name__}: {exc}",
                        style={**theme.CARD, "color": theme.NEGATIVE,
                               "fontSize": "12.5px"})
