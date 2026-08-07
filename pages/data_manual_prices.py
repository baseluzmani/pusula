"""
Data -> Manual prices.

For holdings nothing fetches: the house, valued from Zoopla once a month, and
the mortgage, read off a statement. Enter a value with its date and it fills
forward until the next entry.

The two are separate instruments deliberately. Netted into one line their
drivers contaminate each other - a month where the valuation falls and the
balance also falls shows a loss that is partly a saving. Kept apart, each
series means one thing. The reports still show them merged, because both sit
in the Property category.
"""

from datetime import date

from dash import html, dcc, callback, Input, Output, State, no_update

from core import theme, manual_prices as repo
from ui.layout import card

BTN = {"padding": "7px 14px", "border": f"1px solid {theme.LINE}",
       "borderRadius": "3px", "background": theme.SURFACE, "cursor": "pointer",
       "fontSize": "12.5px", "fontWeight": 500, "color": theme.INK,
       "marginRight": "8px"}


def _th(text, align="left"):
    return html.Th(text, style={
        "padding": "6px 10px", "fontSize": "11px", "fontWeight": 600,
        "textTransform": "uppercase", "letterSpacing": "0.05em",
        "color": theme.SLATE, "textAlign": align, "whiteSpace": "nowrap",
        "borderBottom": f"1px solid {theme.LINE}"})


def render():
    repo.ensure_table()
    df = repo.instruments()

    options = [{"label": f"{r['name']} ({r['fund_id']})",
                "value": r["fund_id"]} for r in df.to_dict("records")]

    rows = []
    for r in df.to_dict("records"):
        _d, value = repo.latest(r["fund_id"])
        units = r.get("units")
        signed = (value * units) if (value is not None and units) else None
        rows.append(html.Tr([
            html.Td(r["name"], style={"padding": "6px 10px",
                                      "fontSize": "12.5px"}),
            html.Td(f"{units:,.0f}" if units is not None else "—",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "textAlign": "right", **theme.NUM,
                           "color": theme.SLATE}),
            html.Td(f"£{value:,.2f}" if value is not None else "—",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "textAlign": "right", **theme.NUM}),
            html.Td(f"£{signed:,.0f}" if signed is not None else "—",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "textAlign": "right", **theme.NUM,
                           "fontWeight": 600,
                           "color": theme.NEGATIVE if (signed or 0) < 0
                                    else theme.INK}),
            html.Td(r.get("last_entry") or "never",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "color": theme.SLATE, **theme.NUM}),
            html.Td(f"{r.get('entries') or 0}",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "textAlign": "right", "color": theme.SLATE,
                           **theme.NUM}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))

    return html.Div([
        html.Div(id="mp-msg"),

        card("Manually priced holdings", html.Div([
            html.Table([
                html.Thead(html.Tr([
                    _th("Holding"), _th("Units", "right"),
                    _th("Latest price", "right"), _th("Value", "right"),
                    _th("Last entry"), _th("Entries", "right")])),
                html.Tbody(rows),
            ], style={"width": "100%", "borderCollapse": "collapse"}),
            html.Div("Units are fixed: 1 for the house, -1 for the mortgage, "
                     "so the price is the value. Enter the mortgage balance "
                     "as a positive number - the sign comes from the units.",
                     style={"color": theme.SLATE, "fontSize": "12px",
                            "marginTop": "10px"}),
        ])),

        card("Record a value", html.Div([
            html.Div([
                html.Div(dcc.Dropdown(id="mp-fund", options=options,
                                      placeholder="Choose a holding…",
                                      clearable=False,
                                      style={"fontSize": "12.5px"}),
                         style={"flex": "1", "minWidth": 0,
                                "marginRight": "10px"}),
                dcc.DatePickerSingle(id="mp-date",
                                     date=date.today().isoformat(),
                                     display_format="DD MMM YYYY",
                                     style={"marginRight": "10px"}),
                dcc.Input(id="mp-value", type="number", placeholder="Value £",
                          style={"padding": "6px 9px", "width": "150px",
                                 "marginRight": "10px"}),
                dcc.Input(id="mp-note", type="text", placeholder="Note",
                          style={"padding": "6px 9px", "width": "180px",
                                 "marginRight": "10px"}),
                html.Button("Save", id="mp-save", n_clicks=0,
                            style={**BTN, "marginRight": "0"}),
            ], style={"display": "flex", "alignItems": "center"}),
            html.Div("The value fills forward until the next entry, or to "
                     "today if there is none. A back-dated entry corrects only "
                     "its own window.",
                     style={"color": theme.SLATE, "fontSize": "12px",
                            "marginTop": "10px"}),
        ])),

        html.Div(id="mp-history"),
    ])


