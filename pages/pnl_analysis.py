"""
pages.pnl_analysis
==================

Portfolio > P&L analysis.

Return on capital employed per instrument, with manual basket aggregation.
All figures come from core.roce and cover the full ledger — this page has no
date filter by design, because a windowed ROCE needs an opening and closing
mark and the unrealised P&L at a window start is not zero.

Rendered as a tab inside pages/portfolio.py, which calls render().
Deliberately does NOT call dash.register_page - it is a sub-tab, not a
top-level page, so it must stay out of dash.page_registry and the top bar.
"""

from __future__ import annotations

import re

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html
from dash.dash_table.Format import Format, Group, Scheme, Symbol

from core import finance as fin
from core.repo import portfolio as repo
from core import risk as risk_engine
from core.roce import (
    MIN_DAYS_TO_ANNUALISE,
    Position,
    aggregate,
    compute_all,
    load_latest_prices,
    load_trades,
    validate,
)


PREFIX = "basket-"

def get_data() -> dict:
    """Run the engine and return everything the page needs.

    Returns {'trades', 'risk', 'members', 'rows', 'by_id', 'asof', 'approx',
    'unpriceable', 'n_closed', 'n_unpriced'}.

    Deliberately uncached. There was a module-level dict here, and it was the
    source of a bug that took a while to find: it filled on the first render
    after the process started and never refilled, so this page showed prices
    from whenever it was first opened while the P&L tab, which recomputes per
    render, moved with the market. The two tabs disagreed by whatever sterling
    and the market had done in between, and nothing on screen said so.

    A stamp-based invalidation fixed the common cases but not in-place edits -
    a corrected fx_rate or a hand-patched close changes no row count and no max
    date - so the Refresh button had to stay as an escape hatch, and a button
    you must remember to press is a worse bug than a slow page.

    The whole run is ~130ms: the ledger walk is about 100ms and the rest is two
    indexed queries. That is cheap enough to pay on every callback in exchange
    for never having to wonder whether the number on screen is current.

    If this ever becomes slow enough to matter, cache it properly with
    Flask-Caching and a short TTL rather than reintroducing a dict that nothing
    invalidates.
    """
    members = compute_all()
    _, approx, unpriceable = load_latest_prices()
    asof = pd.Timestamp.today().normalize()
    trades = load_trades()
    risk = risk_engine.compute_risk(trades, asof)
    return dict(
        trades=trades,
        risk=risk,
        members=members,
        rows=positions_to_rows(members, asof, risk.by_id),
        by_id={p.instrument: p for p in members},
        asof=asof,
        approx=approx,
        unpriceable=unpriceable,
        n_closed=sum(1 for p in members if p.status == "closed"),
        # Open positions with no mark, or ledgers that failed to walk. These
        # report realised P&L only and are left out of the basket.
        n_unpriced=sum(1 for p in members if not p.priced),
    )


# --------------------------------------------------------------------------
# Table styling, matched to the P&L tab: dark header, compact rows, coloured
# fill on the rate columns and coloured text on the money columns.
# If core.theme exposes these values, prefer them over these literals.
# --------------------------------------------------------------------------

HEADER_BG = "#111827"
POS_TEXT, NEG_TEXT = "#15803d", "#b91c1c"
POS_FILL = ["#f0fdf4", "#dcfce7", "#86efac", "#4ade80"]
NEG_FILL = ["#fef2f2", "#fee2e2", "#fecaca", "#f87171"]

# Rate columns hold fractions (0.059 = 5.9%), so the edges are fractions too.
RATE_EDGES = [0.05, 0.15, 0.30]


