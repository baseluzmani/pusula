"""
Portfolio - Inputs tab.

Everything that writes, in one place: adding and correcting transactions, and
managing cash lines. The rest of the Portfolio section is read-only.

Three fields prefill themselves once a fund, account, date and type are
chosen:

  price       the close on the trade date, in the instrument's own unit
  fx rate     the GBP cross on the trade date - this is stored on the
              transaction and fixes its cost basis permanently, so it wants
              the rate that applied then, not today's
  commission  the flat broker fee for the currency

and on a SELL the quantity prefills with the units held in that account,
which saves typing and makes an oversell obvious before it is saved.

All of them stay editable - they are starting values, not constraints.
"""

from __future__ import annotations

from datetime import date

import dash
from dash import html, dcc, callback, Input, Output, State, ctx, ALL, no_update
import pandas as pd

from core import theme
from core.repo import entry as repo
from core.repo import standing, portfolio as pf

TYPES = ("BUY", "SELL", "DIVIDEND")
CURRENCIES = ("GBP", "USD", "TRY")


def render():
    return html.Div([
        html.Div([
            html.Div("Inputs", style={"fontSize": "17px", "fontWeight": 700,
                     "color": theme.INK}),
            html.Div("Add and correct transactions, and manage cash lines. "
                     "Price, FX rate and commission fill themselves in.",
                     style={"fontSize": "12px", "color": theme.SLATE,
                            "marginTop": "2px"}),
        ], style={"marginBottom": "12px"}),

        html.Div(id="in-status", style={"marginBottom": "10px"}),

        html.Div([
            html.Div([_transaction_form(), _recent_panel()],
                     style={"flex": "1", "minWidth": 0, "marginRight": "12px"}),
            html.Div(_cash_panel(), style={"flexShrink": "0",
                     "width": "360px"}),
        ], style={"display": "flex", "alignItems": "flex-start"}),

        dcc.Store(id="in-editing", data=None),
        dcc.Store(id="in-refresh", data=0),
    ])


# --- transaction form -----------------------------------------------------

