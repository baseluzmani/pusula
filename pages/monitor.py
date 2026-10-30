"""
Monitor tab (Markets).

For every instrument in the selected universe: how far it is from its
52-week high, where it sits against its 200-day average, how far it is from
your own buy / sell / review levels, its Momentum quadrant, and a status.
core/monitor.py has the sums and the status rules; the rule thresholds are
settings under Data -> Config, registered with their defaults the first time
this tab opens.

The 50/200 column also gives the trend phase (Uptrend / Weakening /
Downtrend / Improving, from the price against the 50-day and the 50-day
against the 200-day), and Next cross says how many trading days until the
averages cross if the price stays where it is - shown within
MONITOR_CROSS_HORIZON days, with the trend case and the price needed in the
hover.

Click a row to see its one-year chart (52-week high, 200-day average, your
levels, your trades, and a note on the phases) and to set or change its
levels. Levels are stored in the mkt_targets table (core/repo/targets.py),
in the instrument's quote units - pence for a .L listing.
"""

from dash import html, dcc, callback, Input, Output, State, ctx, ALL, no_update
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core import theme, monitor as mon, momentum as mom
from core.repo import market as repo
from core.repo import settings
from core.repo import targets as tg
from ui import universe

# (key, default, type, description) - created on first open if missing.
SETTINGS = [
    ("MONITOR_REVIEW_SIGMA", 2.5, "float",
     "Monitor: Review when the drawdown is at least this many sigma"),
    ("MONITOR_REVIEW_PCT", 25.0, "float",
     "Monitor: Review when the drawdown is at least this many percent"),
    ("MONITOR_WATCH_SIGMA", 1.5, "float",
     "Monitor: Watch when the drawdown is at least this many sigma"),
    ("MONITOR_MA_DAYS", 200, "int",
     "Monitor: moving-average length in trading days"),
    ("MONITOR_MA_FAST_DAYS", 50, "int",
     "Monitor: fast moving average (the 50 in the 50/200 cross)"),
    ("MONITOR_MA_WATCH_DAYS", 10, "int",
     "Monitor: Watch after this many closes below the moving average"),
    ("MONITOR_NEAR_PCT", 5.0, "float",
     "Monitor: a level counts as near within this many percent"),
    ("MONITOR_CROSS_HORIZON", 20, "int",
     "Monitor: show the next 50/200 cross when the price held flat brings "
     "it within this many trading days"),
]

# Next cross cell: shaded within CROSS_NEAR trading days, darker within
# CROSS_SOON - the same tints as a level that is near / reached.
CROSS_NEAR = 10
CROSS_SOON = 5

STATUS_STYLE = {
    "Review": {"background": theme.NEGATIVE, "color": theme.SURFACE},
    "Watch": {"background": theme.NEEDLE, "color": theme.SURFACE},
    "OK": {"color": theme.NEUTRAL},
}


# --- settings ------------------------------------------------------------