def _rate_bands(col: str, *, reverse: bool = False,
                optional: bool = False) -> list[dict]:
    """Graduated green/red fill, strongest at the extremes.

    Edges are fractions because the column holds 0.059 for 5.9%.

    reverse=True swaps the colours, for a column where a rise is bad news
    (the price going up after you sold). optional=True is for a column that
    can be blank: every rule gets an "is num" guard, because the table's
    filter compares a blank as if it were 0 and would otherwise paint it
    with the 0-5% band.
    """
    pos, neg = (NEG_FILL, POS_FILL) if reverse else (POS_FILL, NEG_FILL)
    guard = f"{{{col}}} is num && " if optional else ""
    out: list[dict] = []
    bounds = [0.0] + RATE_EDGES
    for i in range(len(RATE_EDGES)):
        lo, hi = bounds[i], bounds[i + 1]
        out.append({
            "if": {"filter_query": f"{guard}{{{col}}} >= {lo} && {{{col}}} < {hi}",
                   "column_id": col},
            "backgroundColor": pos[i],
        })
        # Mirror of the positive band, half-open the other way: (-hi, -lo].
        # Strict "<" at the zero end keeps 0.0 in the positive band only, and
        # inclusive ">=" at the far end stops values landing on an edge
        # (-0.05, -0.15) falling through with no colour at all.
        near = f"{{{col}}} < {-lo}" if lo == 0.0 else f"{{{col}}} <= {-lo}"
        out.append({
            "if": {"filter_query": f"{guard}{near} && {{{col}}} > {-hi}",
                   "column_id": col},
            "backgroundColor": neg[i],
        })
    top = RATE_EDGES[-1]
    out.append({"if": {"filter_query": f"{guard}{{{col}}} >= {top}", "column_id": col},
                "backgroundColor": pos[-1]})
    out.append({"if": {"filter_query": f"{guard}{{{col}}} <= {-top}", "column_id": col},
                "backgroundColor": neg[-1]})
    return out


# --------------------------------------------------------------------------
# Last buy / last sell. The dates are coloured by age, to build two habits:
# buying regularly (a recent buy is green, a year without one is red) and
# holding without selling (a recent sale is red, three months without one is
# green). Closed rows get no date colour - there is nothing left to hold.
# Bands are (lo, hi] in days; hi=None is open-ended; gaps get no colour.
# --------------------------------------------------------------------------

BUY_AGE_BANDS = [
    (None, 30, POS_FILL[2]),
    (30, 90, POS_FILL[1]),
    # 91-180 days: no colour
    (180, 365, NEG_FILL[1]),
    (365, None, NEG_FILL[2]),
]
SELL_AGE_BANDS = [
    (None, 30, NEG_FILL[2]),
    (30, 60, NEG_FILL[1]),
    (60, 90, POS_FILL[1]),
    (90, None, POS_FILL[2]),
]
# A holding with no sale since it was opened turns green after this many days.
HOLD_GREEN_DAYS = 90
# A mark older than this is treated as out of date: the return is still shown,
# greyed out, with the price date on hover.
STALE_MARK_DAYS = 7


# Sharpe ratio fills: (lo, hi) half-open [lo, hi); None is open-ended.
# 0 to 0.5 gets no colour - not distinguishable from cash at these lengths.
SHARPE_BANDS = [
    (None, 0.0, NEG_FILL[2]),
    (0.5, 1.0, POS_FILL[1]),
    (1.0, 2.0, POS_FILL[2]),
    (2.0, None, POS_FILL[3]),
]


def _sharpe_bands(col: str = "sharpe") -> list[dict]:
    out = []
    for lo, hi, fill in SHARPE_BANDS:
        q = [f"{{{col}}} is num"]
        if lo is not None:
            q.append(f"{{{col}}} >= {lo}")
        if hi is not None:
            q.append(f"{{{col}}} < {hi}")
        out.append({"if": {"filter_query": " && ".join(q), "column_id": col},
                    "backgroundColor": fill})
    return out


def _age_bands(age_col: str, show_col: str, bands) -> list[dict]:
    """Fill `show_col` by the day count in hidden `age_col`. -1 = no colour."""
    out = []
    for lo, hi, fill in bands:
        q = [f"{{{age_col}}} >= 0"]
        if lo is not None:
            q.append(f"{{{age_col}}} > {lo}")
        if hi is not None:
            q.append(f"{{{age_col}}} <= {hi}")
        out.append({"if": {"filter_query": " && ".join(q), "column_id": show_col},
                    "backgroundColor": fill})
    return out


