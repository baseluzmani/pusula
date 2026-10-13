"""
Momentum tab.

Where each instrument in the selected universe sits on its trend (X) against
its recent move (Y), both in units of its own volatility - core/momentum.py
has the sums. The four quadrants:

    Recovering  |  Rising          trend down, bouncing  |  trend up, rising
    ------------+-----------
    Falling     |  Pullback        trend down, falling   |  trend up, dipping

Trails show where a dot has been over the last few steps. Click a dot to show
or hide its trail; "All trails" shows every trail for up to 12 instruments
(above that the chart becomes spaghetti).

Horizon presets (trend / recent / trail spacing), the trail length and the
volatility floor are settings under Data -> Config. They are registered with
their defaults the first time this tab opens, so there is nothing to insert.

Encoding: colour is held vs not held and bubble area is GBP value for
holdings. Asset type is deliberately not a colour: with six or more types the
colours cannot be told apart in a scatter. Use the "Asset type" universe to
look at one type, and the Type column in the table.

Returns are in each instrument's own currency, like the other Markets tabs.
"""

import math

from dash import html, dcc, callback, Input, Output, State, ctx, ALL, no_update
import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core import theme, momentum as mom, finance as fin, valuation as val
from core.repo import market as repo
from core.repo import portfolio as pf
from core.repo import settings
from ui import universe

NONE = "NONE"           # benchmark dropdown value meaning "no benchmark"
MAX_TRAILS = 12         # "All trails" only up to this many dots
MAX_LABELS = 25         # above this many dots, only holdings get a label

PRESET_KEYS = {"Tactical": "MOMENTUM_TACTICAL", "Short": "MOMENTUM_SHORT",
               "Medium": "MOMENTUM_MEDIUM", "Long": "MOMENTUM_LONG"}

# (key, default, type, description) - created on first open if missing.
SETTINGS = [
    ("MOMENTUM_TACTICAL", mom.HORIZON_DEFAULTS["Tactical"], "str",
     "Momentum Tactical preset: trend window, recent window, trail spacing"),
    ("MOMENTUM_SHORT", mom.HORIZON_DEFAULTS["Short"], "str",
     "Momentum Short preset: trend window, recent window, trail spacing"),
    ("MOMENTUM_MEDIUM", mom.HORIZON_DEFAULTS["Medium"], "str",
     "Momentum Medium preset: trend window, recent window, trail spacing"),
    ("MOMENTUM_LONG", mom.HORIZON_DEFAULTS["Long"], "str",
     "Momentum Long preset: trend window, recent window, trail spacing"),
    ("MOMENTUM_TRAIL_POINTS", 6, "int",
     "Momentum: trail points behind each dot (0 = no trails, max 12)"),
    ("MOMENTUM_VOL_FLOOR", mom.VOL_FLOOR, "float",
     "Momentum: annualised volatility floor (0.02 = 2%)"),
]


# --- settings ------------------------------------------------------------

def _horizon(name):
    name = name if name in PRESET_KEYS else "Medium"
    default = mom.HORIZON_DEFAULTS[name]
    return mom.parse_horizon(settings.get(PRESET_KEYS[name], default), default)


def _trail_points():
    try:
        n = int(settings.get("MOMENTUM_TRAIL_POINTS", 6))
    except (TypeError, ValueError):
        n = 6
    return max(0, min(n, 12))


def _vol_floor():
    try:
        f = float(settings.get("MOMENTUM_VOL_FLOOR", mom.VOL_FLOOR))
    except (TypeError, ValueError):
        f = mom.VOL_FLOOR
    # Never zero: a perfectly flat series would divide by it.
    return max(f, 0.001)


# --- layout --------------------------------------------------------------

