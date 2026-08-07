"""
Portfolio - Summary tab.

Every holding by value, in thousands, with its share of the portfolio, and
optionally the change since a past snapshot. The snapshot picker is what makes
this tab useful: pick a date and each row shows how it has moved since.

Snapshot values are frozen records, not recomputed - a snapshot is what the
portfolio was worth on that day, so comparing against it is comparing against
history rather than a re-derived figure.
"""

from __future__ import annotations

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd

from core import theme, finance as fin, valuation as val
from core.repo import portfolio as repo

# Composite definitions come from FTScrapper's config, loaded by file path -
# a plain `import config` fails because Pusula is not on that path.
def _composites():
    return val.composite_definitions()


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Summary", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Holdings by value, with change since a snapshot.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Label("Compare with", style={"fontSize": "11px",
                           "color": theme.SLATE, "marginRight": "8px"}),
                dcc.Dropdown(id="psum-snapshot",
                             options=repo.snapshot_options(),
                             value=repo.latest_snapshot_date() or "none",
                             clearable=False,
                             style={"fontSize": "12px", "width": "170px"}),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "12px"}),
        html.Div(html.Div(id="psum-table"),
                 style={**theme.CARD, "padding": "0", "overflow": "auto",
                        "maxHeight": "calc(100vh - 220px)"}),
    ])


@callback(Output("psum-table", "children"), Input("psum-snapshot", "value"))
def _summary(snap_date):
    instruments = repo.instruments()
    prices = repo.latest_prices()
    rates = fin.fx_rates(prices)
    gbpusd = rates["USD"]
    price_map = fin.latest_price_map(prices)

    valued = val.value_holdings(repo.holdings(), instruments, price_map,
                                gbpusd, rates, _composites())
    rows = valued.to_dict("records") if not valued.empty else []

    cash = repo.cash_accounts()
    cash_total = val.cash_total_gbp(cash, rates)
    if cash_total:
        rows.append({"fund_id": "CASH:TOTAL", "name": "Cash",
                     "value": cash_total})

    if not rows:
        return _msg("No holdings found.")

    snap = repo.snapshot_values(snap_date)
    snap_label = (pd.Timestamp(snap_date).strftime("%d %b %Y")
                  if snap else None)
    # Anything in the snapshot that is no longer held still belongs in the
    # comparison - otherwise the snapshot column omits those values while the
    # total includes them, and the column does not add up. Exited positions
    # come in at zero, so the change reads as the full disposal.
    if snap:
        held = {r["fund_id"] for r in rows}
        names = repo.instruments()
        for fid, prior in snap.items():
            if fid in held or not val.clean(prior):
                continue
            rows.append({"fund_id": fid,
                         "name": (names.get(fid, {}) or {}).get("name") or fid,
                         "value": 0.0})
    # NaN is truthy, so one unpriceable holding would poison the sum and
    # turn every percentage into nan%. val.total skips NaN and None.
    total = val.total(r.get("value") for r in rows)

    body = []
    for r in sorted(rows, key=lambda x: val.clean(x.get("value")) or 0,
                       reverse=True):
        body.append(_row(r, total, snap, bool(snap_label)))
    body.append(_totals_row(total, snap, bool(snap_label)))

    return html.Table([_header(snap_label), html.Tbody(body)],
                      style={"width": "100%", "borderCollapse": "collapse"})


def _header(snap_label):
    cols = ["Fund", "Value k", "%"]
    if snap_label:
        cols += [f"{snap_label} k", "Chg k", "Chg %"]
    return html.Thead(html.Tr([
        html.Th(c, style={"background": theme.INK, "color": "#fff",
                "padding": "7px 8px", "fontSize": "11px", "fontWeight": 600,
                "textAlign": "left" if i == 0 else "right",
                "whiteSpace": "nowrap", "position": "sticky", "top": 0})
        for i, c in enumerate(cols)]))