def _money_text(col: str) -> list[dict]:
    return [
        {"if": {"filter_query": f"{{{col}}} > 0", "column_id": col},
         "color": POS_TEXT},
        {"if": {"filter_query": f"{{{col}}} < 0", "column_id": col},
         "color": NEG_TEXT},
    ]


TABLE_STYLE = dict(
    style_as_list_view=False,
    style_table={"overflowX": "auto"},
    style_cell={
        "padding": "4px 6px",
        "fontSize": "12.5px",
        "fontFamily": "system-ui, sans-serif",
        "textAlign": "right",
        "border": "none",
        "borderBottom": "1px solid #f1f5f9",
    },
    style_header={
        "backgroundColor": HEADER_BG,
        "color": "white",
        "fontWeight": 600,
        "fontSize": "12px",
        "border": "none",
        "padding": "6px 6px",
    },
    # Two header rows: the group names on top, centred and quieter.
    style_header_conditional=[
        {"if": {"header_index": 0}, "textAlign": "center", "color": "#9ca3af",
         "fontWeight": 500, "fontSize": "11px", "paddingBottom": "0px"},
    ],
    style_cell_conditional=[
        {"if": {"column_id": "name"}, "textAlign": "left", "minWidth": "200px",
         "fontWeight": 500},
        {"if": {"column_id": "net_cost"}, "color": "#9ca3af"},
        {"if": {"column_id": ["last_buy", "last_sell"]}, "textAlign": "center",
         "fontVariantNumeric": "tabular-nums", "minWidth": "52px"},
    ],
    style_data_conditional=(
        _rate_bands("roce_total")
        + _rate_bands("roce_annualised")
        # Before the closed-row rule, like ROCE, so closed rows show it plain.
        + _sharpe_bands()
        + _money_text("total_pnl")
        + _money_text("realised")
        + _money_text("unrealised")
        + _money_text("fx_pnl")
        + [
            # Under MIN_DAYS_TO_ANNUALISE the rate - and the mean return in
            # Sharpe - is too noisy to read, so both are greyed out.
            {"if": {"filter_query": "{_short} = 1",
                    "column_id": ["roce_annualised", "sharpe"]},
             "color": "#c0c4cc", "fontStyle": "italic",
             "backgroundColor": "transparent"},
            {"if": {"filter_query": "{status} = closed"},
             "backgroundColor": "#fafafa"},
            {"if": {"filter_query": "{status} = closed", "column_id": "name"},
             "color": "#9ca3af", "fontStyle": "italic"},
        ]
        # After the closed-row rule, so closed rows keep their return colours:
        # for a position you have exited, the move since the sale is the
        # number worth seeing. Date colours are already off for closed rows
        # (their ages are -1).
        + _age_bands("_buy_age", "last_buy", BUY_AGE_BANDS)
        + _age_bands("_sell_age", "last_sell", SELL_AGE_BANDS)
        + [{"if": {"filter_query": f"{{_hold_age}} > {HOLD_GREEN_DAYS}",
                   "column_id": "last_sell"},
            "backgroundColor": SELL_AGE_BANDS[-1][2]}]
        + _rate_bands("ret_buy", optional=True)
        # Reversed: the price rising after you sold means the sale cost you.
        + _rate_bands("ret_sell", reverse=True, optional=True)
        + [{"if": {"filter_query": "{_stale} = 1", "column_id": c},
            "color": "#9ca3af", "fontStyle": "italic",
            "backgroundColor": "transparent"}
           for c in ("ret_buy", "ret_sell")]
    ),
)


_INT = Format(precision=0, scheme=Scheme.fixed, group=Group.yes)
_PCT = Format(precision=1, scheme=Scheme.percentage)
_RATIO = Format(precision=2, scheme=Scheme.fixed)
# Days since a trade, shown as "3d". Numeric, so the column sorts by age; a
# blank (no such trade) shows as a dash.
_AGO = Format(precision=0, scheme=Scheme.fixed, group=Group.yes,
              symbol=Symbol.yes, symbol_suffix="d", nully="\u2014")