def _history(fund_id):
    df = repo.entries(fund_id)
    if df.empty:
        return card("History", html.Div(
            "No entries yet.",
            style={"color": theme.SLATE, "fontSize": "13px"}))

    rows = []
    for r in df.to_dict("records"):
        rows.append(html.Tr([
            html.Td(r["date"], style={"padding": "5px 10px",
                                      "fontSize": "12.5px", **theme.NUM}),
            html.Td(f"£{r['value']:,.2f}",
                    style={"padding": "5px 10px", "fontSize": "12.5px",
                           "textAlign": "right", **theme.NUM}),
            html.Td(r.get("note") or "",
                    style={"padding": "5px 10px", "fontSize": "12px",
                           "color": theme.SLATE}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))

    return card(f"History — {fund_id}", html.Div([
        html.Table([
            html.Thead(html.Tr([_th("Date"), _th("Value", "right"),
                                _th("Note")])),
            html.Tbody(rows),
        ], style={"width": "100%", "borderCollapse": "collapse"}),
        html.Div([
            html.Button("Rebuild price series", id="mp-rebuild", n_clicks=0,
                        style={**BTN, "marginTop": "12px"}),
        ]),
        html.Div("Rebuild regenerates every daily price row from these "
                 "entries. Use it if the series and the entries disagree.",
                 style={"color": theme.SLATE, "fontSize": "12px",
                        "marginTop": "8px"}),
    ]))


def _flash(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEGATIVE
    return html.Div(text, style={
        "padding": "10px 14px", "marginBottom": "12px", "borderRadius": "3px",
        "fontSize": "13px", "color": colour,
        "border": f"1px solid {colour}33", "background": f"{colour}0D"})


@callback(Output("mp-history", "children"), Input("mp-fund", "value"))
def show_history(fund_id):
    if not fund_id:
        return html.Div()
    return _history(fund_id)


@callback(
    Output("mp-msg", "children"),
    Output("mp-history", "children", allow_duplicate=True),
    Input("mp-save", "n_clicks"),
    Input("mp-rebuild", "n_clicks"),
    State("mp-fund", "value"),
    State("mp-date", "date"),
    State("mp-value", "value"),
    State("mp-note", "value"),
    prevent_initial_call=True,
)
def save(_save, _rebuild, fund_id, on_date, value, note):
    from dash import ctx

    if not fund_id:
        return _flash("Choose a holding first.", ok=False), no_update

    if ctx.triggered_id == "mp-rebuild":
        n = repo.rebuild(fund_id)
        return (_flash(f"Rebuilt {fund_id} from {n} entries."),
                _history(fund_id))

    if value is None or on_date is None:
        return _flash("A date and a value are both required.",
                      ok=False), no_update

    try:
        val = float(value)
    except (TypeError, ValueError):
        return _flash("Value must be a number.", ok=False), no_update

    if val < 0:
        return _flash("Enter a positive figure. The mortgage is negative "
                      "because its units are -1, not its price.",
                      ok=False), no_update

    repo.set_price(fund_id, on_date[:10], val, (note or "").strip() or None)
    return (_flash(f"Saved £{val:,.2f} for {fund_id} on {on_date[:10]}, "
                   f"filled forward."),
            _history(fund_id))