def render():
    for key, default, vtype, desc in SETTINGS:
        settings.ensure(key, default, vtype, desc)

    horizons = []
    for name in PRESET_KEYS:
        h = _horizon(name)
        horizons.append({"label": f"{name} ({h.trend}/{h.recent})",
                         "value": name})

    bench_opts = [{"label": "None", "value": NONE}]
    bench_opts += [{"label": r.name, "value": r.fund_id}
                   for r in repo.benchmarks().itertuples()]

    small = {"fontSize": "11px", "color": theme.SLATE, "marginRight": "8px"}
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Momentum", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Trend against recent move, each in its own "
                         "volatility. Hollow dots have short history.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Label("Horizon", style=small),
                dcc.RadioItems(id="mom-horizon", options=horizons,
                               value="Medium", inline=True,
                               inputStyle={"marginRight": "5px"},
                               labelStyle={"marginRight": "14px",
                                           "fontSize": "12px",
                                           "color": theme.TEXT}),
                html.Label("Benchmark", style={**small, "marginLeft": "10px"}),
                dcc.Dropdown(id="mom-bench", options=bench_opts, value=NONE,
                             clearable=False, style={"width": "190px",
                                                     "fontSize": "12px"}),
                dcc.Checklist(id="mom-trails",
                              options=[{"label": " All trails", "value": "on"}],
                              value=[], inputStyle={"marginRight": "5px"},
                              labelStyle={"fontSize": "12px",
                                          "color": theme.TEXT},
                              style={"marginLeft": "16px"}),
            ], style={"display": "flex", "alignItems": "center",
                      "flexWrap": "wrap", "gap": "4px"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "flexWrap": "wrap",
                  "gap": "10px", "marginBottom": "14px"}),

        html.Div([
            html.Div(id="mom-info", style={"fontSize": "11px",
                     "color": theme.NEUTRAL, "marginBottom": "6px"}),
            dcc.Graph(id="mom-chart", config={"displayModeBar": False},
                      style={"height": "560px"}),
        ], style=theme.CARD),

        html.Div(html.Div(id="mom-table"),
                 style={**theme.CARD, "overflow": "auto"}),

        dcc.Store(id="mom-rows"),
        dcc.Store(id="mom-sort", data={"col": "x", "asc": False}),
        dcc.Store(id="mom-picked", data=[]),       # dots clicked for a trail
    ])


# --- chart ---------------------------------------------------------------

@callback(
    Output("mom-chart", "figure"),
    Output("mom-info", "children"),
    Output("mom-rows", "data"),
    Input(universe.STORE_ID, "data"),
    Input("mom-horizon", "value"),
    Input("mom-bench", "value"),
    Input("mom-trails", "value"),
    Input("mom-picked", "data"),
)
def _chart(store, horizon_name, bench_id, trails_on, picked):
    if not universe.is_chosen(store):
        return _blank(universe.PROMPT), "", None
    ids = universe.resolve_ids(store)
    if not ids:
        return _blank("No instruments in this universe."), "", None

    h = _horizon(horizon_name)
    points = _trail_points()
    lookback = h.trend_days + points * h.step_days + 35   # 35: holiday cushion
    since = (pd.Timestamp.now().normalize()
             - pd.Timedelta(days=lookback)).strftime("%Y-%m-%d")

    px = repo.prices(ids, min_date=since)
    if not px.empty:
        px = px[~px["asset_type"].isin(repo.EXCLUDED_TYPES)]
    if px.empty:
        return _blank("No prices for this universe."), "", None

    bench, bench_name = None, None
    if bench_id and bench_id != NONE:
        b = repo.prices([bench_id], min_date=since)
        if not b.empty:
            bench = pd.Series(b["close"].to_numpy(), index=b["date"])
            bench_name = b["fund_name"].iloc[-1] or bench_id

    res = mom.trails(px, h, points, bench=bench, vol_floor=_vol_floor())
    now = res[res["step"] == 0].copy()
    if now.empty:
        return _blank("Not enough price history for this horizon."), "", None

    meta = (px.drop_duplicates("fund_id", keep="last")
              .set_index("fund_id")[["fund_name", "asset_type"]])
    held = set(repo.open_position_ids())
    values = _held_values()
    now["name"] = [meta["fund_name"].get(f) or f for f in now["fund_id"]]
    now["type"] = [meta["asset_type"].get(f) or "\u2014" for f in now["fund_id"]]
    now["held"] = now["fund_id"].isin(held)
    now["value"] = [(values or {}).get(f) for f in now["fund_id"]]

    all_trails = bool(trails_on) and len(now) <= MAX_TRAILS
    if points == 0:
        trail_ids = set()
    elif all_trails:
        trail_ids = set(now["fund_id"])
    else:
        trail_ids = set(picked or []) & set(now["fund_id"])
    fig = _figure(now, res, trail_ids, h, bench_name)

    latest = now["as_of"].max()
    parts = [f"As of {latest:%d %b %Y}",
             f"{h.trend} trend, {h.recent} recent",
             "own-currency returns"]
    if bench_name:
        parts.append(f"relative to {bench_name}")
    elif bench_id and bench_id != NONE:
        parts.append("benchmark has no prices - showing absolute")
    if points == 0:
        pass
    elif all_trails:
        parts.append(f"trails: {points} points, every {h.step}")
    else:
        if trails_on:
            parts.append(f"all trails only up to {MAX_TRAILS} instruments")
        parts.append("click a dot to show its trail")
    if now["short"].any():
        parts.append(f"{int(now['short'].sum())} short history")
    if values is None:
        parts.append("GBP values unavailable - bubbles not sized")

    data = {"trend": h.trend, "recent": h.recent,
            "rows": _records(now)}
    return fig, " \u00b7 ".join(parts), data


