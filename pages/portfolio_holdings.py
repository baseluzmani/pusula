"""
Portfolio - holdings tab.

The landing view: every holding with its units, native price and GBP value,
optionally compared against a past snapshot, with breakdowns by category and
asset type alongside.

Read-only. Everything that writes lives on the Inputs tab.

Positions closed since the chosen snapshot still appear, at zero, so the
comparison columns reconcile with their own totals - the same reasoning as on
the Summary tab. A holding absent from the snapshot is treated as having had
no value then, so its whole current value shows as the change.
"""

from __future__ import annotations

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd

from core import theme, finance as fin, valuation as val
from core.repo import portfolio as repo

CURRENCY_SYMBOLS = {"GBP": "\u00A3", "USD": "$", "TRY": "\u20BA"}


def _composites():
    return val.composite_definitions()


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Portfolio", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Holdings with units, price and value in GBP.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Div([
                    html.Label("Compare with", style={"fontSize": "11px",
                               "color": theme.SLATE, "marginRight": "8px"}),
                    dcc.Dropdown(id="pf-snapshot",
                                 options=repo.snapshot_options(),
                                 value=repo.latest_snapshot_date() or "none",
                                 clearable=False,
                                 style={"fontSize": "12px", "width": "170px"}),
                ], style={"display": "flex", "alignItems": "center"}),
                html.Div(id="pf-total", style={"fontSize": "22px",
                         "fontWeight": 700, "color": theme.INK,
                         "marginLeft": "20px", **theme.NUM}),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={**theme.CARD, "display": "flex",
                  "justifyContent": "space-between", "alignItems": "center",
                  "marginBottom": "12px"}),

        html.Div([
            html.Div(html.Div(id="pf-holdings"),
                     style={**theme.CARD, "flex": "1", "minWidth": 0,
                            "padding": "0", "marginRight": "12px",
                            "overflow": "auto",
                            "maxHeight": "calc(100vh - 210px)"}),
            html.Div([
                html.Div([
                    html.Div("By category", style=theme.CARD_TITLE),
                    html.Div(id="pf-by-category"),
                ], style={**theme.CARD, "marginBottom": "12px"}),
                html.Div([
                    html.Div("By asset type", style=theme.CARD_TITLE),
                    html.Div(id="pf-by-type"),
                ], style=theme.CARD),
            ], style={"flexShrink": "0", "width": "380px",
                      "overflowY": "auto", "maxHeight": "calc(100vh - 210px)"}),
        ], style={"display": "flex", "alignItems": "flex-start"}),
    ])


# --- data -----------------------------------------------------------------

def _rows(snap_date):
    """Valued holdings plus cash, with the native price kept for display."""
    instruments = repo.instruments()
    prices = repo.prices()
    rates = fin.fx_rates(prices)
    price_map = fin.latest_price_map(prices)

    valued = val.value_holdings(repo.holdings(), instruments, price_map,
                                rates["USD"], rates, _composites())
    rows = valued.to_dict("records") if not valued.empty else []
    for r in rows:
        inst = instruments.get(r["fund_id"], {})
        r["currency"] = inst.get("currency") or "GBP"
        r["price_unit"] = inst.get("price_unit") or "pound"
        r["native_price"] = price_map.get(r["fund_id"])

    cash_total = val.cash_total_gbp(repo.cash_accounts(), rates)
    if cash_total:
        rows.append({"fund_id": "CASH:TOTAL", "name": "Cash",
                     "asset_type": "Cash", "category": "Cash",
                     "units": None, "value": cash_total, "currency": "GBP",
                     "price_unit": "pound", "native_price": None})
    return rows


def _with_exits(rows, snap):
    """Add zero-value rows for anything in the snapshot no longer held."""
    if not snap:
        return rows
    held = {r["fund_id"] for r in rows}
    instruments = repo.instruments()
    for fid, prior in snap.items():
        if fid in held or not val.clean(prior):
            continue
        inst = instruments.get(fid, {})
        rows.append({"fund_id": fid, "name": inst.get("name") or fid,
                     "asset_type": inst.get("asset_type") or "Other",
                     "category": inst.get("category") or "Other",
                     "units": 0.0, "value": 0.0,
                     "currency": inst.get("currency") or "GBP",
                     "price_unit": inst.get("price_unit") or "pound",
                     "native_price": None})
    return rows