def _setting(key, default, cast):
    try:
        return cast(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _rules() -> mon.Rules:
    d = mon.Rules()
    return mon.Rules(
        review_sigma=_setting("MONITOR_REVIEW_SIGMA", d.review_sigma, float),
        review_pct=_setting("MONITOR_REVIEW_PCT", d.review_pct, float),
        watch_sigma=_setting("MONITOR_WATCH_SIGMA", d.watch_sigma, float),
        ma_days=max(20, _setting("MONITOR_MA_DAYS", d.ma_days, int)),
        ma_fast_days=max(5, _setting("MONITOR_MA_FAST_DAYS", d.ma_fast_days,
                                     int)),
        ma_watch_days=_setting("MONITOR_MA_WATCH_DAYS", d.ma_watch_days, int),
        near_pct=_setting("MONITOR_NEAR_PCT", d.near_pct, float),
        cross_horizon=max(1, _setting("MONITOR_CROSS_HORIZON",
                                      d.cross_horizon, int)),
    )


# --- layout --------------------------------------------------------------

def render():
    for key, default, vtype, desc in SETTINGS:
        settings.ensure(key, default, vtype, desc)

    label = {"fontSize": "10px", "color": theme.SLATE, "marginBottom": "3px"}
    box = {"width": "130px", "padding": "6px 8px", "fontSize": "13px",
           "borderRadius": "5px", "border": f"1px solid {theme.LINE}"}

    def field(text_id, input_id, **kw):
        return html.Div([html.Div(id=text_id, style=label),
                         dcc.Input(id=input_id, debounce=False, style=box, **kw)])

    return html.Div([
        html.Div([
            html.Div("Monitor", style={"fontSize": "17px", "fontWeight": 700,
                                       "color": theme.INK}),
            html.Div("Distance from the 52-week high, the 200-day average and "
                     "your own levels. Click a row for its chart and to set "
                     "levels.",
                     style={"fontSize": "12px", "color": theme.SLATE,
                            "marginTop": "2px"}),
        ], style={"marginBottom": "14px"}),

        html.Div([
            html.Div(id="mon-info", style={"fontSize": "11px",
                     "color": theme.NEUTRAL, "marginBottom": "8px"}),
            html.Div(id="mon-table", style={"overflow": "auto",
                                            "maxHeight": "calc(100vh - 260px)"}),
        ], style=theme.CARD),

        # Detail for the clicked row: chart plus the levels form. Always in
        # the layout (hidden until a row is clicked) so the form callbacks
        # always have their components.
        html.Div(id="mon-detail", style={"display": "none"}, children=[
            html.Div(id="mon-detail-title", style=theme.CARD_TITLE),
            dcc.Graph(id="mon-chart", config={"displayModeBar": False},
                      style={"height": "360px"}),
            html.Div(id="mon-phase-note"),
            html.Div([
                field("mon-buy-label", "mon-buy", type="number", min=0),
                field("mon-sell-label", "mon-sell", type="number", min=0),
                field("mon-review-label", "mon-review", type="number", min=0),
                html.Div([html.Div("Note", style=label),
                          dcc.Input(id="mon-note", type="text", debounce=False,
                                    placeholder="Invalidation condition\u2026",
                                    style={**box, "width": "320px"})]),
                html.Button("Save", id="mon-save", n_clicks=0,
                            style=_button(primary=True)),
                html.Button("Clear", id="mon-clear", n_clicks=0,
                            style=_button(primary=False)),
                html.Span(id="mon-msg", style={"fontSize": "12px",
                          "color": theme.SLATE, "alignSelf": "center"}),
            ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap",
                      "alignItems": "flex-end", "marginTop": "8px"}),
        ]),

        dcc.Store(id="mon-rows"),
        dcc.Store(id="mon-sort", data={"col": "status", "asc": True}),
        dcc.Store(id="mon-selected"),
        dcc.Store(id="mon-refresh", data=0),       # bumped after a save
    ])


def _button(primary):
    return {"padding": "7px 16px", "fontSize": "12px", "fontWeight": 600,
            "borderRadius": "5px", "cursor": "pointer",
            "border": f"1px solid {theme.INK if primary else theme.LINE}",
            "background": theme.INK if primary else theme.SURFACE,
            "color": theme.SURFACE if primary else theme.TEXT}


# --- data ----------------------------------------------------------------

@callback(
    Output("mon-rows", "data"),
    Output("mon-info", "children"),
    Input(universe.STORE_ID, "data"),
    Input("mon-refresh", "data"),
)
def _compute(store, _refresh):
    if not universe.is_chosen(store):
        return None, universe.PROMPT
    ids = universe.resolve_ids(store)
    if not ids:
        return None, "No instruments in this universe."

    rules = _rules()
    since = (pd.Timestamp.now().normalize()
             - pd.Timedelta(days=mon.lookback_days(rules))).strftime("%Y-%m-%d")
    px = repo.prices(ids, min_date=since)
    if not px.empty:
        px = px[~px["asset_type"].isin(repo.EXCLUDED_TYPES)]
    if px.empty:
        return None, "No prices for this universe."

    m = mon.metrics(px, tg.all_targets(list(px["fund_id"].unique())), rules)
    if m.empty:
        return None, "Not enough price history."

    meta = repo.instruments(list(m["fund_id"])).set_index("fund_id")
    held = set(repo.open_position_ids())

    # Momentum quadrant on the Medium preset, for context.
    medium = mom.parse_horizon(
        settings.get("MOMENTUM_MEDIUM", mom.HORIZON_DEFAULTS["Medium"]),
        mom.HORIZON_DEFAULTS["Medium"])
    quad = mom.scores(px, medium).set_index("fund_id")["quadrant"].to_dict()

    rows = []
    for r in m.to_dict("records"):
        fid = r["fund_id"]
        inst = meta.loc[fid] if fid in meta.index else {}
        rec = {k: _clean(v) for k, v in r.items()}
        rec.update({
            "name": (inst.get("name") if len(inst) else None) or fid,
            "type": (inst.get("asset_type") if len(inst) else None) or "\u2014",
            "unit": _unit(inst),
            "held": fid in held,
            "quadrant": quad.get(fid),
            "status_rank": mon.STATUS_ORDER[r["status"]],
            "as_of": r["as_of"].strftime("%Y-%m-%d"),
            "high_date": r["high_date"].strftime("%Y-%m-%d"),
            "cross_date": (r["cross_date"].strftime("%Y-%m-%d")
                           if pd.notna(r["cross_date"]) else None),
            "next_date": (r["next_date"].strftime("%Y-%m-%d")
                          if pd.notna(r["next_date"]) else None),
        })
        rows.append(rec)

    n_review = sum(r["status"] == "Review" for r in rows)
    n_watch = sum(r["status"] == "Watch" for r in rows)
    latest = max(r["as_of"] for r in rows)
    info = " \u00b7 ".join([
        f"As of {pd.Timestamp(latest):%d %b %Y}",
        f"{len(rows)} instruments",
        f"{n_review} review, {n_watch} watch",
        f"52-week high, {rules.ma_days}-day average",
        "prices in quote units",
    ])
    return {"rows": rows, "ma_days": rules.ma_days,
            "ma_fast_days": rules.ma_fast_days}, info


def _unit(info) -> str:
    """'p' for pence-quoted instruments, else the currency code."""
    if not len(info):
        return ""
    if str(info.get("price_unit") or "").lower() == "pence":
        return "p"
    return str(info.get("currency") or "")


def _clean(v):
    """JSON-safe: numpy scalars to Python, NaN to None, timestamps left for
    the caller."""
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v


# --- table ---------------------------------------------------------------

COLS = [  # key, label, align, sort key
    ("name", "Instrument", "left", "name"),
    ("type", "Type", "left", "type"),
    ("price", "Price", "right", "price"),
    ("dd", "From high", "right", "dd"),
    ("dd_sigma", "\u03c3", "right", "dd_sigma"),
    ("days_since_high", "Since", "right", "days_since_high"),
    ("vs_ma_fast", "vs 50d", "right", "vs_ma_fast"),
    ("vs_ma", "vs 200d", "right", "vs_ma"),
    ("days_below_ma", "Below", "right", "days_below_ma"),
    ("cross", "50/200", "left", "cross_days"),
    ("next", "Next cross", "left", "next_due"),
    ("buy", "Buy", "right", "dist_buy"),
    ("sell", "Sell", "right", "dist_sell"),
    ("review", "Review", "right", "dist_review"),
    ("quadrant", "Momentum", "left", "quadrant"),
    ("status", "Status", "left", "status"),
    ("note", "Note", "left", "note"),
]


@callback(
    Output("mon-table", "children"),
    Output("mon-sort", "data"),
    Input("mon-rows", "data"),
    Input({"type": "mon-sort", "col": ALL}, "n_clicks"),
    State("mon-sort", "data"),
)
def _table(data, _clicks, sort):
    trig = ctx.triggered_id
    if isinstance(trig, dict) and trig.get("type") == "mon-sort":
        col = trig["col"]
        sort = ({"col": col, "asc": not sort["asc"]}
                if sort["col"] == col else {"col": col, "asc": True})
    if not data or not data.get("rows"):
        return "", sort

    df = pd.DataFrame(data["rows"])
    if sort["col"] == "status":
        df = df.sort_values(["status_rank", "dd_sigma"],
                            ascending=[sort["asc"], True], na_position="last")
    elif sort["col"] in df.columns:
        df = df.sort_values(sort["col"], ascending=sort["asc"],
                            na_position="last")

    ma_days = data.get("ma_days", 200)
    fast_days = data.get("ma_fast_days", 50)

    def header(label):
        # Labels follow the configured lengths, e.g. "vs 50d", "50/200".
        return {"vs 50d": f"vs {fast_days}d", "vs 200d": f"vs {ma_days}d",
                "50/200": f"{fast_days}/{ma_days}"}.get(label, label)

    def arrow(key):
        if sort["col"] == key:
            return " \u25b2" if sort["asc"] else " \u25bc"
        return ""

    head = html.Thead(html.Tr([
        html.Th(f"{header(label)}{arrow(skey)}",
                id={"type": "mon-sort", "col": skey}, n_clicks=0,
                style={"background": theme.INK, "color": theme.SURFACE,
                       "padding": "5px 8px", "fontSize": "10px",
                       "fontWeight": 600, "textAlign": align,
                       "whiteSpace": "nowrap", "cursor": "pointer",
                       "userSelect": "none", "position": "sticky", "top": 0,
                       "zIndex": 1})
        for key, label, align, skey in COLS]))

    cell = {"padding": "4px 8px", "fontSize": "11px", "whiteSpace": "nowrap",
            "borderBottom": f"1px solid {theme.LINE}", "color": theme.INK}
    num = {**cell, **theme.NUM, "textAlign": "right"}

    body = []
    for r in _records(df):
        name = _short(r["name"], 30) + (" \u25e6" if r["short"] else "")
        status_style = {**cell, "fontWeight": 600}
        pill = html.Span(r["status"], title=r["reasons"] or "",
                         style={**STATUS_STYLE[r["status"]],
                                "padding": "1px 7px", "borderRadius": "9px",
                                "fontSize": "10px"})
        body.append(html.Tr([
            html.Td(html.Div(html.Span(name, title=str(r["name"]))),
                    style={**cell, "fontWeight": 600 if r["held"] else 400,
                           "maxWidth": "220px", "overflow": "hidden",
                           "textOverflow": "ellipsis"}),
            html.Td(r["type"], style={**cell, "color": theme.SLATE}),
            html.Td(_price(r["price"], r["unit"]), style=num),
            html.Td(f"{r['dd']:+.1f}%", style={**num, "fontWeight": 600,
                    "background": theme.heat_rgb(r["dd"], cap=25)}),
            html.Td(f"{r['dd_sigma']:+.1f}", style={**num,
                    "background": theme.heat_rgb(r["dd_sigma"], cap=3)}),
            html.Td(f"{r['days_since_high']}d", style=num),
            html.Td(_pct(r["vs_ma_fast"]), style=num),
            html.Td(_pct(r["vs_ma"]), style=num),
            html.Td(f"{r['days_below_ma']}d" if r["days_below_ma"] else "\u2014",
                    style=num),
            _cross_cell(r, fast_days, ma_days, cell),
            _next_cell(r, fast_days, ma_days, cell),
            _level_cell(r["buy_target"], r["dist_buy"], r["buy_state"],
                        r["unit"], num),
            _level_cell(r["sell_target"], r["dist_sell"], r["sell_state"],
                        r["unit"], num),
            _level_cell(r["review_level"], r["dist_review"],
                        r["review_state"], r["unit"], num),
            html.Td(r["quadrant"] or "\u2014", style={**cell,
                    "color": theme.TEXT}),
            html.Td(pill, style=status_style),
            html.Td(_short(r["note"], 34) if r["note"] else "",
                    title=r["note"] or "",
                    style={**cell, "color": theme.SLATE}),
        ], id={"type": "mon-row", "fund_id": r["fund_id"]}, n_clicks=0,
            style={"cursor": "pointer"}))

    note = html.Div("Bold = held \u00b7 \u25e6 = under a year of history \u00b7 "
                    "levels show the move needed to reach them; shaded when "
                    "near, darker when reached \u00b7 next cross = trading "
                    f"days until the {fast_days}/{ma_days} cross if the price "
                    f"stays flat; shaded within {CROSS_NEAR}, darker within "
                    f"{CROSS_SOON} \u00b7 hover a status or a cross for "
                    "the detail",
                    style={"fontSize": "10px", "color": theme.NEUTRAL,
                           "marginTop": "8px"})
    return html.Div([html.Table([head, html.Tbody(body)],
                                style={"width": "100%",
                                       "borderCollapse": "collapse"}),
                     note]), sort


def _records(df):
    """Rows as dicts with every missing value as None. Rebuilding a frame
    from the stored rows turns None into NaN in any column that also holds
    numbers (a review level set on one instrument but not the next), and
    NaN is truthy - it would print as "nan" instead of a dash."""
    return [{k: (None if isinstance(v, float) and v != v else v)
             for k, v in rec.items()} for rec in df.to_dict("records")]


def _cross_cell(r, fast_days, ma_days, style):
    """'Golden \u00b7 12d \u00b7 Uptrend': the 50-day is above the 200-day,
    crossed 12 days ago, and the price is above the 50-day. '>390d' when
    there has been no cross as far back as both averages exist."""
    if not r["cross"]:
        return html.Td("\u2014", style={**style, "color": theme.NEUTRAL})
    golden = r["cross"] == "Golden"
    days = (f"{int(r['cross_days'])}d" if r["cross_days"] is not None
            else f">{int(r['cross_span'] or 0)}d")
    since = (f" since {pd.Timestamp(r['cross_date']):%d %b %Y}"
             if r["cross_date"] else f" for over {days[1:]}")
    title = (f"{fast_days}-day {'above' if golden else 'below'} the "
             f"{ma_days}-day{since} "
             f"({'golden' if golden else 'death'} cross)")
    parts = [html.Span(f"{r['cross']} \u00b7 {days}")]
    if r.get("phase"):
        above = (r["vs_ma_fast"] or 0) >= 0
        title += (f"; price {'above' if above else 'below'} the "
                  f"{fast_days}-day: {r['phase']}")
        parts.append(html.Span(f" \u00b7 {r['phase']}",
                               style={"fontWeight": 400,
                                      "color": theme.SLATE}))
    return html.Td(parts, title=title,
                   style={**style, "fontWeight": 600,
                          "color": theme.POSITIVE if golden
                          else theme.NEGATIVE})


def _next_cell(r, fast_days, ma_days, style):
    """'Golden ~8d': with the price held flat, the 50-day crosses the
    200-day in about 8 trading days. A dash when that is further out than
    the horizon; the hover has the detail either way."""
    if not r.get("next_cross"):
        return html.Td("\u2014", style={**style, "color": theme.NEUTRAL})
    title = _next_title(r, fast_days, ma_days)
    if r["next_due"] is None:
        return html.Td("\u2014", title=title,
                       style={**style, "color": theme.NEUTRAL})
    due = int(r["next_due"])
    bg = (theme.TINT_HIT if due <= CROSS_SOON
          else theme.TINT_NEAR if due <= CROSS_NEAR else None)
    golden = r["next_cross"] == "Golden"
    return html.Td(f"{r['next_cross']} ~{due}d", title=title,
                   style={**style, "fontWeight": 600,
                          "color": theme.POSITIVE if golden
                          else theme.NEGATIVE,
                          **({"background": bg} if bg else {})})


def _next_title(r, fast_days, ma_days):
    """Hover for the Next cross cell: the gap, the flat-price and trend
    cases, and the flat price needed by day 10 and by the horizon."""
    unit, price = r["unit"], r["price"]
    kind = r["next_cross"].lower()
    golden = kind == "golden"
    gap = r["ma_gap"] or 0.0
    lines = [f"{fast_days}-day {abs(gap):.1f}% "
             f"{'above' if gap >= 0 else 'below'} the {ma_days}-day"]

    if r["next_days"]:
        k = int(r["next_days"])
        when = (f" (around {pd.Timestamp(r['next_date']):%d %b %Y})"
                if r["next_date"] else "")
        line = (f"Price flat at {_price(price, unit)}: {kind} cross in "
                f"~{k} trading days{when}")
        if r["next_level"]:
            rel = (price / r["next_level"] - 1) * 100
            line += (f", the averages meeting near "
                     f"{_price(r['next_level'], unit)} - price "
                     f"{abs(rel):.1f}% {'above' if rel >= 0 else 'below'}")
        lines.append(line)
    else:
        lines.append(f"Price flat at {_price(price, unit)}: no {kind} cross "
                     f"within {mon.CROSS_LIMIT} trading days")

    if r["trend_slope"] is not None:
        lines.append(
            f"Price on its {fast_days}-day trend "
            f"({r['trend_slope']:+.2f}% a day): "
            + (f"~{int(r['trend_days'])} trading days" if r["trend_days"]
               else f"no {kind} cross within {mon.CROSS_LIMIT} trading days"))

    side = "at or above" if golden else "at or below"
    needs = []
    for k, be in ((r["be_near_k"], r["be_near"]), (r["be_far_k"], r["be_far"])):
        if not k or be is None:
            continue
        k = int(k)
        if be <= 0:
            needs.append(f"by day {k}: at any price" if golden
                         else f"by day {k}: not possible")
        else:
            needs.append(f"by day {k}: price {side} {_price(be, unit)} "
                         f"({(be / price - 1) * 100:+.1f}%)")
    if needs:
        lines.append(f"{kind.title()} cross " + "; ".join(needs))
    return "\n".join(lines)


def _level_cell(level, dist, state, unit, style):
    if not level:
        return html.Td("\u2014", style={**style, "color": theme.NEUTRAL})
    bg = {"near": theme.TINT_NEAR, "hit": theme.TINT_HIT}.get(state)
    text = f"{_price(level, unit)} ({dist:+.1f}%)"
    if state == "hit":
        text += " \u2713"
    return html.Td(text, style={**style, **({"background": bg} if bg else {})})


# Highlight the clicked row in place (the same CSS class Compare uses), so a
# click does not rebuild the whole table.
@callback(
    Output({"type": "mon-row", "fund_id": ALL}, "className"),
    Input("mon-selected", "data"),
    State({"type": "mon-row", "fund_id": ALL}, "id"),
)
def _mark(selected, ids):
    return ["cmp-picked" if i["fund_id"] == selected else "" for i in ids]


@callback(
    Output("mon-selected", "data"),
    Input({"type": "mon-row", "fund_id": ALL}, "n_clicks"),
    State("mon-selected", "data"),
)
def _select(clicks, selected):
    # A rebuilt table arrives with every n_clicks at 0: not a click.
    if not any(clicks or []):
        return no_update
    trig = ctx.triggered_id
    if not isinstance(trig, dict):
        return no_update
    fid = trig["fund_id"]
    return None if fid == selected else fid     # click again to close


# --- detail: chart + levels form ------------------------------------------

@callback(
    Output("mon-detail", "style"),
    Output("mon-detail-title", "children"),
    Output("mon-chart", "figure"),
    Output("mon-buy", "value"),
    Output("mon-sell", "value"),
    Output("mon-review", "value"),
    Output("mon-note", "value"),
    Output("mon-buy-label", "children"),
    Output("mon-sell-label", "children"),
    Output("mon-review-label", "children"),
    Output("mon-msg", "children", allow_duplicate=True),
    Output("mon-phase-note", "children"),
    Input("mon-selected", "data"),
    Input("mon-rows", "data"),
    prevent_initial_call=True,
)
def _detail(selected, data):
    rows = {r["fund_id"]: r for r in (data or {}).get("rows", [])}
    hidden = {"display": "none"}
    if not selected or selected not in rows:
        return (hidden, "", go.Figure(), None, None, None, "",
                "", "", "", "", "")
    r = rows[selected]
    unit = r["unit"]
    ma_days = data.get("ma_days", 200)
    fast_days = data.get("ma_fast_days", 50)
    shown = {**theme.CARD, "display": "block"}
    msg = "" if ctx.triggered_id == "mon-selected" else no_update
    return (shown, f"{r['name']} \u00b7 {r['status']}"
                   + (f" \u2014 {r['reasons']}" if r["reasons"] else "")
                   + (f" \u00b7 {r['phase']} phase" if r.get("phase") else ""),
            _chart(selected, r, ma_days, fast_days),
            r["buy_target"], r["sell_target"], r["review_level"],
            r["note"] or "",
            f"Buy target ({unit})", f"Sell target ({unit})",
            f"Review level ({unit})", msg,
            _phase_note(r.get("phase"), fast_days, ma_days))


def _phase_note(current, fast_days, ma_days):
    """The four trend phases under the chart, the instrument's own one
    highlighted, and a line on what crosses can and cannot tell you."""
    small = {"fontSize": "11px", "lineHeight": "1.5"}
    rows = []
    for name, text in mon.PHASE_TEXT:
        on = name == current
        rows.append(html.Div([
            html.Span(name, style={"display": "inline-block", "width": "82px",
                                   "fontWeight": 700,
                                   "color": theme.INK if on else theme.TEXT}),
            html.Span(text.format(fast=fast_days, slow=ma_days)),
        ], style={**small, "color": theme.INK if on else theme.SLATE,
                  "padding": "1px 6px", "borderRadius": "4px",
                  **({"background": theme.TINT_NEAR} if on else {})}))
    return html.Div([
        html.Div(f"Trend phase \u2014 where the price sits against the "
                 f"{fast_days}-day average, and the {fast_days}-day against "
                 f"the {ma_days}-day",
                 style={**small, "fontWeight": 600, "color": theme.TEXT,
                        "marginBottom": "2px"}),
        *rows,
        html.Div("Crosses lag turning points by weeks to months: they "
                 "confirm what the price has already done rather than "
                 "forecast it, and much of their timing is set by old prices "
                 "leaving the averages.",
                 style={**small, "color": theme.NEUTRAL, "marginTop": "3px",
                        "padding": "0 6px"}),
    ], style={"margin": "6px 0 10px",
              "borderTop": f"1px solid {theme.LINE_SOFT}", "paddingTop": "8px"})


def _chart(fund_id, r, ma_days, fast_days=50):
    since = (pd.Timestamp.now().normalize()
             - pd.Timedelta(days=365 + int(ma_days * 7 / 5) + 30))
    px = repo.prices([fund_id], min_date=since.strftime("%Y-%m-%d"))
    fig = go.Figure()
    if px.empty:
        return fig
    s = pd.Series(px["close"].to_numpy(), index=px["date"])
    full = mon.with_ma(s, ma_days, fast_days)
    df = full[full.index >= full.index[-1] - pd.Timedelta(days=365)]
    unit = r["unit"]

    fig.add_trace(go.Scatter(
        x=df.index, y=df["close"], mode="lines", name="Price",
        line=dict(color=theme.TEXT, width=1.6),
        hovertemplate="%{x|%d %b %Y}: %{y:,.2f}<extra></extra>"))
    if df["ma"].notna().any():
        fig.add_trace(go.Scatter(
            x=df.index, y=df["ma"], mode="lines", name=f"{ma_days}-day",
            line=dict(color=theme.NEUTRAL, width=1.2, dash="dash"),
            hovertemplate="%{x|%d %b %Y}: %{y:,.2f}<extra>" + f"{ma_days}-day"
                          "</extra>"))
    if df["ma_fast"].notna().any():
        fig.add_trace(go.Scatter(
            x=df.index, y=df["ma_fast"], mode="lines",
            name=f"{fast_days}-day",
            line=dict(color=theme.CHART_HELD, width=1.2),
            hovertemplate="%{x|%d %b %Y}: %{y:,.2f}<extra>"
                          + f"{fast_days}-day</extra>"))

    # Crosses inside the year shown, as diamonds on the 50-day line. Found
    # on the full history so a cross on the first visible day still counts.
    if fast_days < ma_days:
        events = [(d, k) for d, k in mon.crosses(full["ma_fast"], full["ma"],
                                                 full.index)
                  if d >= df.index[0]]
        for kind, colour in (("Golden", theme.POSITIVE),
                             ("Death", theme.NEGATIVE)):
            ds = [d for d, k in events if k == kind]
            if not ds:
                continue
            fig.add_trace(go.Scatter(
                x=ds, y=[full["ma_fast"].loc[d] for d in ds], mode="markers",
                name=f"{kind} cross",
                marker=dict(symbol="diamond", size=10, color=colour,
                            line=dict(width=1, color=theme.SURFACE)),
                hovertemplate=f"{kind} cross " + "%{x|%d %b %Y}<extra></extra>"))

    # Your trades, drawn on the closing price of the day so they sit on the
    # line whatever units the trade itself was recorded in.
    tx = repo.transactions(fund_id)
    if not tx.empty:
        tx = tx[tx["type"].isin(["BUY", "SELL"])
                & (tx["trade_date"] >= df.index[0])]
        for kind, symbol, colour in (("BUY", "triangle-up", theme.POSITIVE),
                                     ("SELL", "triangle-down", theme.NEGATIVE)):
            t = tx[tx["type"] == kind]
            if t.empty:
                continue
            ys = [df["close"].asof(d) for d in t["trade_date"]]
            fig.add_trace(go.Scatter(
                x=t["trade_date"], y=ys, mode="markers", name=kind.title(),
                marker=dict(symbol=symbol, size=11, color=colour,
                            line=dict(width=1, color=theme.SURFACE)),
                customdata=[f"{q:,.4g}" for q in t["quantity"]],
                hovertemplate=f"{kind.title()} " + "%{customdata} units, "
                              "%{x|%d %b %Y}<extra></extra>"))

    levels = [(r["high"], "52-week high", theme.SLATE, "dot")]
    for key, label, colour in (("buy_target", "Buy", theme.POSITIVE),
                               ("sell_target", "Sell", theme.NEEDLE),
                               ("review_level", "Review", theme.NEGATIVE)):
        if r[key]:
            levels.append((r[key], label, colour, "dash"))
    for y, label, colour, dash in levels:
        fig.add_hline(y=y, line=dict(color=colour, width=1.2, dash=dash),
                      annotation_text=f"{label} {_price(y, unit)}",
                      annotation_position="top left",
                      annotation_font=dict(size=10, color=colour))

    # Keep every level on screen even when it is far from the price.
    ys = list(df["close"].dropna()) + [lv[0] for lv in levels]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.06 or hi * 0.05
    fig.update_layout(
        height=360, margin=dict(l=50, r=20, t=10, b=30),
        plot_bgcolor=theme.SURFACE, paper_bgcolor=theme.SURFACE,
        font=dict(family=theme.FONT_UI, color=theme.TEXT, size=11),
        hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", x=0, y=1.08, font=dict(size=10)),
        yaxis=dict(range=[lo - pad, hi + pad], gridcolor=theme.LINE_SOFT,
                   tickfont=dict(size=10)),
        xaxis=dict(gridcolor=theme.LINE_SOFT, tickfont=dict(size=10)))
    return fig


@callback(
    Output("mon-msg", "children"),
    Output("mon-refresh", "data"),
    Input("mon-save", "n_clicks"),
    Input("mon-clear", "n_clicks"),
    State("mon-selected", "data"),
    State("mon-buy", "value"),
    State("mon-sell", "value"),
    State("mon-review", "value"),
    State("mon-note", "value"),
    State("mon-refresh", "data"),
    prevent_initial_call=True,
)
def _save(_s, _c, fund_id, buy, sell, review, note, refresh):
    if not fund_id:
        return "Click a row first.", no_update
    when = pd.Timestamp.now().strftime("%H:%M")
    if ctx.triggered_id == "mon-clear":
        tg.clear(fund_id)
        return f"Cleared {when}", (refresh or 0) + 1
    done = tg.save(fund_id, buy, sell, review, note)
    return f"{done.title()} {when}", (refresh or 0) + 1


# --- helpers -------------------------------------------------------------

def _price(v, unit):
    if v is None:
        return "\u2014"
    text = f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    return f"{text}{unit}" if unit == "p" else f"{text} {unit}".strip()


def _pct(v):
    return f"{v:+.1f}%" if v is not None else "\u2014"


def _short(name, n=26):
    s = str(name)
    return s if len(s) <= n else s[:n - 1] + "\u2026"