# Two-row headers: a group name over short column names. Adjacent identical
# top names merge into one cell (merge_duplicate_headers on the table).
TABLE_COLUMNS = [
    dict(id="name", name=["", "Instrument"], type="text"),
    dict(id="qty", name=["", "Qty"], type="numeric", format=_INT),
    dict(id="net_cost", name=["", "Net cost"], type="text"),
    dict(id="days_deployed", name=["Capital employed", "Days"], type="numeric",
         format=_INT),
    dict(id="avg_capital", name=["Capital employed", "Avg £"], type="numeric",
         format=_INT),
    dict(id="peak_capital", name=["Capital employed", "Peak £"], type="numeric",
         format=_INT),
    dict(id="realised", name=["P&L £", "Realised"], type="numeric", format=_INT),
    dict(id="unrealised", name=["P&L £", "Unrealised"], type="numeric",
         format=_INT),
    dict(id="fx_pnl", name=["P&L £", "of which FX"], type="numeric",
         format=_INT),
    dict(id="total_pnl", name=["P&L £", "Total"], type="numeric", format=_INT),
    # Not "Total": it would sit next to P&L's "Total" and the two merge.
    dict(id="roce_total", name=["ROCE", "To date"], type="numeric", format=_PCT),
    dict(id="roce_annualised", name=["ROCE", "Annualised"], type="numeric",
         format=_PCT),
    # core.risk: daily GBP returns over the days the position was held.
    dict(id="vol", name=["Risk while held", "Vol"], type="numeric", format=_PCT),
    dict(id="max_dd", name=["Risk while held", "Max DD"], type="numeric",
         format=_PCT),
    dict(id="sharpe", name=["Risk while held", "Sharpe"], type="numeric",
         format=_RATIO),
    # How long ago, as "3d"; the date itself is in the hover text.
    dict(id="last_buy", name=["Last buy", "Ago"], type="numeric", format=_AGO),
    dict(id="ret_buy", name=["Last buy", "Return"], type="numeric",
         format=_PCT),
    dict(id="last_sell", name=["Last sell", "Ago"], type="numeric", format=_AGO),
    dict(id="ret_sell", name=["Last sell", "Return"], type="numeric",
         format=_PCT),
]


# Words that make a name long without telling holdings apart. Display only:
# the search box still matches the full name and the id.
_NAME_FILLER = re.compile(
    r"\s+(?:UCITS ETF|UCITS|Corporation|Corp\.?|Inc\.?|plc|PLC|A\.S\.|S\.A\.)"
    r"(?=\s|$)"
)
_CCY_BEFORE_TICKER = re.compile(r"\s+(?:USD|GBP|EUR)(?=\s*\()")


def short_name(name: str) -> str:
    """'iShares Nasdaq US Biotechnology UCITS ETF GBP (BTEK.L)' ->
    'iShares Nasdaq US Biotechnology (BTEK.L)'."""
    s = _CCY_BEFORE_TICKER.sub("", _NAME_FILLER.sub("", name or ""))
    return re.sub(r"\s{2,}", " ", s).strip() or (name or "")


def _days(d, asof: pd.Timestamp) -> int:
    """Whole days from d to asof; -1 when there is no date."""
    if d is None or pd.isna(d):
        return -1
    return int((asof - pd.Timestamp(d).normalize()).days)


def _on(d) -> str | None:
    """'30 Oct 2026', or None when there is no date."""
    return None if d is None or pd.isna(d) else f"{pd.Timestamp(d):%d %b %Y}"