@callback(
    Output("pf-holdings", "children"), Output("pf-total", "children"),
    Output("pf-by-category", "children"), Output("pf-by-type", "children"),
    Input("pf-snapshot", "value"),
)
def _render(snap_date):
    snap = repo.snapshot_values(snap_date)
    rows = _with_exits(_rows(snap_date), snap)
    if not rows:
        return _msg("No holdings found."), "", html.Div(), html.Div()

    total = val.total(r.get("value") for r in rows)
    show = bool(snap)
    label = (pd.Timestamp(snap_date).strftime("%d %b %Y") if show else None)

    rows.sort(key=lambda r: val.clean(r.get("value")) or 0, reverse=True)
    body = [_holding_row(r, total, snap, show) for r in rows]
    body.append(_totals_row(total, snap, show))
    table = html.Table([_header(label), html.Tbody(body)],
                       style={"width": "100%", "borderCollapse": "collapse"})

    cats = _breakdown(rows, "category", total,
                      repo.snapshot_category_values(snap_date), show, label)
    types = _breakdown(rows, "asset_type", total,
                       repo.snapshot_asset_type_values(snap_date), show, label)
    return table, f"\u00A3{total:,.0f}", cats, types


# --- holdings table -------------------------------------------------------

def _header(label):
    cols = [("Fund", "left"), ("Category", "left"), ("CCY", "center"),
            ("Units", "right"), ("Price", "right"), ("Value \u00A3", "right"),
            ("%", "right")]
    if label:
        cols += [(f"{label} \u00A3", "right"), ("Chg \u00A3", "right"),
                 ("Chg %", "right")]
    return html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "6px 9px", "fontSize": "10px", "fontWeight": 600,
                "textAlign": a, "whiteSpace": "nowrap", "position": "sticky",
                "top": 0, "zIndex": 1}) for c, a in cols]))


def _holding_row(r, total, snap, show):
    value = val.clean(r.get("value"))
    pct = (value / total * 100) if (total and value is not None) else None
    name = r["name"]
    disp = name if len(name) <= 32 else name[:32] + "\u2026"
    units = r.get("units")

    cells = [
        html.Td(html.Span(disp, title=name),
                style={"padding": "4px 9px", "fontSize": "11.5px",
                       "color": theme.INK, "whiteSpace": "nowrap",
                       "maxWidth": "230px", "overflow": "hidden",
                       "textOverflow": "ellipsis"}),
        html.Td(r.get("category") or "\u2014",
                style={"padding": "4px 9px", "fontSize": "10px",
                       "color": theme.SLATE, "whiteSpace": "nowrap"}),
        html.Td(r.get("currency") or "\u2014",
                style={"padding": "4px 6px", "fontSize": "10px",
                       "textAlign": "center", "color": theme.NEUTRAL}),
        _num(_fmt_units(units), theme.TEXT),
        _num(_fmt_price(r), theme.SLATE),
        _num(f"{value:,.0f}" if value is not None else "N/A", theme.INK, 600),
        _num(f"{pct:.1f}%" if pct is not None else "\u2014", theme.SLATE),
    ]
    if show:
        cells += _change_cells(value, val.clean(snap.get(r["fund_id"])))
    return html.Tr(cells, style={"borderBottom": f"1px solid {theme.LINE}"})


def _change_cells(value, raw_prior):
    """Snapshot value, change and change percent for one row.

    A missing snapshot entry means the position did not exist then, so the
    baseline is zero rather than unknown - otherwise the change column cannot
    add up to its own total.
    """
    prior = raw_prior if raw_prior is not None else 0.0
    chg = (value - prior) if value is not None else None
    chg_pct = (((value / prior) - 1) * 100
               if (prior and value is not None) else None)
    colour = theme.POSITIVE if (chg or 0) >= 0 else theme.NEGATIVE

    if value == 0 and prior:
        pct_text, pct_colour = "sold", theme.NEUTRAL
    elif chg_pct is not None:
        pct_text, pct_colour = f"{chg_pct:+.1f}%", colour
    elif prior == 0 and value:
        pct_text, pct_colour = "new", theme.NEUTRAL
    else:
        pct_text, pct_colour = "\u2014", theme.NEUTRAL

    return [
        _num(f"{prior:,.0f}", theme.NEUTRAL),
        _num(f"{chg:+,.0f}" if chg is not None else "\u2014", colour, 600),
        _num(pct_text, pct_colour, 600),
    ]


def _totals_row(total, snap, show):
    base = {"padding": "7px 9px", "fontSize": "12px", "textAlign": "right",
            **theme.NUM, "fontWeight": 700,
            "borderTop": f"2px solid {theme.INK}"}
    cells = [
        html.Td("TOTAL", colSpan=5, style={**base, "textAlign": "left"}),
        html.Td(f"{total:,.0f}", style=base),
        html.Td("", style={"borderTop": f"2px solid {theme.INK}"}),
    ]
    if show:
        prior = val.total(snap.values())
        chg = total - prior
        pct = ((total / prior - 1) * 100) if prior else None
        colour = theme.POSITIVE if chg >= 0 else theme.NEGATIVE
        cells += [
            html.Td(f"{prior:,.0f}", style={**base, "color": theme.NEUTRAL}),
            html.Td(f"{chg:+,.0f}", style={**base, "color": colour}),
            html.Td(f"{pct:+.1f}%" if pct is not None else "\u2014",
                    style={**base, "color": colour}),
        ]
    return html.Tr(cells)