def _transaction_form():
    return html.Div([
        html.Div([
            html.Span(id="in-form-title", children="Add transaction",
                      style=theme.CARD_TITLE),
            html.Button("Cancel edit", id="in-cancel", n_clicks=0,
                        style={**_btn(), "display": "none"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "center"}),

        html.Div([
            _field("Account", dcc.Dropdown(
                id="in-account", options=standing.account_options(),
                placeholder="Account\u2026",
                style={"fontSize": "12px"}), "170px"),
            _field("Fund", dcc.Dropdown(
                id="in-fund", options=_fund_options(), placeholder="Fund\u2026",
                style={"fontSize": "12px"}), "260px"),
            _field("Date", dcc.DatePickerSingle(
                id="in-date", date=date.today().strftime("%Y-%m-%d"),
                display_format="DD MMM YYYY"), None),
            _field("Type", dcc.Dropdown(
                id="in-type", options=[{"label": t.title(), "value": t}
                                       for t in TYPES],
                value="BUY", clearable=False,
                style={"fontSize": "12px"}), "120px"),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "10px",
                  "alignItems": "flex-end", "marginTop": "10px"}),

        html.Div([
            _field("Quantity", dcc.Input(id="in-qty", type="number",
                   placeholder="0", style=_inp()), "130px"),
            _field("Price", dcc.Input(id="in-price", type="number",
                   placeholder="0.00", style=_inp()), "130px"),
            _field("Currency", dcc.Dropdown(
                id="in-currency", options=[{"label": c, "value": c}
                                           for c in CURRENCIES],
                value="GBP", clearable=False,
                style={"fontSize": "12px"}), "110px"),
            _field("FX rate", dcc.Input(id="in-fx", type="number",
                   placeholder="1.0", style=_inp()), "120px"),
            _field("Commission", dcc.Input(id="in-comm", type="number",
                   placeholder="0.00", style=_inp()), "120px"),
            html.Div(html.Button("Save transaction", id="in-save", n_clicks=0,
                     style=_btn(primary=True)),
                     style={"marginBottom": "2px"}),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "10px",
                  "alignItems": "flex-end", "marginTop": "10px"}),

        html.Div(id="in-hint", style={"fontSize": "11.5px",
                 "color": theme.NEUTRAL, "marginTop": "8px"}),
    ], style={**theme.CARD, "marginBottom": "12px"})


def _recent_panel():
    return html.Div([
        html.Div("Recent transactions", style=theme.CARD_TITLE),
        html.Div("Click a row to edit it.",
                 style={"fontSize": "11.5px", "color": theme.NEUTRAL,
                        "marginBottom": "8px"}),
        # Built at layout time rather than left for a callback to fill. A
        # panel that starts empty is indistinguishable from a broken one.
        html.Div(id="in-recent", children=_recent_table(None),
                 style={"overflowX": "auto", "maxHeight": "440px",
                        "overflowY": "auto"}),
    ], style=theme.CARD)


def _cash_panel():
    return html.Div([
        html.Div("Cash", style=theme.CARD_TITLE),
        html.Div("Add a line, remove a line - no editing in place.",
                 style={"fontSize": "11.5px", "color": theme.NEUTRAL,
                        "marginBottom": "10px"}),
        html.Div([
            dcc.Dropdown(id="in-cash-account",
                         options=standing.account_options(),
                         placeholder="Account\u2026",
                         style={"fontSize": "12px", "marginBottom": "8px"}),
            html.Div([
                dcc.Dropdown(id="in-cash-currency",
                             options=[{"label": c, "value": c}
                                      for c in CURRENCIES],
                             value="GBP", clearable=False,
                             style={"fontSize": "12px", "width": "95px"}),
                dcc.Input(id="in-cash-amount", type="number",
                          placeholder="Amount",
                          style={**_inp(), "flex": "1"}),
            ], style={"display": "flex", "gap": "8px",
                      "marginBottom": "8px"}),
            html.Button("Add cash line", id="in-cash-add", n_clicks=0,
                        style={**_btn(primary=True), "width": "100%"}),
        ]),
        html.Div(id="in-cash-list", children=_cash_table(),
                 style={"marginTop": "12px"}),
    ], style=theme.CARD)


def _field(label, control, width):
    style = {}
    if width:
        style["width"] = width
    return html.Div([
        html.Label(label, style={"display": "block", "fontSize": "10px",
                   "fontWeight": 700, "letterSpacing": "0.06em",
                   "textTransform": "uppercase", "color": theme.SLATE,
                   "marginBottom": "5px"}),
        control,
    ], style=style)


def _inp():
    return {"padding": "7px 9px", "fontSize": "12.5px", "width": "100%",
            "borderRadius": "5px", "border": f"1px solid {theme.LINE}"}


def _btn(primary=False):
    base = {"padding": "8px 16px", "borderRadius": "5px", "fontSize": "12.5px",
            "fontWeight": 600, "cursor": "pointer", "border": "none"}
    if primary:
        return {**base, "backgroundColor": theme.POSITIVE, "color": "#fff"}
    return {**base, "backgroundColor": theme.SURFACE, "color": theme.TEXT,
            "border": f"1px solid {theme.LINE}"}


def _fund_options():
    df = pf.instruments()
    if not df:
        return []
    return sorted(
        ({"label": v.get("name") or k, "value": k} for k, v in df.items()),
        key=lambda o: o["label"])


# --- prefills -------------------------------------------------------------

@callback(
    Output("in-currency", "value"), Output("in-price", "value"),
    Output("in-fx", "value"), Output("in-comm", "value"),
    Output("in-qty", "value"), Output("in-hint", "children"),
    Input("in-fund", "value"), Input("in-date", "date"),
    Input("in-type", "value"), Input("in-account", "value"),
    State("in-editing", "data"),
    prevent_initial_call=True,
)
def _prefill(fund_id, trade_date, ttype, account, editing):
    # While editing an existing row the fields hold that row's values; do not
    # overwrite them from market data.
    if editing:
        return (no_update,) * 6
    if not fund_id:
        return "GBP", None, 1.0, 4.0, no_update, ""

    inst = pf.instruments().get(fund_id, {})
    currency = inst.get("currency") or "GBP"
    price = repo.price_on(fund_id, trade_date)
    fx = repo.fx_on(currency, trade_date)
    comm = repo.default_commission(currency)

    # Only a SELL prefills the quantity. Returning None otherwise would wipe
    # a figure already typed whenever the date or account changed.
    qty, hint = no_update, []
    if price is not None:
        unit = "p" if inst.get("price_unit") == "pence" else ""
        hint.append(f"Price {price:,.4g}{unit} on {trade_date}")
    else:
        hint.append("No price found on that date - enter it manually")
    if currency != "GBP":
        hint.append(f"FX {currency}/GBP {fx:,.4f} on that date")

    if ttype == "SELL":
        held = repo.units_held(fund_id, account)
        if held > 0:
            qty = held
            hint.append(f"{held:,.4g} units held"
                        + (f" in {account}" if account else " in total"))
        else:
            hint.append("No units held in that account")

    return currency, price, fx, comm, qty, " \u00b7 ".join(hint)


# --- save -----------------------------------------------------------------

@callback(
    Output("in-status", "children"), Output("in-refresh", "data"),
    Output("in-editing", "data", allow_duplicate=True),
    Input("in-save", "n_clicks"),
    State("in-editing", "data"), State("in-fund", "value"),
    State("in-account", "value"), State("in-date", "date"),
    State("in-type", "value"), State("in-qty", "value"),
    State("in-price", "value"), State("in-currency", "value"),
    State("in-fx", "value"), State("in-comm", "value"),
    State("in-refresh", "data"),
    prevent_initial_call=True,
)
def _save(_n, editing, fund_id, account, trade_date, ttype, qty, price,
          currency, fx, comm, refresh):
    if not fund_id or not trade_date or not ttype:
        return _msg("Fund, date and type are required.", False), no_update, no_update
    if qty in (None, "") or float(qty) <= 0:
        return _msg("Quantity must be greater than zero.", False), no_update, no_update
    if ttype != "DIVIDEND" and price in (None, ""):
        return _msg("Price is required.", False), no_update, no_update
    if currency != "GBP" and not fx:
        return _msg(f"An FX rate is required for {currency} - it fixes the "
                    f"cost basis and cannot default to 1.", False), no_update, no_update

    warn = ""
    if ttype == "SELL":
        held = repo.units_held(fund_id, account)
        if float(qty) > held + 1e-9:
            warn = (f" Note: sold {float(qty):,.4g} but only {held:,.4g} "
                    f"held in that account.")

    name = pf.instruments().get(fund_id, {}).get("name", fund_id)
    try:
        if editing:
            repo.update_transaction(editing, fund_id, account, trade_date,
                                    ttype, qty, price or 1.0, currency, fx,
                                    comm)
            verb = "Updated"
        else:
            repo.add_transaction(fund_id, account, trade_date, ttype, qty,
                                 price or 1.0, currency, fx, comm)
            verb = "Added"
    except Exception as exc:                                   # noqa: BLE001
        return _msg(f"Save failed: {exc}", False), no_update, no_update

    units = repo.stored_units(fund_id)
    return (_msg(f"{verb} {ttype} {float(qty):,.4g} \u00d7 {name} "
                 f"on {trade_date}. Holding now {units:,.4g} units.{warn}",
                 not warn),
            refresh + 1, None)


# --- recent list, selection, delete ---------------------------------------

@callback(Output("in-recent", "children"),
          Input("in-refresh", "data"), Input("in-editing", "data"))
def _recent(_refresh, editing):
    return _recent_table(editing)


def _recent_table(editing):
    df = repo.recent_transactions(30)
    if df.empty:
        return html.P("No transactions yet.",
                      style={"color": theme.NEUTRAL, "fontSize": "12px"})

    head = html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "5px 8px", "fontSize": "10px", "fontWeight": 600,
                "textAlign": "left" if i < 3 else "right",
                "whiteSpace": "nowrap", "position": "sticky", "top": 0})
        for i, c in enumerate(["Date", "Account", "Fund", "Type", "Qty",
                               "Price", "Comm", ""])]))

    rows = []
    for r in df.to_dict("records"):
        selected = editing == r["id"]
        name = r["name"]
        disp = name if len(name) <= 26 else name[:26] + "\u2026"
        colour = {"BUY": "#2E6FB5", "SELL": theme.NEGATIVE,
                  "DIVIDEND": theme.POSITIVE}.get(r["type"], theme.TEXT)
        rows.append(html.Tr([
            html.Td(pd.Timestamp(r["trade_date"]).strftime("%d %b %y"),
                    style=_td(theme.SLATE, "left")),
            html.Td(r["account"] or "\u2014", style=_td(theme.SLATE, "left")),
            html.Td(html.Span(disp, title=name), style=_td(theme.INK, "left")),
            html.Td(r["type"], style={**_td(colour), "fontWeight": 600,
                    "textAlign": "left"}),
            html.Td(f"{r['quantity']:,.4g}", style=_td(theme.TEXT)),
            html.Td(f"{r['price']:,.4g}", style=_td(theme.SLATE)),
            html.Td(f"{r['commission']:,.2f}" if r["commission"]
                    else "\u2014", style=_td(theme.NEUTRAL)),
            html.Td(html.Button("Delete",
                    id={"type": "in-del", "id": r["id"]}, n_clicks=0,
                    style={"background": "none", "border": "none",
                           "color": theme.NEGATIVE, "fontSize": "11px",
                           "cursor": "pointer", "padding": "0"}),
                    style=_td(theme.NEUTRAL)),
        ], id={"type": "in-row", "id": r["id"]}, n_clicks=0, style={
            "cursor": "pointer",
            "background": "#EEF4FB" if selected else "transparent",
            "borderBottom": f"1px solid {theme.LINE}"}))

    return html.Table([head, html.Tbody(rows)],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _td(colour, align="right"):
    return {"padding": "4px 8px", "fontSize": "11px", "textAlign": align,
            **theme.NUM, "color": colour, "whiteSpace": "nowrap"}


@callback(
    Output("in-editing", "data"),
    Output("in-fund", "value"), Output("in-account", "value"),
    Output("in-date", "date"), Output("in-type", "value"),
    Output("in-qty", "value", allow_duplicate=True),
    Output("in-price", "value", allow_duplicate=True),
    Output("in-currency", "value", allow_duplicate=True),
    Output("in-fx", "value", allow_duplicate=True),
    Output("in-comm", "value", allow_duplicate=True),
    Output("in-form-title", "children"), Output("in-cancel", "style"),
    Input({"type": "in-row", "id": ALL}, "n_clicks"),
    Input("in-cancel", "n_clicks"),
    prevent_initial_call=True,
)
def _select(row_clicks, _cancel):
    trigger = ctx.triggered_id
    blank = (None, None, None, date.today().strftime("%Y-%m-%d"), "BUY",
             None, None, "GBP", 1.0, 4.0, "Add transaction",
             {**_btn(), "display": "none"})

    if trigger == "in-cancel" or not trigger:
        return blank
    if not any(row_clicks or []):
        return (no_update,) * 12

    t = repo.transaction(trigger["id"])
    if not t:
        return blank
    return (t["id"], t["fund_id"], t["account"] or None, t["trade_date"],
            t["type"], t["quantity"], t["price"], t["currency"] or "GBP",
            t["fx_rate"], t["commission"], "Edit transaction",
            {**_btn(), "display": "inline-block"})


@callback(
    Output("in-status", "children", allow_duplicate=True),
    Output("in-refresh", "data", allow_duplicate=True),
    Input({"type": "in-del", "id": ALL}, "n_clicks"),
    State("in-refresh", "data"),
    prevent_initial_call=True,
)
def _delete(clicks, refresh):
    if not any(clicks or []):
        return no_update, no_update
    trigger = ctx.triggered_id
    if not trigger:
        return no_update, no_update
    fund_id = repo.delete_transaction(trigger["id"])
    if not fund_id:
        return _msg("That transaction no longer exists.", False), no_update
    units = repo.stored_units(fund_id)
    name = pf.instruments().get(fund_id, {}).get("name", fund_id)
    return (_msg(f"Deleted. {name} holding now {units:,.4g} units."),
            refresh + 1)


# --- cash -----------------------------------------------------------------

@callback(Output("in-cash-list", "children"), Input("in-refresh", "data"))
def _cash_list(_refresh):
    return _cash_table()


def _cash_table():
    df = repo.cash_accounts()
    if df.empty:
        return html.P("No cash lines.", style={"color": theme.NEUTRAL,
                      "fontSize": "12px"})
    rows = []
    for r in df.to_dict("records"):
        rows.append(html.Tr([
            html.Td(r["name"] or "\u2014", style={"padding": "4px 6px",
                    "fontSize": "11.5px", "color": theme.INK}),
            html.Td(r["currency"], style={"padding": "4px 6px",
                    "fontSize": "10px", "color": theme.SLATE}),
            html.Td(f"{r['amount']:,.0f}", style={"padding": "4px 6px",
                    "fontSize": "11.5px", "textAlign": "right", **theme.NUM,
                    "fontWeight": 600, "color": theme.INK}),
            html.Td(html.Button("\u00d7",
                    id={"type": "in-cash-del", "id": r["id"]}, n_clicks=0,
                    style={"background": "none", "border": "none",
                           "color": theme.NEGATIVE, "fontSize": "15px",
                           "cursor": "pointer", "padding": "0 4px"}),
                    style={"width": "24px"}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))
    return html.Table(html.Tbody(rows),
                      style={"width": "100%", "borderCollapse": "collapse"})


@callback(
    Output("in-status", "children", allow_duplicate=True),
    Output("in-refresh", "data", allow_duplicate=True),
    Output("in-cash-amount", "value"),
    Input("in-cash-add", "n_clicks"),
    State("in-cash-account", "value"), State("in-cash-currency", "value"),
    State("in-cash-amount", "value"), State("in-refresh", "data"),
    prevent_initial_call=True,
)
def _add_cash(_n, name, currency, amount, refresh):
    if not name or amount in (None, ""):
        return _msg("Cash needs an account and an amount.", False), no_update, no_update
    repo.add_cash(name, currency or "GBP", amount)
    return (_msg(f"Added {currency} {float(amount):,.0f} to {name}."),
            refresh + 1, None)


@callback(
    Output("in-status", "children", allow_duplicate=True),
    Output("in-refresh", "data", allow_duplicate=True),
    Input({"type": "in-cash-del", "id": ALL}, "n_clicks"),
    State("in-refresh", "data"),
    prevent_initial_call=True,
)
def _delete_cash(clicks, refresh):
    if not any(clicks or []):
        return no_update, no_update
    trigger = ctx.triggered_id
    if not trigger:
        return no_update, no_update
    repo.delete_cash(trigger["id"])
    return _msg("Cash line removed."), refresh + 1


def _msg(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEEDLE
    return html.Div(text, style={
        "padding": "9px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour,
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})