def trade_cells(p: Position, asof: pd.Timestamp) -> dict:
    """The four last-trade columns for one position, plus the hidden fields
    their colours and hover text read.

    last_buy / last_sell  days since the trade (None = never), shown as "3d"
    _buy_on / _sell_on    the trade dates, for the hover text
    _buy_age / _sell_age  days since the last buy / sell; -1 = no colour
                          (closed position, or no such trade)
    _hold_age             days held with no sale since the holding opened;
                          -1 unless that applies. Covers both "never sold" and
                          "sold out once, bought back, not sold since".
    _stale                1 when the mark is older than STALE_MARK_DAYS
    """
    held = p.qty > 0
    sold_in_holding = (held and p.last_sell_date is not None
                       and p.held_since is not None
                       and p.last_sell_date >= p.held_since)
    mark_age = _days(p.mark_date, asof)
    return {
        "last_buy": _days(p.last_buy_date, asof) if p.last_buy_date is not None else None,
        "ret_buy": p.ret_since_buy,
        "last_sell": _days(p.last_sell_date, asof) if p.last_sell_date is not None else None,
        "ret_sell": p.ret_since_sell,
        "_buy_on": _on(p.last_buy_date),
        "_sell_on": _on(p.last_sell_date),
        "_buy_age": _days(p.last_buy_date, asof) if held else -1,
        "_sell_age": _days(p.last_sell_date, asof) if sold_in_holding else -1,
        "_hold_age": (_days(p.held_since, asof)
                      if held and not sold_in_holding else -1),
        "_stale": int(mark_age > STALE_MARK_DAYS),
        "_mark_on": _on(p.mark_date),
    }


def tooltips(rows: list[dict]) -> list[dict]:
    """Hover text for the last-trade cells, one dict per row, same order."""
    out = []
    for r in rows:
        t = {}
        if r.get("_buy_on"):
            t["last_buy"] = f"Bought {r['_buy_on']}"
        if r.get("_sell_on"):
            t["last_sell"] = f"Sold {r['_sell_on']}"
        if r.get("_hold_age", -1) >= 0:
            t["last_sell"] = ((f"Sold {r['_sell_on']}, before this holding opened. "
                               if r.get("_sell_on") else "Never sold. ")
                              + f"Held {r['_hold_age']:,} days without selling")
        if r.get("_stale"):
            note = f"Price from {r['_mark_on']}, so this may be out of date"
            t["ret_buy"] = t["ret_sell"] = note
        if r.get("sharpe") is not None:
            parts = []
            if r.get("_sortino") is not None:
                parts.append(f"Sortino {r['_sortino']:.2f}")
            parts.append(f"{r.get('_risk_days', 0):,} days held")
            if r.get("_short"):
                parts.append(f"under {MIN_DAYS_TO_ANNUALISE} days, too few to rely on")
            t["sharpe"] = " \u00b7 ".join(parts)
        if r.get("_risk_missing"):
            note = f"{r['_risk_missing']:,} held days had no price and are left out"
            t["vol"] = t["max_dd"] = note
        out.append(t)
    return out


# Shown after the name now that there is no Status column. Open and closed
# need no tag: closed rows are greyed out and italic.
STATUS_TAG = {"unpriced": " \u00b7 unpriced", "error": " \u00b7 error"}


def risk_cells(r) -> dict:
    """Vol / Max DD / Sharpe for one row, plus hidden hover fields."""
    if r is None:
        return {"vol": None, "max_dd": None, "sharpe": None,
                "_sortino": None, "_risk_days": 0, "_risk_missing": 0}
    return {"vol": r.vol, "max_dd": r.max_dd, "sharpe": r.sharpe,
            "_sortino": r.sortino, "_risk_days": r.n_days,
            "_risk_missing": r.missing_days}


def positions_to_rows(members: list[Position], asof: pd.Timestamp,
                      risk_by_id: dict | None = None) -> list[dict]:
    instruments = repo.instruments()
    risk_by_id = risk_by_id or {}
    rows = []
    for p in members:
        rows.append({
            "id": p.instrument,
            "name": short_name(p.name) + STATUS_TAG.get(p.status, ""),
            "_full_name": p.name,
            "status": p.status,
            "qty": p.qty,
            "net_cost": fin.fmt_native_cost(
                p.net_cost_native, p.qty, p.currency,
                instruments.get(p.instrument, {}).get("price_unit")),
            "days_deployed": p.days_deployed,
            "avg_capital": p.avg_capital,
            "peak_capital": p.peak_capital,
            "realised": p.realised,
            "unrealised": p.unrealised,
            "fx_pnl": p.fx_pnl,
            "total_pnl": p.total_pnl,
            "roce_total": p.roce_total,
            "roce_annualised": p.roce_annualised,
            # 1/0, not True/False: the table's filter "{_short} = 1" never
            # matches a boolean, so short holdings were never greyed out.
            "_short": int(p.days_deployed < MIN_DAYS_TO_ANNUALISE),
            **trade_cells(p, asof),
            **risk_cells(risk_by_id.get(p.instrument)),
        })
    return rows


