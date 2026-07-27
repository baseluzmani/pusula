"""
Portfolio - P&L tab.

Shows realised/unrealised P&L per holding using the verified core.finance
engine (weighted-average cost, commission-aware, composite-priced). Two
display modes (full / compact), open vs closed positions, and per-period
return columns (1D/1W/1M/3M/YTD) heat-shaded against the spread across
holdings. A totals row sums value, P&L and the 1-day GBP move.

Performance: the legacy tab called calc_return five-plus times per row, each
rescanning the 194k-row price frame. Here latest prices and period returns
are computed once as maps (core.finance.latest_price_map / returns_map) and
looked up per row.

Numbers are validated to match the legacy P&L tab (see tests/parity.py).
"""

from __future__ import annotations

import dash
from dash import html, dcc, callback, Input, Output, State
import pandas as pd

from core import theme, finance as fin
from core.repo import portfolio as repo

try:
    import config as legacy_config
    COMPOSITE_FUNDS = getattr(legacy_config, "COMPOSITE_FUNDS", [])
except Exception:                                              # noqa: BLE001
    COMPOSITE_FUNDS = []

_COMP = {c["fund_id"]: c for c in COMPOSITE_FUNDS}


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Profit & Loss", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Realised and unrealised P&L per holding, in GBP.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Button("Show closed", id="pnl-closed-btn", n_clicks=0,
                            style=_btn(False)),
                html.Button("Compact view", id="pnl-compact-btn", n_clicks=0,
                            style=_btn(False, alt=True)),
            ], style={"display": "flex", "gap": "8px"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "14px"}),

        dcc.Store(id="pnl-closed", data=False),
        dcc.Store(id="pnl-compact", data=False),
        html.Div(html.Div(id="pnl-table"),
                 style={**theme.CARD, "overflow": "auto", "padding": "0",
                        "maxHeight": "calc(100vh - 190px)"}),
    ])


def _btn(active, alt=False):
    on = (theme.NEGATIVE if not alt else "#D06018")
    return {"background": (on if active else theme.INK), "color": "#fff",
            "border": "none", "borderRadius": "4px", "padding": "6px 14px",
            "fontSize": "11px", "fontWeight": 600, "cursor": "pointer"}


# --- toggles --------------------------------------------------------------

@callback(
    Output("pnl-closed", "data"), Output("pnl-closed-btn", "children"),
    Output("pnl-closed-btn", "style"),
    Input("pnl-closed-btn", "n_clicks"), State("pnl-closed", "data"),
    prevent_initial_call=True,
)
def _toggle_closed(_, state):
    s = not state
    return s, ("Hide closed" if s else "Show closed"), _btn(s)


@callback(
    Output("pnl-compact", "data"), Output("pnl-compact-btn", "children"),
    Output("pnl-compact-btn", "style"),
    Input("pnl-compact-btn", "n_clicks"), State("pnl-compact", "data"),
    prevent_initial_call=True,
)
def _toggle_compact(_, state):
    s = not state
    return s, ("Full view" if s else "Compact view"), _btn(s, alt=True)


# --- the table ------------------------------------------------------------

