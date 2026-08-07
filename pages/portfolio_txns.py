"""
Portfolio - Transactions tab.

A filterable ledger of every trade, with live pricing per row: what the
position cost in GBP, what it is worth now, and the resulting P&L. Read-only -
entering transactions lives on the Portfolio tab.

Filters: fund (multi-select), date from (defaults to the start of the current
month), date to, and type.

Performance
-----------
The legacy tab called get_latest_price(df_combined, fund_id) once per row, and
each call scanned the whole ~194k-row price frame. With no start date that is
~570 full scans, which is why it crawled. Here every fund's latest price is
resolved once via core.finance.latest_price_map and each row does a dict
lookup instead.

Commission
----------
Commission is included in Cost GBP - it raises the cost of a BUY and reduces
the proceeds of a SELL - and also shown in its own column, converted to GBP,
so you can see how much of the cost was fees. The two are not additive: the
Commission column is a breakdown of Cost GBP, not an extra charge on top.
Rows predating the commission column carry 0, so their figures are unchanged.
"""

from __future__ import annotations

from datetime import date

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd

from core import theme, finance as fin
from core.repo import portfolio as repo

TYPE_COLOURS = {"BUY": "#2E6FB5", "SELL": theme.NEGATIVE,
                "DIVIDEND": theme.POSITIVE}

COLUMNS = ["Date", "Fund", "Type", "Qty", "Price", "Cost GBP", "Commission",
           "Latest price", "Value GBP", "P&L GBP", "P&L %"]


def _month_start() -> str:
    return date.today().replace(day=1).strftime("%Y-%m-%d")


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Transactions", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Every trade with its cost and current value. "
                         "Commission is included in Cost GBP.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
        ], style={"marginBottom": "12px"}),

        html.Div([
            _field("Fund", dcc.Dropdown(
                id="ptx-fund", options=repo.transaction_fund_options(),
                multi=True, placeholder="All funds\u2026",
                style={"fontSize": "12px", "minWidth": "260px"}), "260px"),
            _field("From", dcc.DatePickerSingle(
                id="ptx-from", date=_month_start(),
                display_format="DD MMM YYYY"), None),
            _field("To", dcc.DatePickerSingle(
                id="ptx-to", display_format="DD MMM YYYY",
                placeholder="End date"), None),
            _field("Type", dcc.Dropdown(
                id="ptx-type", clearable=False, value="ALL",
                options=[{"label": t.title() if t != "ALL" else "All",
                          "value": t}
                         for t in ("ALL", "BUY", "SELL", "DIVIDEND")],
                style={"fontSize": "12px", "width": "120px"}), "120px"),
        ], style={**theme.CARD, "display": "flex", "alignItems": "flex-end",
                  "flexWrap": "wrap", "gap": "14px", "marginBottom": "12px"}),

        html.Div(html.Div(id="ptx-table"),
                 style={**theme.CARD, "padding": "0", "overflow": "auto",
                        "maxHeight": "calc(100vh - 250px)"}),
    ])


def _field(label, control, width):
    style = {"marginRight": "4px"}
    if width:
        style["width"] = width
    return html.Div([
        html.Label(label, style={"display": "block", "fontSize": "10px",
                   "fontWeight": 700, "letterSpacing": "0.06em",
                   "textTransform": "uppercase", "color": theme.SLATE,
                   "marginBottom": "5px"}),
        control,
    ], style=style)


