"""
Shared universe selector for the Markets section.

Rendered once above the tabs. Holds the chosen fund universe in a single
store (``markets-universe``) so the selection persists as the user moves
between Transactions, Compare and Heatmap.

Store shape:  {"mode": "holdings" | "all" | "select", "picked": [fund_id, ...]}

- mode "holdings": the resolved ids are open positions (portfolio_holdings)
- mode "all":      every instrument with prices
- mode "select":   exactly the ids in "picked" (checkbox dropdown)

``resolve_ids(store)`` turns the store into the concrete id list every tab
should display. Keeping that logic here means the three tabs never each
re-implement "what does 'my holdings' mean".
"""

from dash import html, dcc, callback, Input, Output, State, ctx, no_update

from core import theme
from core.repo import market as repo


STORE_ID = "markets-universe"


def resolve_ids(store: dict) -> list:
    """Concrete fund ids for the current universe selection."""
    store = store or {}
    mode = store.get("mode", "holdings")
    if mode == "all":
        return repo.all_instrument_ids()
    if mode == "select":
        return list(store.get("picked") or [])
    return repo.open_position_ids()


def default_store() -> dict:
    """First-load universe: My holdings."""
    return {"mode": "holdings", "picked": []}


def selector_bar():
    """The selector row plus its backing store. Rendered once, above tabs."""
    return html.Div([
        dcc.Store(id=STORE_ID, data=default_store()),

        html.Div([
            html.Span("Universe", style={
                "fontSize": "10px", "fontWeight": 700, "letterSpacing": "0.07em",
                "textTransform": "uppercase", "color": theme.SLATE,
                "marginRight": "14px"}),
            dcc.RadioItems(
                id="markets-mode",
                options=[
                    {"label": "My holdings", "value": "holdings"},
                    {"label": "All instruments", "value": "all"},
                    {"label": "Select\u2026", "value": "select"},
                ],
                value="holdings",
                inline=True,
                inputStyle={"marginRight": "5px"},
                labelStyle={"marginRight": "18px", "fontSize": "13px",
                            "color": theme.TEXT, "cursor": "pointer"},
            ),
            html.Span(id="markets-universe-count", style={
                "fontSize": "11px", "color": theme.NEUTRAL, "marginLeft": "6px"}),
        ], style={"display": "flex", "alignItems": "center",
                  "flexWrap": "wrap", "gap": "6px"}),

        # Checkbox dropdown, only visible in "Select..." mode.
        html.Div(id="markets-picker-wrap", style={"display": "none"}, children=[
            html.Div([
                dcc.Input(id="markets-picker-search", type="text",
                          placeholder="Search instruments\u2026", debounce=False,
                          style={"width": "100%", "padding": "7px 10px",
                                 "fontSize": "13px", "borderRadius": "5px",
                                 "border": f"1px solid {theme.LINE}",
                                 "marginBottom": "8px"}),
                html.Div([
                    html.Button("Select all", id="markets-pick-all", n_clicks=0,
                                style=_link_btn()),
                    html.Button("Deselect all", id="markets-pick-none", n_clicks=0,
                                style=_link_btn()),
                ], style={"display": "flex", "gap": "16px", "marginBottom": "6px"}),
                dcc.Checklist(id="markets-picker", value=[], options=[],
                              style={"maxHeight": "260px", "overflowY": "auto",
                                     "display": "block"},
                              inputStyle={"marginRight": "8px"},
                              labelStyle={"display": "block", "fontSize": "13px",
                                          "padding": "4px 2px",
                                          "color": theme.TEXT, "cursor": "pointer"}),
            ], style={**theme.CARD, "marginTop": "10px", "padding": "12px 14px",
                      "maxWidth": "460px"}),
        ]),
    ], style={"marginBottom": "16px"})


def _link_btn():
    return {"background": "none", "border": "none", "padding": 0,
            "fontSize": "12px", "fontWeight": 600, "cursor": "pointer",
            "color": theme.NEEDLE}


def _picker_options():
    df = repo.instruments()
    if df.empty:
        return []
    return [{"label": (r.name if r.name else r.fund_id), "value": r.fund_id}
            for r in df.itertuples()]


# Show/hide the picker card with the radio.
@callback(
    Output("markets-picker-wrap", "style"),
    Input("markets-mode", "value"),
)
def _toggle_picker(mode):
    if mode == "select":
        return {"display": "block"}
    return {"display": "none"}


# Populate picker options the first time Select... is entered, defaulting to
# everything ticked (per the agreed behaviour). Search filters the visible
# options without losing ticks outside the filter.
@callback(
    Output("markets-picker", "options"),
    Output("markets-picker", "value"),
    Input("markets-mode", "value"),
    Input("markets-picker-search", "value"),
    Input("markets-pick-all", "n_clicks"),
    Input("markets-pick-none", "n_clicks"),
    State("markets-picker", "value"),
    State(STORE_ID, "data"),
)
def _fill_picker(mode, search, _all, _none, current, store):
    if mode != "select":
        return no_update, no_update

    every = _picker_options()
    all_ids = [o["value"] for o in every]
    trigger = ctx.triggered_id

    # Establish the working selection.
    if trigger == "markets-pick-all":
        selected = all_ids
    elif trigger == "markets-pick-none":
        selected = []
    elif trigger == "markets-mode":
        # Entering select mode: seed from store if it held a pick, else all.
        picked = (store or {}).get("picked") or []
        selected = picked if picked else all_ids
    else:
        selected = current or []

    # Filter the *visible* options by search, but keep ticks for hidden ones.
    if search:
        needle = search.lower()
        visible = [o for o in every if needle in o["label"].lower()]
    else:
        visible = every

    return visible, selected


# Single writer for the universe store - avoids the two-callbacks-one-store
# bug that made the original Market Overview flaky.
@callback(
    Output(STORE_ID, "data"),
    Output("markets-universe-count", "children"),
    Input("markets-mode", "value"),
    Input("markets-picker", "value"),
    State("markets-picker", "options"),
)
def _write_store(mode, picked, options):
    if mode == "select":
        # picked holds only currently-visible ticks; merge with any ticked
        # rows filtered out by an active search is handled in _fill_picker,
        # so picked already reflects the full working set on store writes
        # triggered by mode/all/none. For search-narrowed states we still
        # store what is ticked.
        ids = list(picked or [])
        store = {"mode": "select", "picked": ids}
        count = f"{len(ids)} selected"
    elif mode == "all":
        ids = repo.all_instrument_ids()
        store = {"mode": "all", "picked": []}
        count = f"{len(ids)} instruments"
    else:
        ids = repo.open_position_ids()
        store = {"mode": "holdings", "picked": []}
        count = f"{len(ids)} holdings"
    return store, count