def metric(label: str, value: str, sub: str = "") -> html.Div:
    return html.Div(
        [
            html.Div(label, style={"fontSize": "12px", "color": "#6b7280"}),
            html.Div(value, style={"fontSize": "22px", "fontWeight": 600}),
            html.Div(sub, style={"fontSize": "11px", "color": "#9ca3af"}),
        ],
        style={"minWidth": "150px"},
    )


def _render_inner() -> html.Div:
    """Tab body. Called by pages/portfolio.py via the TABS map."""
    data = get_data()
    return html.Div(
        style={"fontFamily": "system-ui, sans-serif"},
        children=[
            html.P(
                "Return on capital employed per instrument. Tick rows to pool them "
                "into a basket — capital-days and P&L are summed, then the rate is "
                "computed once. Figures cover the full ledger and ignore any date "
                "filter set elsewhere.",
                style={"color": "#6b7280", "maxWidth": "70ch"},
            ),

            html.Div([
                dcc.Input(
                    id=PREFIX + "search", type="text",
                    placeholder="Search instruments",
                    style={"width": "300px", "padding": "8px"},
                ),
                html.Button(id=PREFIX + "toggle-closed", n_clicks=0,
                            children=f"Show closed ({data['n_closed']})",
                            style={"marginLeft": "10px", "padding": "8px 12px"}),
                html.Button("Clear selection", id=PREFIX + "clear", n_clicks=0,
                            style={"marginLeft": "10px", "padding": "8px 12px"}),
                html.Span(id=PREFIX + "asof",
                          children=f"as of {data['asof']:%d %b %Y}",
                          style={"marginLeft": "14px", "fontSize": "12px",
                                 "color": "#9ca3af"}),
            ], style={"marginBottom": "12px"}),

            html.Div(
                f"FX note: {len(data['approx'])} non-GBP instruments are marked "
                "using the rate from their most recent trade, not a live rate. "
                "GBP-quoted instruments are exact."
                if data["approx"] else "",
                style={"fontSize": "12px", "color": "#92400e",
                       "background": "#fffbeb", "padding": "8px",
                       "borderRadius": "4px", "marginBottom": "10px"}
                if data["approx"] else {"display": "none"},
            ),

            # Selection lives here, not in the table, so it survives filtering.
            dcc.Store(id=PREFIX + "selected", data=[]),
            dcc.Store(id=PREFIX + "version", data=0),

            dash_table.DataTable(
                id=PREFIX + "table",
                columns=TABLE_COLUMNS,
                data=data["rows"],
                row_selectable="multi",
                selected_row_ids=[],
                sort_action="native",
                page_action="none",
                merge_duplicate_headers=True,
                tooltip_data=tooltips(data["rows"]),
                tooltip_delay=300,
                tooltip_duration=None,
                **TABLE_STYLE,
            ),

            html.Div(id=PREFIX + "summary", style={
                "display": "flex", "gap": "28px", "flexWrap": "wrap",
                "margin": "28px 0 8px 0", "padding": "16px",
                "background": "#f9fafb", "borderRadius": "6px",
            }),
            html.Div(id=PREFIX + "note",
                     style={"fontSize": "12px", "color": "#6b7280"}),

            dcc.Graph(id=PREFIX + "exposure"),

            html.Details([
                html.Summary("Validation",
                             style={"cursor": "pointer", "fontWeight": 600}),
                html.Div(id=PREFIX + "validation",
                         style={"fontSize": "13px", "marginTop": "8px"}),
            ], style={"marginTop": "16px"}),
        ],
    )