def _held_values():
    """{fund_id: GBP value} for open positions, the same way the Holdings
    page values them. None if valuation fails, so the chart still draws."""
    try:
        latest = pf.latest_prices()
        rates = fin.fx_rates(latest)
        valued = val.value_holdings(pf.holdings(), pf.instruments(),
                                    fin.latest_price_map(latest),
                                    rates["USD"], rates)
    except Exception:            # sizing is cosmetic; never block the chart
        return None
    if valued.empty:
        return {}
    valued = valued[valued["value"].notna() & (valued["value"] > 0)]
    return dict(zip(valued["fund_id"], valued["value"].astype(float)))


def _figure(now, res, trail_ids, h, bench_name):
    fig = go.Figure()
    now = now.copy()

    # Marker size: diameter ~ sqrt(value), so bubble AREA is proportional to
    # the GBP value held. Holdings with no value get a mid size; other
    # instruments a small fixed one.
    vmax = max([v for v in now["value"] if _ok(v)] or [1.0])
    now["size"] = [(10 + 28 * math.sqrt(v / vmax) if _ok(v) else 12) if held
                   else 9 for v, held in zip(now["value"], now["held"])]
    label_all = len(now) <= MAX_LABELS
    now["label"] = [_label(f, n) if (label_all or held) else ""
                    for f, n, held in zip(now["fund_id"], now["name"],
                                          now["held"])]

    trail = res[res["fund_id"].isin(trail_ids)]

    # Symmetric axes around zero, never tighter than +-2 sigma, so the
    # quadrants keep their meaning and the scale does not jump between views.
    xs = pd.concat([now["x"], trail["x"]])
    ys = pd.concat([now["y"], trail["y"]])
    xr = max(2.0, float(np.nanmax(np.abs(xs))) * 1.15)
    yr = max(2.0, float(np.nanmax(np.abs(ys))) * 1.15)
    now["textpos"] = _place_labels(now["x"].tolist(), now["y"].tolist(),
                                   now["label"].tolist(), now["size"].tolist(),
                                   xr, yr)

    for x, y, xa, ya, text in [(1, 1, "right", "top", "Rising"),
                               (1, 0, "right", "bottom", "Pullback"),
                               (0, 0, "left", "bottom", "Falling"),
                               (0, 1, "left", "top", "Recovering")]:
        fig.add_annotation(x=x, y=y, xref="paper", yref="paper",
                           xanchor=xa, yanchor=ya, text=text.upper(),
                           showarrow=False,
                           font=dict(size=11, color=theme.NEUTRAL))

    # Trails first, so the dots sit on top. The oldest point carries its
    # date, which shows which way the trail runs.
    held_ids = set(now.loc[now["held"], "fund_id"])
    for fid, g in trail.groupby("fund_id"):
        if len(g) < 2:
            continue
        g = g.sort_values("step", ascending=False)          # oldest first
        colour = theme.CHART_HELD if fid in held_ids else theme.CHART_OTHER
        first = g["as_of"].iloc[0]
        when = [f"{first.day} {first:%b}"] + [""] * (len(g) - 1)
        fig.add_trace(go.Scatter(
            x=g["x"], y=g["y"], mode="lines+markers+text", text=when,
            textposition="bottom center",
            textfont=dict(size=9, color=theme.NEUTRAL),
            line=dict(color=colour, width=1.5),
            marker=dict(size=5, color=colour),
            opacity=0.6, hoverinfo="skip", showlegend=False))

    rel = f" vs {bench_name}" if bench_name else ""
    vol_label = "Tracking error" if bench_name else "Volatility"
    # Every number is formatted here in Python and passed as text. Plotly's
    # own hover formats ignore a leading "+" (e.g. :+.1f) and fall back to
    # printing the raw float with 15 decimals.
    hover = ("<b>%{customdata[0]}</b><br>"
             "%{customdata[1]} \u00b7 %{customdata[2]}<br>"
             f"Trend {h.trend}{rel}: " + "%{customdata[3]}<br>"
             f"Recent {h.recent}{rel}: " + "%{customdata[4]}<br>"
             f"{vol_label}: " + "%{customdata[5]}<br>"
             "%{customdata[6]}Price date %{customdata[7]}"
             "%{customdata[8]}<extra></extra>")

    groups = [(True, "Held", theme.CHART_HELD),
              (False, "Not held", theme.CHART_OTHER)]
    present = [g for g in groups if (now["held"] == g[0]).any()]
    # Draw "Not held" first so the grey dots sit underneath the holdings and
    # their labels; legendrank keeps "Held" first in the legend.
    for is_held, label, colour in reversed(present):
        legend_done = False
        # Filled dots and hollow (short-history) dots are separate traces, so
        # the legend always shows the filled marker.
        for short in (False, True):
            d = now[(now["held"] == is_held) & (now["short"] == short)]
            if d.empty:
                continue
            # The last field (fund_id) is what a click reads to toggle the
            # trail.
            custom = list(zip(
                d["name"], d["type"], d["quadrant"],
                [_pct_sigma(r, z) for r, z in zip(d["r_trend"], d["x"])],
                [_pct_sigma(r, z) for r, z in zip(d["r_recent"], d["y"])],
                [f"{v:.0f}%" for v in d["vol"]],
                [theme.money(v) + " held<br>" if _ok(v) else ""
                 for v in d["value"]],
                [f"{t:%d %b %Y}" for t in d["as_of"]],
                ["<br><i>Short history</i>" if short else ""] * len(d),
                d["fund_id"],
            ))
            fig.add_trace(go.Scatter(
                x=d["x"], y=d["y"], mode="markers+text",
                name=label, legendgroup=label, showlegend=not legend_done,
                legendrank=1 if is_held else 2,
                text=d["label"], textposition=d["textpos"].tolist(),
                textfont=dict(size=10, color=theme.TEXT),
                marker=dict(size=d["size"].tolist(), color=colour,
                            opacity=0.9,
                            symbol="circle-open" if short else "circle",
                            # Filled dots get a white ring so overlaps stay
                            # readable; hollow ones keep their colour.
                            line=dict(width=2, color=colour if short
                                      else theme.SURFACE)),
                customdata=custom, hovertemplate=hover))
            legend_done = True

    fig.update_layout(
        height=560, margin=dict(l=56, r=20, t=10, b=50),
        plot_bgcolor=theme.SURFACE, paper_bgcolor=theme.SURFACE,
        font=dict(family=theme.FONT_UI, color=theme.TEXT, size=11),
        hovermode="closest", hoverlabel=dict(font_size=11),
        showlegend=len(present) > 1,
        legend=dict(orientation="h", x=1, xanchor="right", y=1.02,
                    yanchor="bottom", font=dict(size=11)))
    axis = dict(showgrid=True, gridcolor=theme.LINE_SOFT, zeroline=True,
                zerolinecolor=theme.CHART_ZERO, zerolinewidth=1.5,
                tickfont=dict(size=10), ticksuffix="\u03c3")
    fig.update_xaxes(**axis, range=[-xr, xr],
                     title=dict(text=f"Trend: {h.trend} return{rel}, in "
                                     "\u03c3", font=dict(size=11)))
    fig.update_yaxes(**axis, range=[-yr, yr],
                     title=dict(text=f"Recent: {h.recent} return{rel}, in "
                                     "\u03c3", font=dict(size=11)))
    return fig