def _row(r, total, snap, show_change):
    value = val.clean(r.get("value"))
    # value is 0 for an exited position and None when unpriceable, so
    # test against None rather than truthiness - 0 is falsy.
    pct = (value / total * 100) if (total and value is not None) else None
    name = r["name"]
    disp = name if len(name) <= 30 else name[:30] + "\u2026"

    cells = [
        html.Td(html.Span(disp, title=name),
                style={"padding": "6px 8px", "fontSize": "12.5px",
                       "color": theme.INK, "whiteSpace": "nowrap",
                       "maxWidth": "260px", "overflow": "hidden",
                       "textOverflow": "ellipsis"}),
        _num(f"{value/1000:.1f}" if value is not None else "N/A",
             theme.INK, 600),
        _num(f"{pct:.1f}%" if pct is not None else "N/A", theme.SLATE),
    ]

    if show_change:
        # Absent from the snapshot means the position did not exist then, so
        # the baseline is zero and the whole of today's value is the change.
        # Without this a new holding showed a dash and the change column
        # could not reconcile with its own total.
        raw_prior = val.clean(snap.get(r["fund_id"]))
        prior = raw_prior if raw_prior is not None else 0.0
        chg = (value - prior) if value is not None else None
        # A zero baseline has no percentage - growth from nothing is undefined.
        chg_pct = (((value / prior) - 1) * 100
                   if (prior and value is not None) else None)
        colour = theme.POSITIVE if (chg or 0) >= 0 else theme.NEGATIVE
        # "sold" reads better than -100.0% for a position fully closed, and
        # "new" better than a blank for one opened since. A partial reduction
        # still shows its percentage, since value stays above zero.
        if value == 0 and prior:
            pct_text = "sold"
        elif chg_pct is not None:
            pct_text = f"{chg_pct:+.1f}%"
        elif prior == 0 and value:
            pct_text = "new"
        else:
            pct_text = "\u2014"
        cells += [
            _num(f"{prior/1000:.1f}", theme.NEUTRAL),
            _num(f"{chg/1000:+.1f}" if chg is not None else "\u2014",
                 colour, 600),
            _num(pct_text, colour if chg_pct is not None else theme.NEUTRAL,
                 600),
        ]
    return html.Tr(cells, style={"borderBottom": f"1px solid {theme.LINE}"})


def _totals_row(total, snap, show_change):
    base = {"padding": "8px", "fontSize": "12.5px", "textAlign": "right",
            **theme.NUM, "fontWeight": 700,
            "borderTop": f"2px solid {theme.INK}"}
    cells = [
        html.Td("TOTAL", style={**base, "textAlign": "left"}),
        html.Td(f"{total/1000:.1f}", style={**base, "color": theme.INK}),
        html.Td("", style={"borderTop": f"2px solid {theme.INK}"}),
    ]
    if show_change:
        prior = val.total(snap.values()) if snap else 0
        chg = (total - prior) if prior else None
        chg_pct = ((total / prior - 1) * 100) if prior else None
        colour = theme.POSITIVE if (chg or 0) >= 0 else theme.NEGATIVE
        cells += [
            html.Td(f"{prior/1000:.1f}" if prior else "\u2014",
                    style={**base, "color": theme.NEUTRAL}),
            html.Td(f"{chg/1000:+.1f}" if chg is not None else "\u2014",
                    style={**base, "color": colour}),
            html.Td(f"{chg_pct:+.1f}%" if chg_pct is not None else "\u2014",
                    style={**base, "color": colour}),
        ]
    return html.Tr(cells)


def _num(text, colour, weight=400):
    return html.Td(text, style={"padding": "6px 8px", "fontSize": "12px",
                   "textAlign": "right", **theme.NUM, "color": colour,
                   "fontWeight": weight, "whiteSpace": "nowrap"})


def _msg(text):
    return html.P(text, style={"color": theme.NEUTRAL, "fontSize": "12px",
                               "padding": "14px"})