@callback(
    Output("pnl-table", "children"),
    Input("pnl-closed", "data"), Input("pnl-compact", "data"),
)
def _table(show_closed, compact):
    prices = repo.prices()
    instruments = repo.instruments()
    txns = repo.transactions()
    if txns.empty:
        return _msg("No transactions found.")

    rates = fin.fx_rates(prices)
    gbpusd = rates["USD"]
    price_map = fin.latest_price_map(prices)
    ret_map = fin.returns_map(prices)

    rows = _compute(txns, instruments, price_map, rates, gbpusd)
    if not rows:
        return _msg("No positions to show.")

    df = pd.DataFrame(rows).sort_values("current_value", ascending=False,
                                        na_position="last")
    open_df = df[df["qty"] > 0]
    closed_df = df[df["qty"] == 0]

    # Heat ranges: min/max of each period across open holdings.
    ranges = _ranges(open_df, ret_map)

    body = []
    for r in open_df.to_dict("records"):
        body.append(_row(r, instruments, price_map, ret_map, rates, ranges,
                         compact, closed=False))

    if not closed_df.empty:
        closed_pnl = closed_df["pnl"].dropna().sum()
        if show_closed:
            span = 8 if compact else 14
            body.append(html.Tr([html.Td(
                f"CLOSED POSITIONS ({len(closed_df)})", colSpan=span,
                style={"padding": "6px 10px", "fontSize": "10px",
                       "fontWeight": 700, "color": theme.SLATE,
                       "background": theme.SURFACE})]))
            for r in closed_df.to_dict("records"):
                body.append(_row(r, instruments, price_map, ret_map, rates,
                                 ranges, compact, closed=True))
        else:
            body.append(_closed_summary(len(closed_df), closed_pnl, compact))

    body.append(_totals(df, ret_map, compact))

    return html.Table([_header(compact), html.Tbody(body)],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _compute(txns, instruments, price_map, rates, gbpusd):
    rows = []
    for fid, g in txns.groupby("fund_id", sort=False):
        inst = instruments.get(fid, {})
        punit = inst.get("price_unit", "pound")
        curr = inst.get("currency", "GBP")

        tmp = fin.position_pnl(g, None)
        if tmp["qty"] <= 0:
            cur = None
        elif fid.startswith("COMPOSITE:"):
            cd = _COMP.get(fid)
            cur = (fin.composite_price_gbp(fid, cd["components"], price_map.get,
                   instruments, gbpusd, rates) if cd else None)
        elif fid.startswith(("CASH:", "ASSET:")):
            cur = 1.0
        else:
            cur = fin.to_gbp(price_map.get(fid), punit, curr, gbpusd, rates)

        res = fin.position_pnl(g, cur)
        if res["pnl"] is None:
            continue
        res["fund_id"] = fid
        res["Fund"] = inst.get("name", fid)
        res["Category"] = inst.get("category", "\u2014")
        rows.append(res)
    return rows


def _ranges(open_df, ret_map):
    out = {}
    for period in ("1D", "1W", "1M", "3M", "YTD"):
        vals = [ret_map.get(fid, {}).get(period) for fid in open_df["fund_id"]]
        vals = [v for v in vals if v is not None]
        out[period] = (min(vals), max(vals)) if vals else (0, 0)
    return out


# --- row rendering --------------------------------------------------------

def _header(compact):
    def th(c, i):
        return html.Th(c, style={"background": theme.INK, "color": "#fff",
                       "padding": "6px 10px", "fontSize": "10px",
                       "fontWeight": 600, "position": "sticky", "top": 0,
                       "textAlign": "left" if i == 0 else "right",
                       "whiteSpace": "nowrap"})
    if compact:
        cols = ["Fund", "Price", "1D", "1D %", "1W %", "1M %", "3M %", "YTD %"]
    else:
        cols = ["Fund", "Category", "Price", "Avg cost", "Qty", "Value",
                "P&L", "P&L %", "1D", "1D %", "1W %", "1M %", "3M %", "YTD %"]
    return html.Thead(html.Tr([th(c, i) for i, c in enumerate(cols)]))


def _fmt(v, symbol="", suffix=""):
    if v is None:
        return "\u2014"
    return (f"{symbol}{v:,.2f}{suffix}" if abs(v) < 100
            else f"{symbol}{v:,.0f}{suffix}")


def _native_price(price, fid, instruments):
    if price is None:
        return "\u2014"
    inst = instruments.get(fid, {})
    punit = inst.get("price_unit", "pound")
    sym = {"GBP": "\u00A3", "USD": "$", "TRY": "\u20BA"}.get(
        inst.get("currency", "GBP"), "")
    if punit == "pence":
        return _fmt(price, suffix="p")
    if punit == "point":
        return _fmt(price)
    return _fmt(price, symbol=sym)


def _avg_str(avg_gbp, qty, fid, instruments, rates):
    if not (qty > 0 and avg_gbp > 0):
        return "\u2014"
    inst = instruments.get(fid, {})
    punit = inst.get("price_unit", "pound")
    curr = inst.get("currency", "GBP")
    if punit == "pence" and curr == "GBP":
        return _fmt(avg_gbp * 100, suffix="p")
    if curr == "USD":
        return _fmt(avg_gbp * rates.get("USD", 1.26), symbol="$")
    if curr == "TRY":
        return _fmt(avg_gbp * rates.get("TRY", 43.0), symbol="\u20BA")
    return _fmt(avg_gbp, symbol="\u00A3")


def _ret_td(v, rng):
    return html.Td(f"{v:+.1f}%" if v is not None else "\u2014",
                   style={"padding": "4px 8px", "fontSize": "10.5px",
                          "textAlign": "center", "fontWeight": 600, **theme.NUM,
                          "background": (theme.heat_rgb(v) if v is not None
                                         else "transparent"),
                          "color": theme.INK})


def _row(r, instruments, price_map, ret_map, rates, ranges, compact, closed):
    fid = r["fund_id"]
    pnl = r["pnl"]
    pct = r["pnl_pct"]
    colour = theme.POSITIVE if (pnl or 0) >= 0 else theme.NEGATIVE
    name = r["Fund"]
    ndisp = name if len(name) <= 35 else name[:35] + "\u2026"
    cp = price_map.get(fid)
    rr = ret_map.get(fid, {})
    r1d = rr.get("1D")

    price_c = html.Td(_native_price(cp, fid, instruments),
                      style=_num_td(theme.SLATE))
    name_c = html.Td(html.Span(ndisp, title=name),
                     style={"padding": "5px 10px", "fontSize": "12px",
                            "color": theme.INK, "whiteSpace": "nowrap"})

    val = r["current_value"]
    d1_gbp = (val * r1d / 100) if (val and r1d is not None) else None
    d1_c = html.Td(f"{d1_gbp:+,.0f}" if d1_gbp is not None else "\u2014",
                   style=_num_td(theme.POSITIVE if (r1d or 0) >= 0
                                 else theme.NEGATIVE, weight=600))
    ret_cells = [_ret_td(rr.get("1D"), ranges["1D"]),
                 _ret_td(rr.get("1W"), ranges["1W"]),
                 _ret_td(rr.get("1M"), ranges["1M"]),
                 _ret_td(rr.get("3M"), ranges["3M"]),
                 _ret_td(rr.get("YTD"), ranges["YTD"])]

    if compact:
        cells = [name_c, price_c, d1_c] + ret_cells
    else:
        qty = r["qty"]
        qty_disp = ("\u2014" if qty <= 0 else
                    (f"{qty:,.2f}".rstrip("0").rstrip(".") if qty < 100
                     else f"{qty:,.0f}"))
        val_disp = (f"{val:,.0f}" if val else
                    ("Closed" if qty == 0 else "N/A"))
        cells = [
            name_c,
            html.Td(r["Category"], style={"padding": "5px 10px",
                    "fontSize": "10px", "textAlign": "center",
                    "color": theme.SLATE}),
            price_c,
            html.Td(_avg_str(r["avg_cost"], qty, fid, instruments, rates),
                    style=_num_td(theme.NEUTRAL)),
            html.Td(qty_disp, style=_num_td(theme.TEXT)),
            html.Td(val_disp, style=_num_td(theme.INK, weight=600)),
            html.Td(f"{pnl:+,.0f}" if pnl is not None else "N/A",
                    style=_num_td(colour, weight=700)),
            html.Td(f"{pct:+.1f}%" if pct is not None else "N/A",
                    style=_num_td(colour, weight=600)),
            d1_c,
        ] + ret_cells

    bg = theme.SURFACE if closed else "transparent"
    return html.Tr(cells, style={"borderBottom": f"1px solid {theme.LINE}",
                                 "background": bg})


def _num_td(colour, weight=400):
    return {"padding": "5px 10px", "fontSize": "11.5px", "textAlign": "right",
            **theme.NUM, "fontWeight": weight, "color": colour}


def _closed_summary(count, pnl, compact):
    span = 8 if compact else 5
    colour = theme.POSITIVE if pnl >= 0 else theme.NEGATIVE
    cells = [html.Td(f"Closed positions ({count})", colSpan=span,
             style={"padding": "5px 10px", "fontSize": "12px",
                    "color": theme.NEUTRAL, "fontStyle": "italic"})]
    if not compact:
        cells += [
            html.Td("Closed", style=_num_td(theme.NEUTRAL)),
            html.Td(f"{pnl:+,.0f}", style=_num_td(colour, weight=700)),
            html.Td("\u2014", colSpan=6, style=_num_td(theme.NEUTRAL)),
        ]
    return html.Tr(cells, style={"borderBottom": f"1px solid {theme.LINE}",
                                 "background": theme.SURFACE})


def _totals(all_df, ret_map, compact):
    # Sum P&L over ALL positions (open + closed) to match legacy — closed
    # realised P&L belongs in the total. Value and 1D come from open only.
    open_df = all_df[all_df["qty"] > 0]
    total_value = open_df["current_value"].dropna().sum()
    total_pnl = all_df["pnl"].dropna().sum()
    total_cost = all_df["cost_basis"].sum()
    realised_abs = all_df["realised"].abs().sum()
    total_pct = (total_pnl / (total_cost + realised_abs) * 100
                 if (total_cost + realised_abs) else 0)
    total_1d = 0.0
    for r in open_df.to_dict("records"):
        v = r["current_value"]; d = ret_map.get(r["fund_id"], {}).get("1D")
        if v and d is not None:
            total_1d += v * d / 100

    pc = theme.POSITIVE if total_pnl >= 0 else theme.NEGATIVE
    dc = theme.POSITIVE if total_1d >= 0 else theme.NEGATIVE
    tb = {"padding": "7px 10px", "fontSize": "12px", "textAlign": "right",
          **theme.NUM, "fontWeight": 700, "borderTop": f"2px solid {theme.INK}"}

    if compact:
        return html.Tr([
            html.Td("TOTAL", style={**tb, "textAlign": "left"}),
            html.Td("", style={"borderTop": f"2px solid {theme.INK}"}),
            html.Td(f"{total_1d:+,.0f}", style={**tb, "color": dc}),
            html.Td("", colSpan=5, style={"borderTop": f"2px solid {theme.INK}"}),
        ])
    return html.Tr([
        html.Td("TOTAL", colSpan=5, style={**tb, "textAlign": "left"}),
        html.Td(f"{total_value:,.0f}", style=tb),
        html.Td(f"{total_pnl:+,.0f}", style={**tb, "color": pc}),
        html.Td(f"{total_pct:+.1f}%", style={**tb, "color": pc}),
        html.Td(f"{total_1d:+,.0f}", style={**tb, "color": dc}),
        html.Td("", colSpan=5, style={"borderTop": f"2px solid {theme.INK}"}),
    ])


def _msg(text):
    return html.P(text, style={"color": theme.NEUTRAL, "fontSize": "12px",
                               "padding": "14px"})