@callback(
    Output("mom-picked", "data"),
    Output("mom-chart", "clickData"),
    Input("mom-chart", "clickData"),
    State("mom-picked", "data"),
    prevent_initial_call=True,
)
def _pick(click, picked):
    """Click a dot to show its trail, click again to hide it. clickData is
    reset to None afterwards, otherwise a second click on the same dot would
    not register as a change."""
    if not click:
        return no_update, no_update
    try:
        fid = click["points"][0]["customdata"][9]
    except (KeyError, IndexError, TypeError):
        return no_update, None
    picked = list(picked or [])
    if fid in picked:
        picked.remove(fid)
    else:
        picked.append(fid)
    return picked, None


def _place_labels(xs, ys, texts, sizes, xr, yr, width=1000, height=500):
    """
    A text position for each dot so labels collide as little as possible.

    Greedy: biggest dots first, each label tries above, below, right, left of
    its dot and takes the spot that overlaps fewest labels and dots already
    placed. Works in approximate pixels for a typical desktop plot; on a
    narrow screen it still helps, it just cannot be perfect.
    """
    sx, sy = width / (2 * xr), height / (2 * yr)          # pixels per sigma
    dots = [(x * sx, y * sy, s / 2) for x, y, s in zip(xs, ys, sizes)]
    placed = []
    out = ["top center"] * len(xs)

    def hits(box, own):
        # Covering another label is much worse than covering a dot, so a
        # label clash counts three times.
        x0, x1, y0, y1 = box
        n = 3 * sum(1 for a in placed
                    if x0 < a[1] and a[0] < x1 and y0 < a[3] and a[2] < y1)
        n += sum(1 for j, (cx, cy, r) in enumerate(dots) if j != own
                 and x0 < cx + r and cx - r < x1 and y0 < cy + r and cy - r < y1)
        return n

    for i in sorted(range(len(xs)), key=lambda k: -sizes[k]):
        if not texts[i]:
            continue
        cx, cy, r = dots[i]
        w, hgt = 6.2 * len(texts[i]) + 4, 13
        options = [
            ("top center", (cx - w / 2, cx + w / 2, cy + r + 2, cy + r + 2 + hgt)),
            ("bottom center", (cx - w / 2, cx + w / 2, cy - r - 2 - hgt, cy - r - 2)),
            ("middle right", (cx + r + 3, cx + r + 3 + w, cy - hgt / 2, cy + hgt / 2)),
            ("middle left", (cx - r - 3 - w, cx - r - 3, cy - hgt / 2, cy + hgt / 2)),
        ]
        # min() keeps the first option on a tie, so "above" wins by default.
        pos, box = min(options, key=lambda o: hits(o[1], i))
        out[i] = pos
        placed.append(box)
    return out