@callback(
    Output("ptx-table", "children"),
    Input("ptx-fund", "value"), Input("ptx-from", "date"),
    Input("ptx-to", "date"), Input("ptx-type", "value"),
)
def _table(funds, date_from, date_to, txn_type):
    txns = repo.transactions_filtered(funds or None, date_from, date_to,
                                      txn_type)
    if txns.empty:
        return _msg("No transactions match these filters.")

    instruments = repo.instruments()
    prices = repo.latest_prices()
    rates = fin.fx_rates(prices)
    gbpusd = rates["USD"]
    # One pass for every fund's latest close, instead of a full scan per row.
    price_map = fin.latest_price_map(prices)

    rows, totals = [], {"cost": 0.0, "comm": 0.0, "value": 0.0,
                        "pnl": 0.0}
    for t in txns.to_dict("records"):
        rows.append(_row(t, instruments, price_map, gbpusd, rates, totals))

    rows.append(_totals_row(totals))
    return html.Table([_header(), html.Tbody(rows)],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _latest(fid, instruments, price_map, gbpusd, rates, punit, curr):
    """
    Latest price for one fund as (display_string, gbp_value).

    Composites need no special case any more: importers/composites.py writes a
    real GBP price into prices, and their instruments row says GBP and 'pound',
    so the generic path below handles them. They used to be valued here from
    their components, which was one of two competing pricings of the same
    thing.

    Cash and fixed assets are worth their face value.
    """
    if fid.startswith(("CASH:", "ASSET:")):
        return "\u2014", 1.0
    raw = price_map.get(fid)
    if raw is None:
        return "\u2014", None
    return _native(raw, punit, curr), fin.to_gbp(raw, punit, curr, gbpusd, rates)


def _header():
    return html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "6px 10px", "fontSize": "10px", "fontWeight": 600,
                "textAlign": "left" if i < 2 else "right",
                "whiteSpace": "nowrap", "position": "sticky", "top": 0,
                "zIndex": 1})
        for i, c in enumerate(COLUMNS)]))


def _row(t, instruments, price_map, gbpusd, rates, totals):
    fid = t["fund_id"]
    qty = float(t["quantity"])
    price = float(t["price"])
    fx = float(t["fx_rate"]) if t["fx_rate"] else 1.0
    ttype = t["type"]

    inst = instruments.get(fid, {})
    curr = inst.get("currency", t["currency"] or "GBP")
    punit = inst.get("price_unit", t["price_unit"] or "pound")

    cost_per_unit = fin.txn_price_to_gbp(price, t["currency"], fx, punit)
    comm = fin.commission_to_gbp(t.get("commission", 0.0), t["currency"], fx)

    latest_str, latest_gbp = _latest(fid, instruments, price_map, gbpusd,
                                     rates, punit, curr)

    signed_qty, signed_cost, signed_value, pnl = _signed(
        ttype, qty, cost_per_unit, comm, latest_gbp)

    totals["cost"] += signed_cost
    totals["comm"] += comm
    if signed_value is not None:
        totals["value"] += signed_value
    if pnl is not None:
        totals["pnl"] += pnl

    name = t["name"] or fid
    disp = name if len(name) <= 30 else name[:30] + "\u2026"
    pnl_col = theme.POSITIVE if (pnl or 0) >= 0 else theme.NEGATIVE
    cost_col = theme.POSITIVE if signed_cost >= 0 else theme.NEGATIVE
    val_col = theme.POSITIVE if (signed_value or 0) >= 0 else theme.NEGATIVE
    pct = (pnl / abs(signed_cost) * 100) if (pnl is not None and signed_cost) else None

    return html.Tr([
        _td(pd.Timestamp(t["trade_date"]).strftime("%Y-%m-%d"),
            colour=theme.SLATE, align="left", mono=False),
        html.Td(html.Span(disp, title=name),
                style={"padding": "4px 10px", "fontSize": "12px",
                       "color": theme.INK, "whiteSpace": "nowrap"}),
        _td(ttype, colour=TYPE_COLOURS.get(ttype, theme.TEXT), weight=600,
            mono=False),
        _td(_fmt_qty(signed_qty), colour=theme.SLATE),
        _td(_native(price, punit, curr), colour=theme.SLATE),
        _td(_fmt_signed(signed_cost), colour=cost_col, weight=600),
        _td(f"{comm:,.2f}" if comm else "\u2014", colour=theme.NEUTRAL),
        _td(latest_str, colour=theme.SLATE),
        _td(_fmt_signed(signed_value), colour=val_col),
        _td(_fmt_signed(pnl), colour=pnl_col, weight=700),
        _td(f"{pct:+.1f}%" if pct is not None else "\u2014",
            colour=pnl_col, weight=700),
    ], style={"borderBottom": f"1px solid {theme.LINE}"})


def _signed(ttype, qty, cost_per_unit, comm, latest_gbp):
    """
    Cash-flow view of one transaction, in GBP.

    Cost is negative for money out (a buy) and positive for money in (a sell
    or dividend). Commission always works against you: it deepens the outflow
    on a buy and trims the inflow on a sell.
    """
    if ttype == "BUY":
        signed_qty = qty
        signed_cost = -(qty * cost_per_unit + comm)
        signed_value = latest_gbp * qty if latest_gbp is not None else None
        pnl = (signed_value + signed_cost) if signed_value is not None else None
    elif ttype == "SELL":
        signed_qty = -qty
        signed_cost = qty * cost_per_unit - comm
        signed_value = -latest_gbp * qty if latest_gbp is not None else None
        # How much better off the sale left you versus holding on.
        pnl = (cost_per_unit - (latest_gbp or cost_per_unit)) * qty - comm
    elif ttype == "DIVIDEND":
        signed_qty = None
        signed_cost = qty * cost_per_unit
        signed_value = None
        pnl = signed_cost
    else:
        signed_qty = qty
        signed_cost = -(qty * cost_per_unit + comm)
        signed_value = latest_gbp * qty if latest_gbp is not None else None
        pnl = None
    return signed_qty, signed_cost, signed_value, pnl


def _totals_row(totals):
    cost, value, pnl = totals["cost"], totals["value"], totals["pnl"]
    comm = totals["comm"]
    pct = (pnl / abs(cost) * 100) if cost else None
    base = {"padding": "7px 10px", "fontSize": "12px", "textAlign": "right",
            **theme.NUM, "fontWeight": 700,
            "borderTop": f"2px solid {theme.INK}"}
    return html.Tr([
        html.Td("TOTAL", colSpan=5, style={**base, "textAlign": "left"}),
        html.Td(f"{cost:+,.0f}", style={**base, "color":
                theme.POSITIVE if cost >= 0 else theme.NEGATIVE}),
        html.Td(f"{comm:,.2f}", style={**base, "color": theme.SLATE}),
        html.Td("", style={"borderTop": f"2px solid {theme.INK}"}),
        html.Td(f"{value:+,.0f}", style={**base, "color":
                theme.POSITIVE if value >= 0 else theme.NEGATIVE}),
        html.Td(f"{pnl:+,.0f}", style={**base, "color":
                theme.POSITIVE if pnl >= 0 else theme.NEGATIVE}),
        html.Td(f"{pct:+.1f}%" if pct is not None else "\u2014",
                style={**base, "color":
                       theme.POSITIVE if pnl >= 0 else theme.NEGATIVE}),
    ])


# --- formatting -----------------------------------------------------------

def _td(text, colour=theme.TEXT, weight=400, align="right", mono=True):
    style = {"padding": "4px 10px", "fontSize": "11px", "textAlign": align,
             "color": colour, "fontWeight": weight, "whiteSpace": "nowrap"}
    if mono:
        style.update(theme.NUM)
    return html.Td(text, style=style)


def _fmt(val, symbol="", suffix=""):
    if val is None:
        return "\u2014"
    return (f"{symbol}{val:,.0f}{suffix}" if abs(val) >= 100
            else f"{symbol}{val:,.2f}{suffix}")


def _fmt_signed(val):
    if val is None:
        return "\u2014"
    return f"{val:+,.0f}" if abs(val) >= 100 else f"{val:+,.2f}"


def _fmt_qty(val):
    if val is None:
        return "\u2014"
    if val == int(val):
        return f"{int(val):+,}"
    return f"{val:+,.4f}".rstrip("0").rstrip(".")


def _native(price, price_unit, currency):
    if price is None:
        return "\u2014"
    if price_unit == "pence":
        return _fmt(price, suffix="p")
    sym = {"GBP": "\u00A3", "USD": "$", "TRY": "\u20BA"}.get(currency, "")
    return _fmt(price, symbol=sym)


def _msg(text):
    return html.P(text, style={"color": theme.NEUTRAL, "fontSize": "12px",
                               "padding": "14px"})
