"""
Shared universe selector for the Markets section.

Rendered once above the tabs. Holds the chosen fund universe in a single
store (``markets-universe``) so the selection persists as the user moves
between Transactions, Compare, Heatmap and Momentum.

Store shape:  {"mode": "holdings" | "all" | "type" | "select",
               "picked": [fund_id, ...], "types": [asset_type, ...]}

- mode "holdings": the resolved ids are open positions (portfolio_holdings)
- mode "all":      every instrument with prices
- mode "type":     every priced instrument of the asset types in "types"
- mode "select":   exactly the ids in "picked" (checkbox dropdown)

``resolve_ids(store)`` turns the store into the concrete id list every tab
should display. Keeping that logic here means the three tabs never each
re-implement "what does 'my holdings' mean".
"""

from dash import html, dcc, callback, Input, Output, State, ctx, no_update

from core import theme
from core.repo import market as repo


STORE_ID = "markets-universe"
PROMPT = "Choose a universe above to load the data."


def resolve_ids(store: dict) -> list:
    """Concrete fund ids for the current universe selection."""
    store = store or {}
    mode = store.get("mode")
    if mode == "all":
        return repo.all_instrument_ids()
    if mode == "type":
        return repo.ids_for_types(store.get("types"))
    if mode == "select":
        return list(store.get("picked") or [])
    if mode == "holdings":
        return repo.open_position_ids()
    return []          # nothing chosen yet: load nothing


def is_chosen(store) -> bool:
    """False until the user clicks one of the universe options."""
    return bool((store or {}).get("mode"))


def default_store() -> dict:
    """First-load state: no universe chosen, so no data is loaded."""
    return {"mode": None, "picked": [], "types": []}


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
                    {"label": "Asset type", "value": "type"},
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

        # Asset-type dropdown, only visible in "Asset type" mode. Options are
        # filled the first time the mode is chosen (lazy, like the picker).
        html.Div(id="markets-types-wrap", style={"display": "none"}, children=[
            dcc.Dropdown(id="markets-types", multi=True, value=[], options=[],
                         placeholder="Choose asset types\u2026",
                         style={"fontSize": "13px"}),
        ]),

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


# Show/hide the asset-type dropdown, and fill its options on first use.
@callback(
    Output("markets-types-wrap", "style"),
    Output("markets-types", "options"),
    Input("markets-mode", "value"),
    State("markets-types", "options"),
)
def _toggle_types(mode, options):
    if mode != "type":
        return {"display": "none"}, no_update
    style = {"display": "block", "marginTop": "10px", "maxWidth": "460px"}
    if options:
        return style, no_update
    return style, [{"label": t, "value": t} for t in repo.asset_types()]


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
    Input("markets-types", "value"),
    State("markets-picker", "options"),
)
def _write_store(mode, picked, types, options):
    if mode is None:
        return default_store(), "Choose a universe to load data"
    if mode == "type":
        types = list(types or [])
        ids = repo.ids_for_types(types)
        store = {"mode": "type", "picked": [], "types": types}
        count = (f"{len(ids)} instruments" if types
                 else "Choose one or more asset types")
        return store, count
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