def _pct_sigma(r, z) -> str:
    """'-25.1% (-1.03\u03c3)' - a return and its score, for the hover."""
    if r is None or z is None or not (np.isfinite(r) and np.isfinite(z)):
        return "\u2014"
    return f"{r:+.1f}% ({z:+.2f}\u03c3)"


def _ok(v) -> bool:
    """A usable positive number (not None, not NaN)."""
    return v is not None and bool(np.isfinite(v)) and v > 0


def _label(fund_id, name):
    """Short on-chart label: the ticker where there is a clean one, else a
    shortened name."""
    if fund_id.startswith("YF:"):
        t = fund_id[3:]
        if not any(ch in t for ch in "=^"):
            return t.split(".")[0]
    name = str(name)
    if fund_id.startswith("COMPOSITE:"):
        name = name.replace("HSBC Pension ", "")
    return _short(name, 18)


def _records(now):
    """Rows for the table store: JSON-safe (dates as text, NaN as None)."""
    out = []
    for r in now.to_dict("records"):
        rec = {}
        for k in ("fund_id", "name", "type", "quadrant", "x", "y",
                  "r_trend", "r_recent", "vol", "value", "short", "held"):
            v = r.get(k)
            if isinstance(v, (float, np.floating)) and not np.isfinite(v):
                v = None
            elif isinstance(v, np.generic):
                v = v.item()
            if k == "value" and not _ok(v):
                v = None
            rec[k] = v
        rec["as_of"] = r["as_of"].strftime("%Y-%m-%d")
        out.append(rec)
    return out