# --- breakdown panels -----------------------------------------------------

def _breakdown(rows, key, total, snap_values, show, label):
    """Value by category or asset type, with optional snapshot comparison."""
    grouped = {}
    for r in rows:
        v = val.clean(r.get("value"))
        if v is None:
            continue
        grouped[r.get(key) or "Other"] = grouped.get(r.get(key) or "Other", 0) + v

    # Groups that existed at the snapshot but hold nothing now still belong.
    for name in snap_values:
        grouped.setdefault(name, 0.0)

    order = sorted(grouped.items(), key=lambda kv: kv[1], reverse=True)

    head = [html.Th("", style=_bh("left")),
            html.Th("\u00A3k", style=_bh()),
            html.Th("%", style=_bh())]
    if show:
        head += [html.Th(f"{label} \u00A3k", style=_bh()),
                 html.Th("Chg", style=_bh())]

    body = []
    for name, value in order:
        pct = (value / total * 100) if total else None
        cells = [
            html.Td(name, style={"padding": "3px 6px", "fontSize": "11px",
                    "color": theme.INK, "whiteSpace": "nowrap",
                    "maxWidth": "140px", "overflow": "hidden",
                    "textOverflow": "ellipsis"}),
            _num(f"{value/1000:,.1f}", theme.INK, 600, pad="3px 6px"),
            _num(f"{pct:.1f}%" if pct is not None else "\u2014", theme.SLATE,
                 pad="3px 6px"),
        ]
        if show:
            prior = val.clean(snap_values.get(name)) or 0.0
            chg = value - prior
            colour = theme.POSITIVE if chg >= 0 else theme.NEGATIVE
            cells += [
                _num(f"{prior/1000:,.1f}", theme.NEUTRAL, pad="3px 6px"),
                _num(f"{chg/1000:+,.1f}", colour, 600, pad="3px 6px"),
            ]
        body.append(html.Tr(cells,
                            style={"borderTop": f"1px solid {theme.LINE}"}))

    tb = {"padding": "5px 6px", "fontSize": "11.5px", "textAlign": "right",
          **theme.NUM, "fontWeight": 700,
          "borderTop": f"2px solid {theme.INK}"}
    total_cells = [html.Td("Total", style={**tb, "textAlign": "left"}),
                   html.Td(f"{total/1000:,.1f}", style=tb),
                   html.Td("", style={"borderTop": f"2px solid {theme.INK}"})]
    if show:
        prior_total = val.total(snap_values.values())
        chg = total - prior_total
        colour = theme.POSITIVE if chg >= 0 else theme.NEGATIVE
        total_cells += [
            html.Td(f"{prior_total/1000:,.1f}",
                    style={**tb, "color": theme.NEUTRAL}),
            html.Td(f"{chg/1000:+,.1f}", style={**tb, "color": colour}),
        ]
    body.append(html.Tr(total_cells))

    return html.Table([html.Thead(html.Tr(head)), html.Tbody(body)],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _bh(align="right"):
    return {"background": theme.INK, "color": "#fff", "padding": "4px 6px",
            "fontSize": "9.5px", "fontWeight": 600, "textAlign": align,
            "whiteSpace": "nowrap"}


# --- formatting -----------------------------------------------------------

def _num(text, colour, weight=400, pad="4px 9px"):
    return html.Td(text, style={"padding": pad, "fontSize": "11px",
                   "textAlign": "right", **theme.NUM, "color": colour,
                   "fontWeight": weight, "whiteSpace": "nowrap"})


def _fmt_units(units):
    if units is None:
        return "\u2014"
    if units == int(units):
        return f"{int(units):,}"
    return f"{units:,.4f}".rstrip("0").rstrip(".")


def _fmt_price(r):
    price = r.get("native_price")
    if price is None:
        return "Mixed" if r["fund_id"] == "CASH:TOTAL" else "\u2014"
    if r.get("price_unit") == "pence":
        return f"{price:,.1f}p"
    sym = CURRENCY_SYMBOLS.get(r.get("currency"), "")
    return f"{sym}{price:,.2f}" if abs(price) < 100 else f"{sym}{price:,.0f}"


def _msg(text):
    return html.P(text, style={"color": theme.NEUTRAL, "fontSize": "12px",
                               "padding": "14px"})