def render() -> html.Div:
    """Wrapper so a data problem shows inside the tab instead of taking the
    gunicorn worker down and blacking out all of Pusula on 8060."""
    try:
        return _render_inner()
    except Exception as exc:                      # noqa: BLE001 - deliberate
        import traceback
        return html.Div(
            style={"padding": "16px", "fontFamily": "system-ui, sans-serif"},
            children=[
                html.H3("P&L analysis"),
                html.P("This tab could not load. The rest of Pusula is unaffected.",
                       style={"color": "#b91c1c", "fontWeight": 600}),
                html.Pre(
                    f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                    style={"background": "#fef2f2", "padding": "12px",
                           "fontSize": "12px", "whiteSpace": "pre-wrap",
                           "borderRadius": "4px", "overflowX": "auto"},
                ),
            ],
        )


# --------------------------------------------------------------------------
# Callbacks. @callback (not @app.callback) because use_pages imports this
# module while Dash(...) is still being constructed, so no app object exists
# yet. app.py already sets suppress_callback_exceptions=True, which these need
# since the ids are absent from the DOM while another page is showing.
# --------------------------------------------------------------------------


@callback(
    Output(PREFIX + "table", "data"),
    Output(PREFIX + "table", "tooltip_data"),
    Output(PREFIX + "toggle-closed", "children"),
    Input(PREFIX + "search", "value"),
    Input(PREFIX + "toggle-closed", "n_clicks"),
    Input(PREFIX + "version", "data"),
)
def filter_rows(term, n_clicks, _version):
    """Open positions only by default; the button reveals closed ones.

    Hiding a closed row does not deselect it - track_selection keeps picks that
    are currently out of view, so a basket can mix open and closed positions.

    tooltip_data is positional, so it is rebuilt from the same filtered list.
    """
    data = get_data()
    show_closed = bool((n_clicks or 0) % 2)

    rows = data["rows"]
    if not show_closed:
        rows = [r for r in rows if r["status"] == "open"]
    if term:
        t = term.lower()
        rows = [r for r in rows
                if t in r["_full_name"].lower() or t in r["id"].lower()]

    label = "Hide closed" if show_closed else f"Show closed ({data['n_closed']})"
    return rows, tooltips(rows), label


@callback(
    Output(PREFIX + "selected", "data"),
    Input(PREFIX + "table", "selected_row_ids"),
    Input(PREFIX + "clear", "n_clicks"),
    State(PREFIX + "table", "data"),
    State(PREFIX + "selected", "data"),
    prevent_initial_call=True,
)
def track_selection(selected_row_ids, _clear, visible, stored):
    """Merge the table's selection with rows currently hidden by the search.

    DataTable only reports selections among rows it can see, so ticking one
    instrument, searching for another and ticking that would otherwise drop
    the first. Keep hidden picks, replace visible ones.
    """
    if ctx.triggered_id == PREFIX + "clear":
        return []
    visible_ids = {r["id"] for r in (visible or [])}
    kept = [i for i in (stored or []) if i not in visible_ids]
    return kept + list(selected_row_ids or [])


@callback(
    Output(PREFIX + "table", "selected_row_ids"),
    Input(PREFIX + "selected", "data"),
    State(PREFIX + "table", "data"),
)
def reflect_selection(stored, visible):
    visible_ids = {r["id"] for r in (visible or [])}
    return [i for i in (stored or []) if i in visible_ids]