# --- table ---------------------------------------------------------------

@callback(
    Output("mom-table", "children"),
    Output("mom-sort", "data"),
    Input("mom-rows", "data"),
    Input({"type": "mom-sort", "col": ALL}, "n_clicks"),
    State("mom-sort", "data"),
)
def _table(data, _clicks, sort):
    trig = ctx.triggered_id
    if isinstance(trig, dict) and trig.get("type") == "mom-sort":
        col = trig["col"]
        sort = ({"col": col, "asc": not sort["asc"]}
                if sort["col"] == col else {"col": col, "asc": False})
    if not data or not data.get("rows"):
        return "", sort

    df = pd.DataFrame(data["rows"])
    if sort["col"] in df.columns:
        df = df.sort_values(sort["col"], ascending=sort["asc"],
                            na_position="last")

    cols = [("name", "Instrument", "left"), ("type", "Type", "left"),
            ("quadrant", "Quadrant", "left"),
            ("x", "Trend \u03c3", "right"), ("y", "Recent \u03c3", "right"),
            ("r_trend", f"{data['trend']} %", "right"),
            ("r_recent", f"{data['recent']} %", "right"),
            ("vol", "Vol", "right"), ("value", "Value", "right"),
            ("as_of", "Price date", "right")]

    def arrow(col):
        if sort["col"] == col:
            return " \u25b2" if sort["asc"] else " \u25bc"
        return " \u21c5"

    head = html.Thead(html.Tr([
        html.Th(f"{label}{arrow(key)}", id={"type": "mom-sort", "col": key},
                n_clicks=0,
                style={"background": theme.INK, "color": theme.SURFACE,
                       "padding": "5px 8px", "fontSize": "10px",
                       "fontWeight": 600, "textAlign": align,
                       "whiteSpace": "nowrap", "cursor": "pointer",
                       "userSelect": "none", "position": "sticky", "top": 0})
        for key, label, align in cols]))

    cell = {"padding": "4px 8px", "fontSize": "11px", "whiteSpace": "nowrap",
            "borderBottom": f"1px solid {theme.LINE}"}
    num = {**cell, **theme.NUM, "textAlign": "right", "color": theme.INK}
    body = []
    for r in df.to_dict("records"):
        name = _short(r["name"], 34) + (" \u25e6" if r["short"] else "")
        body.append(html.Tr([
            html.Td(html.Span(name, title=str(r["name"])),
                    style={**cell, "fontWeight": 600 if r["held"] else 400,
                           "color": theme.INK}),
            html.Td(r["type"], style={**cell, "color": theme.SLATE}),
            html.Td(r["quadrant"] or "\u2014",
                    style={**cell, "color": theme.TEXT}),
            _sigma_cell(r["x"], num), _sigma_cell(r["y"], num),
            html.Td(_pct(r["r_trend"]), style=num),
            html.Td(_pct(r["r_recent"]), style=num),
            html.Td(f"{r['vol']:.0f}%" if r["vol"] is not None else "\u2014",
                    style=num),
            html.Td(theme.money(r["value"]) if _ok(r["value"]) else "\u2014",
                    style=num),
            html.Td(pd.Timestamp(r["as_of"]).strftime("%d %b"), style=num),
        ]))
    note = html.Div("Bold = held. \u25e6 = short history.",
                    style={"fontSize": "10px", "color": theme.NEUTRAL,
                           "marginTop": "8px"})
    return html.Div([html.Table([head, html.Tbody(body)],
                                style={"width": "100%",
                                       "borderCollapse": "collapse"}),
                     note]), sort


def _sigma_cell(v, style):
    if v is None:
        return html.Td("\u2014", style=style)
    return html.Td(f"{v:+.2f}", style={**style, "fontWeight": 600,
                   "background": theme.heat_rgb(v, cap=2.0)})


def _pct(v):
    return f"{v:+.1f}%" if v is not None else "\u2014"


# --- helpers -------------------------------------------------------------

def _short(name, n=26):
    s = str(name)
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def _blank(msg):
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=12, color=theme.NEUTRAL))
    fig.update_layout(height=560, plot_bgcolor=theme.SURFACE,
                      paper_bgcolor=theme.SURFACE,
                      margin=dict(l=40, r=40, t=10, b=40),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig
