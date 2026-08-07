"""
Portfolio - Accounts tab.

What sits in each account: positions derived from the transaction history
(net quantity per account and fund), plus any holdings mapped to an account
in config.HOLDING_ACCOUNTS, plus cash balances. One section per account with
its own total, and a grand total at the top.

P&L per position comes from the whole-portfolio figures, so it is the P&L of
the position rather than of the slice held in that account - the same as the
legacy tab did it, since cost basis is tracked per fund, not per account.
"""

from __future__ import annotations

from collections import defaultdict

import dash
from dash import html, callback, Input, Output
import pandas as pd

from core import theme, finance as fin, valuation as val
from core.repo import portfolio as repo

# Composite definitions and account mappings come from FTScrapper's config,
# loaded by file path - a plain `import config` fails from Pusula.
def _composites():
    return val.composite_definitions()


def _holding_accounts():
    return val.holding_accounts()

CURRENCY_SYMBOLS = {"GBP": "\u00A3", "USD": "$", "TRY": "\u20BA"}


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Accounts", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Holdings and cash grouped by account.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Span(id="pac-total", style={"fontSize": "20px",
                      "fontWeight": 700, "color": theme.INK, **theme.NUM}),
        ], style={**theme.CARD, "display": "flex",
                  "justifyContent": "space-between", "alignItems": "center",
                  "marginBottom": "12px"}),
        html.Div(id="pac-body"),
    ])


@callback(Output("pac-body", "children"), Output("pac-total", "children"),
          Input("pf-tabs", "value"))
def _accounts(_tab):
    instruments = repo.instruments()
    prices = repo.latest_prices()
    rates = fin.fx_rates(prices)
    gbpusd = rates["USD"]
    price_map = fin.latest_price_map(prices)

    pnl_map = _pnl_by_fund(instruments, price_map, gbpusd, rates)

    by_account = defaultdict(list)

    # Positions inferred from the transaction ledger.
    positions = repo.account_positions()
    for p in positions.to_dict("records"):
        by_account[p["account"]].append(
            _position(p["fund_id"], p["name"], p["units"], instruments,
                      price_map, gbpusd, rates, pnl_map))

    # Holdings pinned to an account in config, valued from portfolio_holdings.
    holdings = {h["fund_id"]: h["units"]
                for h in repo.holdings().to_dict("records")}
    # Anything already placed by the ledger is skipped: the static mapping
    # exists for holdings with no transactions, and the composites gained a
    # transaction history, so they were being counted twice.
    from_ledger = set(positions["fund_id"]) if not positions.empty else set()
    for fid, account in (_holding_accounts() or {}).items():
        if fid not in holdings or fid in from_ledger:
            continue
        inst = instruments.get(fid, {})
        by_account[account].append(
            _position(fid, inst.get("name", fid), holdings[fid], instruments,
                      price_map, gbpusd, rates, pnl_map=None))

    cash_by_account = defaultdict(list)
    for acc in repo.cash_accounts().to_dict("records"):
        cash_by_account[acc.get("name")].append(acc)

    accounts = sorted(set(by_account) | set(cash_by_account),
                      key=lambda a: (a is None, str(a)))
    if not accounts:
        return _msg("No accounts found."), ""

    sections, grand = [], 0.0
    for account in accounts:
        section, total = _section(account, by_account.get(account, []),
                                  cash_by_account.get(account, []), rates)
        sections.append(section)
        grand += total

    return html.Div(sections), f"\u00A3{grand:,.0f}"


def _pnl_by_fund(instruments, price_map, gbpusd, rates):
    """Whole-portfolio P&L keyed by fund, so each account row can show the
    position's cost and P&L without recomputing per account."""
    txns = repo.transactions()
    if txns.empty:
        return {}
    out = {}
    for fid, g in txns.groupby("fund_id", sort=False):
        price = val.holding_price_gbp(fid, instruments, price_map, gbpusd,
                                      rates, _composites())
        res = fin.position_pnl(g, price)
        out[fid] = res
    return out


def _position(fid, name, units, instruments, price_map, gbpusd, rates,
              pnl_map):
    price = val.holding_price_gbp(fid, instruments, price_map, gbpusd, rates,
                                  _composites())
    inst = instruments.get(fid, {})
    p = (pnl_map or {}).get(fid) if pnl_map else None
    return {
        "fund_id": fid,
        "name": name or inst.get("name") or fid,
        "category": inst.get("category") or "\u2014",
        "units": float(units or 0),
        "value": price * float(units or 0) if price else None,
        "cost": p["cost_basis"] if p else None,
        "pnl": p["pnl"] if p else None,
        "pnl_pct": p["pnl_pct"] if p else None,
    }