@callback(
    Output(PREFIX + "summary", "children"),
    Output(PREFIX + "note", "children"),
    Output(PREFIX + "exposure", "figure"),
    Output(PREFIX + "validation", "children"),
    Input(PREFIX + "selected", "data"),
    Input(PREFIX + "version", "data"),
)
def update_basket(selected, _version):
    data = get_data()
    by_id = data["by_id"]
    chosen = [by_id[i] for i in (selected or []) if i in by_id]

    if not chosen:
        empty = go.Figure()
        empty.update_layout(
            template="simple_white", height=420,
            annotations=[dict(text="Tick instruments above to build a basket",
                              showarrow=False,
                              font=dict(size=14, color="#9ca3af"))],
            xaxis=dict(visible=False), yaxis=dict(visible=False),
        )
        return [], "", empty, ""

    b = aggregate(chosen)
    can_annualise = b.days_deployed >= MIN_DAYS_TO_ANNUALISE

    cards = [
        metric("Instruments", f"{len(chosen)}"),
        metric("Total P&L", f"£{b.total_pnl:,.0f}",
               f"realised £{b.realised:,.0f} / unrealised £{b.unrealised:,.0f}"),
        metric("Avg capital", f"£{b.avg_capital:,.0f}",
               f"peak £{b.peak_capital:,.0f}"),
        metric("Days deployed", f"{b.days_deployed:,}",
               "union, flat days excluded"),
        metric("ROCE", f"{b.roce_total:.1%}", "over the period"),
        metric("Annualised",
               f"{b.roce_annualised:.1%}" if can_annualise else "—",
               "" if can_annualise else f"under {MIN_DAYS_TO_ANNUALISE} days"),
    ]

    # Risk of the basket as held: each day's summed GBP P&L over summed
    # opening value, so sizes and correlations count. Usually better than
    # the members' average Sharpe - that gap is the diversification.
    risk = data["risk"]
    rb = risk_engine.basket(
        [risk.by_id[p.instrument].daily for p in b.members if p.instrument in risk.by_id],
        risk.rf)
    sortino = f"Sortino {rb['sortino']:.2f}" if rb["sortino"] is not None else ""
    cards += [
        metric("Vol", f"{rb['vol']:.1%}" if rb["vol"] is not None else "—",
               "while held"),
        metric("Max DD", f"{rb['max_dd']:.1%}" if rb["max_dd"] is not None else "—",
               "worst peak to trough"),
        metric("Sharpe", f"{rb['sharpe']:.2f}" if rb["sharpe"] is not None else "—",
               sortino if can_annualise
               else f"under {MIN_DAYS_TO_ANNUALISE} days, too few to rely on"),
    ]

    contrib = sorted(chosen, key=lambda p: p.capital_days, reverse=True)
    note = "Contribution to annualised rate:  " + "   ·   ".join(
        f"{p.name}: {p.capital_days / b.capital_days:.0%} of capital-days, "
        f"{p.roce_annualised:+.1%}"
        for p in contrib
    )

    fig = go.Figure()
    for p in contrib:
        s = p.daily_book.reindex(b.daily_book.index).fillna(0.0)
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=p.name, mode="lines",
            line=dict(width=0.5), stackgroup="one",
            hovertemplate="%{y:,.0f}<extra>%{fullData.name}</extra>",
        ))
    fig.add_hline(y=b.avg_capital, line_dash="dash", line_color="#374151",
                  annotation_text=f"average £{b.avg_capital:,.0f}",
                  annotation_position="top left")
    fig.add_hline(y=b.peak_capital, line_dash="dot", line_color="#9ca3af",
                  annotation_text=f"peak £{b.peak_capital:,.0f}",
                  annotation_position="bottom left")
    fig.update_layout(
        template="simple_white", height=440, hovermode="x unified",
        title="Capital employed (book cost)", yaxis_title="£",
        margin=dict(t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )

    checks = validate(chosen, b, data["trades"])
    gaps = [(p.instrument, risk.by_id[p.instrument].missing_days) for p in chosen
            if p.instrument in risk.by_id and risk.by_id[p.instrument].missing_days]
    checks.append((
        "Every held day has a price (risk columns)",
        not gaps,
        "all priced" if not gaps else
        f"{len(gaps)} with gaps, those days left out: "
        + ", ".join(f"{i} ({n} days)" for i, n in gaps[:5]),
    ))
    checks.append((
        "Risk-free rate for Sharpe",
        risk.rf_source != "zero (no data)",
        risk.rf_source,
    ))
    val = html.Ul([
        html.Li([
            html.Span("pass " if ok else "FAIL ",
                      style={"color": "#15803d" if ok else "#b91c1c",
                             "fontWeight": 600}),
            f"{label} — {detail}",
        ])
        for label, ok, detail in checks
    ])

    return cards, note, fig, val