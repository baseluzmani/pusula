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

import base64
from datetime import date

import dash
from dash import html, dcc, callback, Input, Output, State, ctx, ALL, no_update
import pandas as pd

from core import background, theme
from core.repo import entry as repo
from core.repo import pension_import as pen
from core.repo import standing, portfolio as pf
from ui.help import help_title, help_icon

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

        _pension_panel(),

        dcc.Store(id="in-editing", data=None),
        dcc.Store(id="in-refresh", data=0),
        dcc.Store(id="in-pen-data", data=None),
        dcc.Store(id="in-pen-close", data=[]),
        dcc.Store(id="in-pen-maprev", data=0),
    ])


# --- transaction form -----------------------------------------------------

def _transaction_form():
    return html.Div([
        html.Div([
            html.Div([
                html.Span(id="in-form-title", children="Add transaction",
                          style={**theme.CARD_TITLE, "marginBottom": 0}),
                help_icon("portfolio.inputs.form"),
            ], style={"display": "flex", "alignItems": "center",
                      "gap": "6px"}),
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
        help_title("Recent transactions", "portfolio.inputs.recent"),
        html.Div("Click a row to edit it. Shows the latest 30, or up to 200 "
                 "once a filter is set.",
                 style={"fontSize": "11.5px", "color": theme.NEUTRAL,
                        "marginBottom": "8px"}),
        html.Div([
            _field("Fund", dcc.Dropdown(
                id="in-f-fund", options=_fund_options(),
                placeholder="All funds", style={"fontSize": "12px"}), "260px"),
            _field("Account", dcc.Dropdown(
                id="in-f-account",
                options=standing.account_options(active_only=False),
                placeholder="All accounts",
                style={"fontSize": "12px"}), "170px"),
            _field("Type", dcc.Dropdown(
                id="in-f-type", options=[{"label": t.title(), "value": t}
                                         for t in TYPES],
                placeholder="All types",
                style={"fontSize": "12px"}), "130px"),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "10px",
                  "alignItems": "flex-end", "marginBottom": "10px"}),
        # Built at layout time rather than left for a callback to fill. A
        # panel that starts empty is indistinguishable from a broken one.
        html.Div(id="in-recent", children=_recent_table(None),
                 style={"overflowX": "auto", "maxHeight": "440px",
                        "overflowY": "auto"}),
    ], style=theme.CARD)


def _cash_panel():
    return html.Div([
        help_title("Cash", "portfolio.inputs.cash"),
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
          Input("in-refresh", "data"), Input("in-editing", "data"),
          Input("in-f-fund", "value"), Input("in-f-account", "value"),
          Input("in-f-type", "value"))
def _recent(_refresh, editing, fund, account, ttype):
    return _recent_table(editing, fund, account, ttype)


def _recent_table(editing, fund=None, account=None, ttype=None):
    filtered = bool(fund or account or ttype)
    df = repo.recent_transactions(200 if filtered else 30,
                                  fund_id=fund, account=account, ttype=ttype)
    if df.empty:
        return html.P("No transactions match these filters." if filtered
                      else "No transactions yet.",
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


# --- pension statement import ---------------------------------------------
#
# One CSV from the pension provider - a switch or a monthly contribution -
# becomes a set of composite BUY/SELL transactions. The logic lives in
# core/repo/pension_import.py; this is the upload, the preview and the button.

def _pension_panel():
    accounts = standing.account_options()
    default = (pen.DEFAULT_ACCOUNT
               if any(o["value"] == pen.DEFAULT_ACCOUNT for o in accounts)
               else None)
    return html.Div([
        help_title("Pension statement import", "portfolio.inputs.pension"),
        html.Div("Upload the provider's transaction-details CSV - a switch "
                 "or a monthly contribution. Each fund's £ value is "
                 "turned into units at Pusula's own composite price. "
                 "Nothing is written until you press Import.",
                 style={"fontSize": "11.5px", "color": theme.NEUTRAL,
                        "marginBottom": "10px"}),
        html.Div([
            dcc.Upload(
                id="in-pen-upload", multiple=False, accept=".csv",
                children=html.Div(["Drop the CSV here or ",
                                   html.Span("choose a file", style={
                                       "color": "#2E6FB5",
                                       "textDecoration": "underline"})]),
                style={"width": "260px", "padding": "14px 12px",
                       "border": f"1px dashed {theme.NEUTRAL}",
                       "borderRadius": "6px", "textAlign": "center",
                       "fontSize": "12.5px", "color": theme.SLATE,
                       "cursor": "pointer", "background": theme.CANVAS}),
            _field("Account", dcc.Dropdown(
                id="in-pen-account", options=accounts, value=default,
                placeholder="Account…", clearable=False,
                style={"fontSize": "12px"}), "170px"),
            _field("Trade date", dcc.DatePickerSingle(
                id="in-pen-date", date=None, placeholder="From the file",
                display_format="DD MMM YYYY"), None),
        ], style={"display": "flex", "flexWrap": "wrap", "gap": "12px",
                  "alignItems": "flex-end"}),
        html.Div(id="in-pen-preview", style={"marginTop": "12px",
                                             "overflowX": "auto"}),
    ], style={**theme.CARD, "marginTop": "12px"})


def _decode(contents: str) -> str:
    raw = base64.b64decode(contents.split(",", 1)[1])
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


@callback(
    Output("in-pen-data", "data"), Output("in-pen-date", "date"),
    Output("in-pen-close", "data", allow_duplicate=True),
    Input("in-pen-upload", "contents"), State("in-pen-upload", "filename"),
    prevent_initial_call=True,
)
def _pen_upload(contents, filename):
    if not contents:
        return no_update, no_update, no_update
    rows, problems = pen.parse_csv(_decode(contents))
    net, trade_date = pen.aggregate(rows)
    data = {"file": filename, "rows": len(rows), "problems": problems,
            "net": [[k, v] for k, v in net.items()]}
    return data, trade_date or no_update, []


@callback(
    Output("in-pen-preview", "children"),
    Input("in-pen-data", "data"), Input("in-pen-account", "value"),
    Input("in-pen-date", "date"), Input("in-pen-close", "data"),
    Input("in-pen-maprev", "data"), Input("in-refresh", "data"),
)
def _pen_preview(data, account, trade_date, close, _rev, _refresh):
    if not data:
        return None
    if not data["net"]:
        return html.P(f"No fund rows found in {data['file']}.",
                      style={"color": theme.NEGATIVE, "fontSize": "12px"})
    if not trade_date:
        return html.P("Pick a trade date.",
                      style={"color": theme.NEUTRAL, "fontSize": "12px"})
    p = pen.plan(dict(data["net"]), trade_date, account, close or [])
    return _pen_table(p, data)


def _pen_table(p, data):
    names = {k: v.get("name") or k for k, v in pf.instruments().items()}
    options = None                                  # built only if needed

    head = html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "5px 8px", "fontSize": "10px", "fontWeight": 600,
                "textAlign": "left" if i < 2 or i == 7 else "right",
                "whiteSpace": "nowrap"})
        for i, c in enumerate(["Provider fund", "Composite", "£ (file)",
                               "Price", "Units", "Type", "Close fully",
                               "Notes"])]))

    rows = []
    for l in p["lines"]:
        if l["fund_id"]:
            comp = html.Span(names.get(l["fund_id"], l["fund_id"]),
                             title=l["fund_id"])
        else:
            options = options or pen.composite_options()
            comp = dcc.Dropdown(id={"type": "in-pen-map", "name": l["name"]},
                                options=options, placeholder="Map to…",
                                style={"fontSize": "11px", "width": "220px"})

        value = f"{l['value']:,.2f}"
        if abs(l["value_used"] - l["value"]) >= 0.005:
            value = html.Span([value, html.Br(), html.Span(
                f"{l['value_used']:,.2f} used",
                style={"color": theme.NEEDLE, "fontSize": "10px"})])

        price = (html.Span([f"{l['price']:,.4f}", html.Span(
            f"  {pd.Timestamp(l['price_date']):%d %b}",
            style={"color": theme.NEUTRAL, "fontSize": "10px"})])
            if l["price"] else "—")

        close = ""
        if l["type"] == "SELL" and l["fund_id"] and l["price"]:
            close = dcc.Checklist(
                id={"type": "in-pen-closebox", "fund": l["fund_id"]},
                options=[{"label": "", "value": "close"}],
                value=["close"] if l["close"] else [],
                style={"display": "inline-block"})

        colour = "#2E6FB5" if l["type"] == "BUY" else theme.NEGATIVE
        rows.append(html.Tr([
            html.Td(l["name"], style=_td(theme.INK, "left")),
            html.Td(comp, style=_td(theme.SLATE, "left")),
            html.Td(value, style=_td(theme.TEXT)),
            html.Td(price, style=_td(theme.SLATE)),
            html.Td(f"{l['quantity']:,.4f}" if l["quantity"] else "—",
                    style=_td(theme.TEXT)),
            html.Td(l["type"], style={**_td(colour), "fontWeight": 600}),
            html.Td(close, style=_td(theme.TEXT)),
            html.Td(" · ".join(l["warnings"]),
                    style={**_td(theme.NEGATIVE, "left"),
                           "whiteSpace": "normal", "minWidth": "220px"}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))

    rows.append(html.Tr([
        html.Td("Net", style={**_td(theme.INK, "left"), "fontWeight": 700}),
        html.Td(""),
        html.Td(f"{p['net']:,.2f}", style={**_td(theme.INK),
                                            "fontWeight": 700}),
        html.Td(""), html.Td(""), html.Td(""), html.Td(""), html.Td(""),
    ]))

    kind = ("Contribution" if all(l["type"] == "BUY" for l in p["lines"])
            else "Switch")
    info = (f"{kind} · {data['rows']} rows from {data['file']} netted "
            f"into {len(p['lines'])} funds")
    notes = [html.Div(info, style={"fontSize": "11.5px", "color": theme.SLATE,
                                   "marginBottom": "8px"})]
    for msg in data.get("problems", []) + p["blocking"]:
        notes.append(html.Div(msg, style={"fontSize": "11.5px",
                     "color": theme.NEGATIVE, "marginBottom": "4px"}))

    blocked = bool(p["blocking"])
    button = html.Button(
        f"Import {len(p['lines'])} transactions", id="in-pen-import",
        n_clicks=0, disabled=blocked,
        style={**_btn(primary=not blocked), "marginTop": "10px",
               **({"cursor": "not-allowed", "opacity": 0.6}
                  if blocked else {})})

    return html.Div([
        *notes,
        html.Table([head, html.Tbody(rows)],
                   style={"width": "100%", "borderCollapse": "collapse"}),
        button,
    ])


@callback(
    Output("in-pen-close", "data"),
    Input({"type": "in-pen-closebox", "fund": ALL}, "value"),
    State({"type": "in-pen-closebox", "fund": ALL}, "id"),
    State("in-pen-close", "data"),
    prevent_initial_call=True,
)
def _pen_close(values, ids, current):
    # The preview redraws these boxes every time; returning no_update when
    # nothing changed stops a redraw from triggering another redraw.
    chosen = sorted(i["fund"] for v, i in zip(values, ids) if v)
    if chosen == sorted(current or []):
        return no_update
    return chosen


@callback(
    Output("in-pen-maprev", "data"),
    Input({"type": "in-pen-map", "name": ALL}, "value"),
    State({"type": "in-pen-map", "name": ALL}, "id"),
    State("in-pen-maprev", "data"),
    prevent_initial_call=True,
)
def _pen_map(values, ids, rev):
    saved = 0
    for value, ident in zip(values, ids):
        if value:
            pen.save_mapping(ident["name"], value)
            saved += 1
    return (rev or 0) + 1 if saved else no_update


@callback(
    Output("in-status", "children", allow_duplicate=True),
    Output("in-refresh", "data", allow_duplicate=True),
    Output("in-pen-data", "data", allow_duplicate=True),
    Output("in-pen-close", "data", allow_duplicate=True),
    Input("in-pen-import", "n_clicks"),
    State("in-pen-data", "data"), State("in-pen-account", "value"),
    State("in-pen-date", "date"), State("in-pen-close", "data"),
    State("in-refresh", "data"),
    prevent_initial_call=True,
)
def _pen_import(n, data, account, trade_date, close, refresh):
    if not n or not data or not trade_date:
        return (no_update,) * 4
    # Planned again from the stored file rather than trusting what is on
    # screen, so the write always matches the current prices and holdings.
    p = pen.plan(dict(data["net"]), trade_date, account, close or [])
    try:
        result = pen.write(p)
    except Exception as exc:                                   # noqa: BLE001
        return (_msg(f"Import failed: {exc}", False), no_update, no_update,
                no_update)

    background.start(["scripts/rebuild_history.py", "--apply"],
                     "rebuild_history")
    backup = result["backup"].rsplit("/", 1)[-1]
    return (_msg(f"Imported {result['written']} transactions on "
                 f"{trade_date} (net £{p['net']:,.2f}). Backup "
                 f"{backup}. Snapshot history is rebuilding in the "
                 f"background."),
            (refresh or 0) + 1, None, [])


def _msg(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEEDLE
    return html.Div(text, style={
        "padding": "9px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour,
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})