def _section(account, holdings, cashes, rates):
    hold_total = val.total(h["value"] for h in holdings)
    cash_total = val.cash_total_gbp(cashes, rates)
    total = hold_total + cash_total

    rows = []
    for h in sorted(holdings, key=lambda x: val.clean(x["value"]) or 0,
                     reverse=True):
        rows.append(_holding_row(h))
    for c in cashes:
        rows.append(_cash_row(c, rates))
    rows.append(_account_total_row(account, total))

    return html.Div([
        html.Div(str(account or "Unassigned"), style={
            "color": theme.NEEDLE, "fontSize": "11px", "fontWeight": 700,
            "letterSpacing": "0.06em", "textTransform": "uppercase",
            "marginBottom": "8px", "borderLeft": f"3px solid {theme.NEEDLE}",
            "paddingLeft": "8px"}),
        html.Div(html.Table([_header(), html.Tbody(rows)],
                 style={"width": "100%", "borderCollapse": "collapse"}),
                 style={"overflowX": "auto"}),
    ], style={**theme.CARD, "marginBottom": "10px"}), total


def _header():
    cols = [("Fund", "left"), ("Category", "left"), ("Qty", "right"),
            ("Value \u00A3", "right"), ("Cost \u00A3", "right"),
            ("P&L \u00A3", "right"), ("P&L %", "right")]
    return html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "5px 8px", "fontSize": "10px", "fontWeight": 600,
                "textAlign": a, "whiteSpace": "nowrap"})
        for c, a in cols]))


def _holding_row(h):
    pnl_col = theme.POSITIVE if (h["pnl"] or 0) >= 0 else theme.NEGATIVE
    name = h["name"]
    disp = name if len(name) <= 35 else name[:35] + "\u2026"
    units = h["units"]
    qty = (f"{units:,.0f}" if units == int(units)
           else f"{units:,.4f}".rstrip("0").rstrip("."))
    return html.Tr([
        html.Td(html.Span(disp, title=name),
                style={"padding": "4px 8px", "fontSize": "11px",
                       "color": theme.INK, "whiteSpace": "nowrap"}),
        html.Td(h["category"], style={"padding": "4px 8px",
                "fontSize": "10px", "color": theme.SLATE,
                "whiteSpace": "nowrap"}),
        _num(qty, theme.TEXT),
        _num(f"{h['value']:,.0f}" if h["value"] else "\u2014", theme.INK, 600),
        _num(f"{h['cost']:,.0f}" if h["cost"] else "\u2014", theme.NEUTRAL),
        _num(f"{h['pnl']:+,.0f}" if h["pnl"] is not None else "\u2014",
             pnl_col, 700),
        _num(f"{h['pnl_pct']:+.1f}%" if h["pnl_pct"] is not None else "\u2014",
             pnl_col),
    ], style={"borderBottom": f"1px solid {theme.LINE}"})


def _cash_row(acc, rates):
    amount = float(acc.get("amount") or 0)
    curr = acc.get("currency", "GBP")
    gbp = val.cash_to_gbp(amount, curr, rates)
    sym = CURRENCY_SYMBOLS.get(curr, "")
    return html.Tr([
        html.Td(f"Cash ({curr})", style={"padding": "4px 8px",
                "fontSize": "11px", "color": theme.NEUTRAL,
                "fontStyle": "italic", "whiteSpace": "nowrap"}),
        html.Td("Cash", style={"padding": "4px 8px", "fontSize": "10px",
                "color": theme.NEUTRAL}),
        _num("\u2014", theme.NEUTRAL),
        _num(f"{gbp:,.0f}", theme.INK, 600),
        _num(f"{sym}{abs(amount):,.0f}", theme.NEUTRAL),
        _num("\u2014", theme.NEUTRAL),
        _num("\u2014", theme.NEUTRAL),
    ], style={"borderBottom": f"1px solid {theme.LINE}",
              "background": theme.SURFACE})


def _account_total_row(account, total):
    border = f"2px solid {theme.LINE}"
    return html.Tr([
        html.Td(f"{account or 'Unassigned'} TOTAL", colSpan=3,
                style={"padding": "6px 8px", "fontSize": "11.5px",
                       "fontWeight": 700, "color": theme.NEEDLE,
                       "borderTop": border}),
        html.Td(f"{total:,.0f}", style={"padding": "6px 8px",
                "fontSize": "11.5px", "textAlign": "right", **theme.NUM,
                "fontWeight": 700, "color": theme.NEEDLE,
                "borderTop": border}),
        html.Td("", colSpan=3, style={"borderTop": border}),
    ])


def _num(text, colour, weight=400):
    return html.Td(text, style={"padding": "4px 8px", "fontSize": "11px",
                   "textAlign": "right", **theme.NUM, "color": colour,
                   "fontWeight": weight, "whiteSpace": "nowrap"})


def _msg(text):
    return html.P(text, style={"color": theme.NEUTRAL, "fontSize": "12px",
                               "padding": "14px"})
