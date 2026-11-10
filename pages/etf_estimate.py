"""
ETF -> Day estimate.

An indicative move for funds that price late or not at all during the day -
the HSBC pension lines, SEMI after London closes, UC14 - worked out from
what they hold and the latest Yahoo prices. Under each estimate, a backtest
of the same weights against the fund's own price, on the same day and on the
next one, says how far to trust it and which day the move will be booked on.

Rendered as a tab inside pages/etf.py, which calls render(). Not a
registered page. No SQL: data comes from core/repo/fundest.py, arithmetic
from core/fundest.py.

What is estimated is listed in etf_est_targets (seeded with SEMI, every
COMPOSITE:HSBC_* composite and the UC14 / AIGA futures baskets), edited on
Data -> ETF targets. Holdings without a Yahoo instrument are reported as not
priced; Add missing instruments on that tab (scripts/add_constituents.py)
creates them.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dash_table, dcc, html
from dash.dash_table.Format import Format, Scheme, Sign, Symbol

from core import fundest as fe
from core import theme
from core.repo import fundest as repo
from ui import figures
from ui.layout import card, placeholder

PREFIX = "est-"
LONDON = ZoneInfo("Europe/London")
WINDOW_DAYS = 150           # calendar days of prices read, ~100 trading days

_RET = Format(precision=2, scheme=Scheme.fixed, sign=Sign.positive,
              symbol=Symbol.yes, symbol_suffix="%", nully="—")
_WT = Format(precision=1, scheme=Scheme.fixed, symbol=Symbol.yes,
             symbol_suffix="%", nully="—")
_PP = Format(precision=2, scheme=Scheme.fixed, symbol=Symbol.yes,
             symbol_suffix="pp", nully="—")
_INT = Format(precision=0, scheme=Scheme.fixed, nully="—")

FIT_LABEL = {"same": "same day", "next": "next day", None: "—"}

SUMMARY_COLS = [
    {"name": "Fund", "id": "label"},
    {"name": "Holdings", "id": "holdings_date"},
    {"name": "Listed", "id": "listed", "type": "numeric", "format": _WT},
    {"name": "Priced", "id": "priced", "type": "numeric", "format": _WT},
    {"name": "Traded", "id": "traded", "type": "numeric", "format": _WT},
    {"name": "Method", "id": "method"},
    {"name": "Before FX", "id": "local", "type": "numeric", "format": _RET},
    {"name": "FX", "id": "fx", "type": "numeric", "format": _RET},
    {"name": "Estimate", "id": "estimate", "type": "numeric", "format": _RET},
    {"name": "Actual", "id": "actual", "type": "numeric", "format": _RET},
    {"name": "Booked", "id": "best"},
    {"name": "Error same", "id": "mae_same", "type": "numeric",
     "format": _PP},
    {"name": "Error next", "id": "mae_next", "type": "numeric",
     "format": _PP},
    {"name": "Days", "id": "days", "type": "numeric", "format": _INT},
]

DETAIL_COLS = [
    {"name": "Holding", "id": "name"},
    {"name": "Yahoo", "id": "symbol"},
    {"name": "Ccy", "id": "currency"},
    {"name": "Weight", "id": "weight_pct", "type": "numeric", "format": _WT},
    {"name": "Local", "id": "local", "type": "numeric", "format": _RET},
    {"name": "FX", "id": "fx", "type": "numeric", "format": _RET},
    {"name": "GBP", "id": "gbp", "type": "numeric", "format": _RET},
    {"name": "Contribution", "id": "contribution", "type": "numeric",
     "format": Format(precision=3, scheme=Scheme.fixed, sign=Sign.positive,
                      symbol=Symbol.yes, symbol_suffix="pp",
                      nully="—")},
    {"name": "Status", "id": "status"},
]

_TABLE = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": theme.INK, "color": theme.SURFACE,
                  "fontWeight": 600, "fontSize": "10.5px",
                  "letterSpacing": "0.05em", "textTransform": "uppercase",
                  "border": "none", "textAlign": "left"},
    style_cell={"padding": "5px 9px", "fontSize": "12px",
                "fontFamily": theme.FONT_UI, "textAlign": "left",
                "color": theme.TEXT, "border": "none",
                "borderBottom": f"1px solid {theme.LINE_SOFT}",
                "whiteSpace": "nowrap", "overflow": "hidden",
                "textOverflow": "ellipsis", "maxWidth": "320px"},
)


def _numeric_cells(cols):
    return [{"if": {"column_id": c["id"]}, "textAlign": "right",
             "fontFamily": theme.FONT_NUM,
             "fontVariantNumeric": "tabular-nums"}
            for c in cols if c.get("type") == "numeric"]


def _signed(*col_ids):
    out = []
    for c in col_ids:
        out += [
            {"if": {"filter_query": f"{{{c}}} > 0", "column_id": c},
             "color": theme.POSITIVE},
            {"if": {"filter_query": f"{{{c}}} < 0", "column_id": c},
             "color": theme.NEGATIVE},
        ]
    return out


def _today() -> str:
    return datetime.now(LONDON).strftime("%Y-%m-%d")


# --- Layout ---------------------------------------------------------------

def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Label("As of", style=theme.CARD_TITLE),
                dcc.DatePickerSingle(id=f"{PREFIX}date", date=_today(),
                                     display_format="DD MMM YYYY"),
            ], style={"marginRight": "16px"}),
            html.Div(
                "Estimate = holdings x latest Yahoo prices, converted to GBP. "
                "Listed = weight of the holdings on file (top 10 for FT "
                "funds); Priced = the part with a Yahoo price, scaled up to "
                "the whole fund; Traded = the part with a price for the "
                "date. Booked = whether the fund's own price has tracked the "
                "estimate on the same day or the next (today's weights over "
                f"the last {fe.BACKTEST_DAYS} trading days); Actual = the "
                "fund's move for the date, on that day. Error = mean "
                "absolute difference.",
                style={"fontSize": "11.5px", "color": theme.SLATE,
                       "maxWidth": "880px"}),
        ], style={"display": "flex", "alignItems": "flex-end",
                  "marginBottom": "14px"}),
        dcc.Loading(html.Div(id=f"{PREFIX}summary"), type="dot",
                    color=theme.NEEDLE),
        dcc.Store(id=f"{PREFIX}store"),
        html.Div([
            html.Label("Breakdown", style=theme.CARD_TITLE),
            dcc.Dropdown(id=f"{PREFIX}pick", clearable=False,
                         style={"width": "420px", "fontSize": "12.5px"}),
        ], style={"margin": "6px 0 12px"}),
        html.Div(id=f"{PREFIX}detail"),
    ])


# --- Computation ------------------------------------------------------------

def _compute(row: dict, as_of: str) -> dict:
    """Everything the page shows for one target. Plain types, so the result
    can sit in a dcc.Store."""
    hold, hdate = repo.constituents(row, as_of)
    residual = row.get("residual_fund_id") or None
    actual_id = row.get("actual_fund_id") or None
    hedged = bool(row.get("hedged"))

    ids = set(hold["fund_id"].dropna()) if not hold.empty else set()
    meta = repo.instrument_meta(list(ids | {residual, actual_id}))
    cur = {r.fund_id: (r.currency or "GBP") for r in meta.itertuples()}
    unit = {r.fund_id: r.price_unit for r in meta.itertuples()}
    known = set(meta["fund_id"]) if not meta.empty else set()
    crosses = {fe.fx_fund_id(c) for c in cur.values()} - {None}

    when = pd.Timestamp(as_of)
    since = (when - timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%d")
    closes = repo.closes(list(ids | crosses | {residual, actual_id}), since)
    upto = (when + timedelta(days=7)).strftime("%Y-%m-%d")
    closes = closes[closes["date"] <= upto]

    summary, detail = fe.estimate(hold, closes[closes["date"] <= as_of], cur,
                                  known, as_of, residual, hedged)
    actual = fe.actual_returns(closes, actual_id, cur.get(actual_id),
                               unit.get(actual_id), hedged)
    bt = fe.backtest(hold, closes, cur, known, actual, residual, hedged,
                     days=10_000)
    if not bt.empty:
        bt = (bt[pd.to_datetime(bt["date"]) <= when]
              .tail(fe.BACKTEST_DAYS).reset_index(drop=True))
    fit = fe.fit(bt)

    # The fund's move for this date, on the day it is booked: the same day,
    # or the next one for a fund struck the morning after.
    booked = fit.get("best") or "same"
    today = bt[pd.to_datetime(bt["date"]) == when] if not bt.empty else bt
    actual_today = (float(today[booked].iloc[0])
                    if not today.empty and pd.notna(today[booked].iloc[0])
                    else np.nan)
    return {
        "target": row["target"], "label": row["label"],
        "holdings_date": hdate or ("basket" if row["kind"] == "basket"
                                   else "—"),
        "summary": summary, "fit": fit, "actual_today": actual_today,
        "detail": detail.to_dict("records"),
        "backtest": ([] if bt.empty else
                     bt.assign(date=pd.to_datetime(bt["date"])
                               .dt.strftime("%Y-%m-%d"))
                       .to_dict("records")),
    }


def _pct(x):
    return None if x is None or pd.isna(x) else round(float(x) * 100, 4)


def _summary_row(r: dict) -> dict:
    s, f = r["summary"], r["fit"]
    return {
        "target": r["target"], "label": r["label"],
        "holdings_date": r["holdings_date"],
        "listed": s["listed_weight"] if s["total"] else None,
        "priced": s["priced_weight"] if s["total"] else None,
        "traded": s["moved_weight"] if s["total"] else None,
        "method": s["method"],
        "local": _pct(s["local"]), "fx": _pct(s["fx"]),
        "estimate": _pct(s["headline"]),
        "actual": _pct(r["actual_today"]),
        "best": FIT_LABEL.get(f.get("best")),
        "mae_same": _pct(f["same"]["mae"]), "mae_next": _pct(f["next"]["mae"]),
        "days": max(f["same"]["n"], f["next"]["n"]) or None,
    }


# --- Callbacks --------------------------------------------------------------

@callback(
    Output(f"{PREFIX}summary", "children"),
    Output(f"{PREFIX}store", "data"),
    Output(f"{PREFIX}pick", "options"),
    Output(f"{PREFIX}pick", "value"),
    Input(f"{PREFIX}date", "date"),
)
def _run(as_of):
    as_of = (as_of or _today())[:10]
    rows = repo.targets()
    if rows.empty:
        return placeholder("No funds to estimate."), None, [], None

    results, errors = [], []
    for row in rows.to_dict("records"):
        try:
            results.append(_compute(row, as_of))
        except Exception as exc:                               # noqa: BLE001
            errors.append(f"{row['label']}: {exc}")

    table = dash_table.DataTable(
        id=f"{PREFIX}table",
        columns=SUMMARY_COLS,
        data=[_summary_row(r) for r in results],
        sort_action="native",
        style_cell_conditional=_numeric_cells(SUMMARY_COLS) + [
            {"if": {"column_id": "label"}, "fontWeight": 600},
            {"if": {"column_id": "estimate"}, "fontWeight": 700},
        ],
        style_data_conditional=_signed("local", "fx", "estimate", "actual"),
        **_TABLE,
    )
    notes = [html.Div(e, style={"color": theme.NEGATIVE, "fontSize": "11.5px"})
             for e in errors]
    options = [{"label": r["label"], "value": r["target"]} for r in results]
    store = {r["target"]: r for r in results}
    first = options[0]["value"] if options else None
    return (card(f"Indicative moves - {as_of}", table, *notes), store,
            options, first)


@callback(
    Output(f"{PREFIX}detail", "children"),
    Input(f"{PREFIX}pick", "value"),
    Input(f"{PREFIX}store", "data"),
)
def _detail(target, store):
    if not store or not target or target not in store:
        return None
    r = store[target]
    detail = pd.DataFrame(r["detail"])
    if detail.empty:
        return card(r["label"], placeholder(
            "No holdings for this fund. Check Fund compare and the "
            "instrument's holdings_id."))

    missing = detail[~detail["status"].isin(
        [fe.STATUS_PRICED, fe.STATUS_NO_MOVE, "proxy"])]
    rows = detail.assign(
        symbol=detail["fund_id"].fillna("").str.replace("YF:", "",
                                                        regex=False),
        local=detail["local"] * 100, fx=detail["fx"] * 100,
        gbp=detail["gbp"] * 100, contribution=detail["contribution"] * 100,
    ).replace({np.nan: None})

    table = dash_table.DataTable(
        columns=DETAIL_COLS, data=rows.to_dict("records"),
        sort_action="native", page_action="none",
        style_cell_conditional=_numeric_cells(DETAIL_COLS),
        style_data_conditional=_signed("local", "fx", "gbp", "contribution")
        + [{"if": {"filter_query": f'{{status}} = "{s}"'},
            "color": theme.NEUTRAL}
           for s in (fe.STATUS_MISSING, fe.STATUS_UNMAPPED,
                     fe.STATUS_NO_PRICES)],
        style_table={"maxHeight": "560px", "overflowY": "auto",
                     "overflowX": "auto"},
        **{k: v for k, v in _TABLE.items() if k != "style_table"},
    )

    note = None
    if not missing.empty:
        w = missing["weight_pct"].sum()
        count = missing["status"].value_counts()
        fixes = {
            fe.STATUS_MISSING: "not in instruments - Data > ETF targets "
                               "> Add missing instruments",
            fe.STATUS_NO_PRICES: "no prices yet - the same button "
                                 "fetches them",
            fe.STATUS_UNMAPPED: "no Yahoo symbol - set it in ETF > "
                                "Ticker map",
        }
        parts = [f"{count[k]} {text}" for k, text in fixes.items()
                 if count.get(k)]
        note = html.Div(
            f"Not priced: {len(missing)} holding(s), {w:.1f}% of the fund "
            f"({'; '.join(parts)}).",
            style={"fontSize": "11.5px", "color": theme.NEEDLE,
                   "marginBottom": "8px"})

    return html.Div([
        card(f"{r['label']} - holdings", note, table),
        card("Estimate against the fund's own price", _chart(r)),
    ])


def _chart(r: dict):
    bt = pd.DataFrame(r["backtest"])
    best = r["fit"].get("best")
    if bt.empty or not best:
        return dcc.Graph(figure=figures.empty(
            "Not enough history yet - prices for the holdings build up "
            "after the next Yahoo import.", height=260),
            config={"displayModeBar": False})
    other = "next" if best == "same" else "same"
    fig = go.Figure()
    fig.add_bar(x=bt["date"], y=bt["est"] * 100, name="Estimate",
                marker_color=theme.CHART_HELD)
    fig.add_scatter(x=bt["date"], y=bt[best] * 100, mode="markers",
                    name=f"Fund, {FIT_LABEL[best]}",
                    marker=dict(color=theme.NEEDLE, size=7))
    fig.add_scatter(x=bt["date"], y=bt[other] * 100, mode="markers",
                    name=f"Fund, {FIT_LABEL[other]}", visible="legendonly",
                    marker=dict(color=theme.NEUTRAL, size=6, symbol="x"))
    f = r["fit"][best]
    fig.update_layout(**figures.base_layout(
        height=300, barmode="overlay",
        yaxis=dict(ticksuffix="%", gridcolor=theme.LINE_SOFT,
                   zerolinecolor=theme.CHART_ZERO),
        legend=dict(orientation="h", y=1.12, x=0),
        margin=dict(l=48, r=20, t=30, b=40),
    ))
    return html.Div([
        html.Div(f"Best fit: {FIT_LABEL[best]} - mean error "
                 f"{f['mae'] * 100:.2f}pp, correlation "
                 f"{f['corr']:.2f}, {f['n']} days."
                 if pd.notna(f["corr"]) else
                 f"Best fit: {FIT_LABEL[best]} - mean error "
                 f"{f['mae'] * 100:.2f}pp, {f['n']} days.",
                 style={"fontSize": "12px", "color": theme.SLATE,
                        "marginBottom": "6px"}),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ])
