"""
Indicators — registered page, dark.

The only section of Pusula that is not about your money. Everything else
values a position; this is the weather around them, so it is styled apart
rather than made to match. Dark also does what the subject wants: a curve
inverting reads faster against a dark ground than a light one, and the page is
meant to be scanned rather than read.

The palette is the FT's salmon paper inverted onto warm near-black. FT has
been the price source since FTScrapper, which is a better reference than
pastiching a terminal you do not use - and it keeps the section from looking
like a generic dark mode.

Charts are the five from the standalone macro dashboard, restyled. The series
browser below them lists everything collected, including what the charts do
not use: macro_series.dashboard decides what is charted, never what is kept.
"""

from __future__ import annotations

import dash
from dash import html, dcc, callback, Input, Output, State, no_update
import pandas as pd
import plotly.graph_objects as go

from core import finance as fin, valuation as val
from core.repo import macro as repo
from core.repo import portfolio as pf

dash.register_page(__name__, path="/indicators", name="Indicators", order=3)

DEFAULT_FROM = "2025-12-01"

# --- palette --------------------------------------------------------------
INK = "#14100E"        # warm near-black, not grey
PANEL = "#1B1613"
LINE = "#2E2721"
GRID = "#241E1A"
PAPER = "#FFF1E5"      # FT salmon
DIM = "#C9B7A8"
MUTED = "#8A7A6D"
POS = "#4FB98A"
NEG = "#E5605B"
WARN = "#E0A458"
BLUE = "#6FA8DC"
ACCENT = "#F2957B"

MONO = "'IBM Plex Mono', ui-monospace, monospace"

CURVE = [("DGS2", "2Y", NEG), ("DGS5", "5Y", WARN),
         ("DGS10", "10Y", BLUE), ("DGS30", "30Y", POS)]

TENORS = [("DTB3", "3M"), ("DGS1", "1Y"), ("DGS2", "2Y"), ("DGS3", "3Y"),
          ("DGS5", "5Y"), ("DGS7", "7Y"), ("DGS10", "10Y"),
          ("DGS20", "20Y"), ("DGS30", "30Y")]

SPREADS = [("T10Y2Y", "10Y − 2Y", BLUE, None),
           ("T10Y3M", "10Y − 3M", WARN, "3 2")]

REAL = [("DGS10", "10Y nominal", BLUE, None),
        ("DFII10", "10Y real (TIPS)", POS, None),
        ("T10YIE", "10Y breakeven", WARN, "3 2")]

# The regime strip. Each is (series, label, invert, unit) - invert marks the
# ones where a fall is the risk-on reading, so the colour is not simply "up is
# green". VIX rising is not good news.
REGIME = [
    ("VIXCLS",       "VIX",       "CBOE",      True,  ""),
    ("YF:DX-Y.NYB",  "DXY",       "ICE",       True,  ""),
    ("DFII10",       "10Y real",  "DFII10",    False, "%"),
    ("T10Y2Y",       "2s10s",     "US",        False, "%"),
    ("BAMLH0A0HYM2", "HY OAS",    "ICE BofA",  True,  "%"),
    ("DGS30",        "30Y",       "US long",   False, "%"),
]

CARD = {"background": PANEL, "border": f"1px solid {LINE}",
        "padding": "12px 14px 13px", "minWidth": 0}

TITLE = {"fontSize": "10px", "letterSpacing": "0.15em",
         "textTransform": "uppercase", "color": DIM, "fontWeight": 600}

NOTE = {"fontSize": "11px", "color": MUTED, "marginTop": "2px"}


def _picker(pid):
    """A native date input, not dcc.DatePickerSingle.

    The Dash picker renders react-dates' own DOM, whose class names change
    between versions and which ignored every selector aimed at it - white text
    on a white field on a dark page. A native input takes ordinary CSS, and
    colorScheme dark makes the browser render its calendar to match.
    """
    return html.Div([
        html.Label("From", style={
            "fontSize": "9.5px", "fontWeight": 700, "letterSpacing": "0.08em",
            "textTransform": "uppercase", "color": MUTED,
            "marginRight": "8px"}),
        dcc.Input(id=pid, type="date", value=DEFAULT_FROM, debounce=True,
                  style={"background": PANEL, "color": PAPER,
                         "border": f"1px solid {LINE}", "borderRadius": "2px",
                         "padding": "5px 8px", "fontFamily": MONO,
                         "fontSize": "11.5px", "colorScheme": "dark",
                         "outline": "none", "width": "130px"}),
    ], style={"display": "flex", "alignItems": "center"})


def _head(title, note=None, pid=None):
    left = html.Div([html.Div(title, style=TITLE),
                     html.Div(note, style=NOTE) if note else None])
    if pid is None:
        return html.Div(left, style={"marginBottom": "10px"})
    return html.Div([left, _picker(pid)], style={
        "display": "flex", "justifyContent": "space-between",
        "alignItems": "flex-start", "marginBottom": "10px", "gap": "16px"})


def layout():
    return html.Div([
        html.Div([
            html.Div([
                html.H1("Indicators", style={
                    "fontFamily": "'Instrument Serif', Georgia, serif",
                    "fontSize": "26px", "fontWeight": 400, "color": PAPER,
                    "lineHeight": 1, "margin": 0}),
                html.Div("Rates, spreads and the weather around the "
                         "portfolio.", style={**NOTE, "marginTop": "4px"}),
            ]),
            html.Div(id="ind-asof", style={
                "fontFamily": MONO, "fontSize": "11px", "color": MUTED,
                "textAlign": "right"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "borderBottom": f"1px solid {LINE}",
                  "paddingBottom": "12px", "marginBottom": "14px",
                  "gap": "20px", "flexWrap": "wrap"}),

        html.Div(id="ind-strip", style={
            "display": "grid", "gridTemplateColumns": "repeat(6, 1fr)",
            "gap": "1px", "background": GRID, "border": f"1px solid {LINE}",
            "marginBottom": "14px"}),

        html.Div([
            html.Div([
                _head("Treasury curve",
                      "2Y, 5Y, 10Y and 30Y, NBER recessions shaded.",
                      "ind-from"),
                dcc.Graph(id="ind-curve", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1", "marginRight": "12px"}),
            html.Div([
                _head("Gilt curve",
                      "5Y, 10Y, 20Y and 30Y+, with Bank Rate as the floor."),
                dcc.Graph(id="ind-gilt", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1"}),
        ], style={"display": "flex", "marginBottom": "12px"}),

        html.Div([
            html.Div([
                _head("Recession spreads", "Below zero is inverted."),
                dcc.Graph(id="ind-spreads", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1", "marginRight": "12px"}),
            html.Div([
                _head("Real vs nominal",
                      "The gap is expected inflation."),
                dcc.Graph(id="ind-real", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1","marginRight": "12px"}),
            html.Div([
                _head("Volatility", "VIX, against its own year's range."),
                dcc.Graph(id="ind-vix", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1"}),
            html.Div([
                _head("Gilt real & breakeven",
                      "Real yields and implied inflation, direct from the "
                      "index-linked curve."),
                dcc.Graph(id="ind-gilt-real",
                          config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1"}),
        ], style={"display": "flex", "marginBottom": "12px"}),

        html.Div([
            html.Div([
                _head("Curve shape", "The curve on chosen dates."),
                dcc.Graph(id="ind-snapshot", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1", "marginRight": "12px"}),
            html.Div([
                _head("Curve evolution", "One curve per quarter."),
                dcc.Graph(id="ind-evolution", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1"}),
        ], style={"display": "flex", "marginBottom": "12px"}),

        html.Div([
            html.Div([
                _head("Equity", "Rebased to the From date."),
                dcc.Graph(id="ind-equity", config={"displayModeBar": False}),
                html.Table([
                    html.Thead(html.Tr([
                        html.Th("Index", style={
                            "padding": "8px 8px 6px", "fontSize": "9px",
                            "fontWeight": 600, "textTransform": "uppercase",
                            "letterSpacing": "0.08em", "color": MUTED,
                            "textAlign": "left",
                            "borderBottom": f"1px solid {LINE}"}),
                        *[html.Th(h, style={
                            "padding": "8px 8px 6px", "fontSize": "9px",
                            "fontWeight": 600, "textTransform": "uppercase",
                            "letterSpacing": "0.08em", "color": MUTED,
                            "textAlign": "right",
                            "borderBottom": f"1px solid {LINE}"})
                          for h in ("1D", "1M", "Period")]])),
                    html.Tbody(id="ind-equity-rows")],
                    style={"width": "100%", "borderCollapse": "collapse",
                           "marginTop": "6px"}),
            ], style={**CARD, "flex": "1", "marginRight": "12px"}),

            html.Div([
                _head("Commodity signals", "Ratios, not prices."),
                html.Div(id="ind-commodities"),
                dcc.Graph(id="ind-ratios", config={"displayModeBar": False},
                          style={"marginTop": "8px"}),
            ], style={**CARD, "flex": "1", "marginRight": "12px"}),

            html.Div([
                _head("Your themes", "Held, against its own driver."),
                html.Div(id="ind-themes"),
            ], style={**CARD, "flex": "1"}),
        ], style={"display": "flex", "marginBottom": "12px"}),

        html.Div([
            html.Div([
                _head("All series",
                      "Everything collected, including what is not charted. "
                      "Click a row to plot it."),
                html.Div(id="ind-series"),
            ], style={**CARD, "flex": "1", "marginRight": "12px"}),
            html.Div([
                html.Div(id="ind-picked-title", style={**TITLE,
                                                       "marginBottom": "10px"}),
                dcc.Graph(id="ind-picked", config={"displayModeBar": False}),
            ], style={**CARD, "flex": "1"}),
        ], style={"display": "flex"}),
    ], style={"background": INK, "minHeight": "100vh",
              "padding": "18px 20px 28px", "color": PAPER,
              "fontFamily": "'IBM Plex Sans Condensed', system-ui, sans-serif"})


# --- plotting -------------------------------------------------------------

def _dark(fig, height=320, ytitle="", zero=False, legend=True):
    fig.update_layout(
        height=height, hovermode="x unified", showlegend=legend,
        paper_bgcolor=PANEL, plot_bgcolor=PANEL,
        font=dict(family="'IBM Plex Sans Condensed', sans-serif",
                  size=10, color=DIM),
        margin=dict(l=52, r=16, t=8 if not legend else 26, b=32),
        yaxis_title=ytitle,
        hoverlabel=dict(bgcolor="#241E1A", bordercolor=LINE,
                        font=dict(color=PAPER, size=11, family=MONO)),
        legend=dict(orientation="h", y=1.10, x=0, font=dict(size=10),
                    bgcolor="rgba(0,0,0,0)"))
    fig.update_xaxes(showgrid=False, tickfont=dict(size=9.5, color=MUTED),
                     linecolor=LINE, zeroline=False)
    fig.update_yaxes(showgrid=True, gridcolor=GRID,
                     tickfont=dict(size=9.5, color=MUTED, family=MONO),
                     linecolor=LINE, zeroline=zero, zerolinecolor="#4A3E35",
                     title_font=dict(size=10, color=MUTED))
    return fig


def _blank(msg, height=320):
    fig = go.Figure()
    fig.add_annotation(text=msg, x=.5, y=.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(size=12, color=MUTED))
    fig.update_layout(height=height, paper_bgcolor=PANEL, plot_bgcolor=PANEL,
                      margin=dict(l=40, r=40, t=10, b=40))
    return fig


def _shade(fig, lo, hi):
    """NBER recessions as bands, under the traces rather than over them."""
    start = lo.strftime("%Y-%m-%d") if hasattr(lo, "strftime") else str(lo)
    rec = repo.observations("USREC", start=start)
    if rec.empty:
        return
    inside, began = False, None
    for when, row in rec.iterrows():
        if row["value"] == 1 and not inside:
            began, inside = when, True
        elif row["value"] == 0 and inside:
            fig.add_vrect(x0=began, x1=when, fillcolor="#5A4A3C", opacity=.28,
                          layer="below", line_width=0)
            inside = False
    if inside and began is not None:
        fig.add_vrect(x0=began, x1=hi, fillcolor="#5A4A3C", opacity=.28,
                      layer="below", line_width=0)


# --- regime strip ---------------------------------------------------------

@callback(Output("ind-strip", "children"), Output("ind-asof", "children"),
          Input("ind-from", "value"))
def _strip(_since):
    cells = []
    latest_date = None

    for sid, label, sub, invert, unit in REGIME:
        st = repo.range_stats(sid, 365)
        if not st:
            cells.append(html.Div([
                html.Div(label, style={"fontSize": "9.5px",
                                       "letterSpacing": "0.13em",
                                       "textTransform": "uppercase",
                                       "color": MUTED}),
                html.Div("no data", style={"fontFamily": MONO,
                                           "fontSize": "13px",
                                           "color": LINE, "marginTop": "8px"}),
            ], style={"background": PANEL, "padding": "11px 13px"}))
            continue

        latest_date = max(latest_date or st["date"], st["date"])
        rng = (st["hi"] - st["lo"]) or 1
        pct = max(0.0, min(1.0, (st["value"] - st["lo"]) / rng))
        chg = st.get("change")

        # Colour follows meaning, not sign: a falling VIX and a falling
        # spread are not the same news, which is what invert encodes.
        if chg is None or abs(chg) < 1e-9:
            colour, arrow = MUTED, ""
        else:
            good = (chg < 0) if invert else (chg > 0)
            colour = POS if good else NEG
            arrow = "+" if chg > 0 else ""

        dec = 2 if abs(st["value"]) < 100 else 1
        cells.append(html.Div([
            html.Div([
                html.Span(label),
                html.Span(sub, style={"fontSize": "9px", "opacity": .7}),
            ], style={"fontSize": "9.5px", "letterSpacing": "0.13em",
                      "textTransform": "uppercase", "color": MUTED,
                      "display": "flex", "justifyContent": "space-between",
                      "gap": "6px", "marginBottom": "6px"}),

            html.Div([
                html.B(f"{st['value']:,.{dec}f}{unit}", style={
                    "fontFamily": MONO, "fontSize": "21px", "fontWeight": 500,
                    "letterSpacing": "-0.02em", "color": PAPER}),
                html.Span("" if chg is None else f"{arrow}{chg:,.2f}",
                          style={"fontFamily": MONO, "fontSize": "11px",
                                 "fontWeight": 500, "color": colour}),
            ], style={"display": "flex", "alignItems": "baseline",
                      "gap": "8px", "marginBottom": "9px"}),

            # Where today sits in its own year. A level means little without
            # it - VIX at 17 is a different statement in a year that ranged
            # 12-28 than one that ranged 15-18.
            html.Div([
                html.Div(style={"position": "absolute", "top": "5px",
                                "left": 0, "right": 0, "height": "2px",
                                "background": LINE}),
                html.Div(style={"position": "absolute", "top": "5px",
                                "left": 0, "height": "2px",
                                "right": f"{(1-pct)*100:.1f}%",
                                "background": "#3A312A"}),
                html.Div(style={"position": "absolute", "top": "1px",
                                "left": f"calc({pct*100:.1f}% - 0.75px)",
                                "width": "1.5px", "height": "10px",
                                "background": PAPER}),
                html.Div([
                    html.Span(f"{st['lo']:,.{dec}f}"),
                    html.Span(f"{st['hi']:,.{dec}f}"),
                ], style={"position": "absolute", "top": "13px", "left": 0,
                          "right": 0, "display": "flex",
                          "justifyContent": "space-between",
                          "fontFamily": MONO, "fontSize": "8.5px",
                          "color": "#6B5C51"}),
            ], style={"position": "relative", "height": "24px"}),
        ], style={"background": PANEL, "padding": "11px 13px 8px"}))

    stamp = (f"Latest observation {latest_date}" if latest_date
             else "No macro data — run the FRED importer")
    return cells, stamp


# --- time series ----------------------------------------------------------

@callback(Output("ind-curve", "figure"), Output("ind-spreads", "figure"),
          Output("ind-real", "figure"), Input("ind-from", "value"))
def _timeseries(since):
    since = (since or DEFAULT_FROM)[:10]

    df = repo.frame([c[0] for c in CURVE], since)
    if df.empty:
        b = _blank("No macro data — run the FRED importer")
        return b, b, b

    curve = go.Figure()
    for sid, label, colour in CURVE:
        if sid not in df.columns or df[sid].isna().all():
            continue
        curve.add_trace(go.Scatter(
            x=df.index, y=df[sid], mode="lines", name=label,
            line=dict(color=colour, width=1.5),
            hovertemplate="%{y:.2f}%<extra>" + label + "</extra>"))
    _shade(curve, df.index.min(), df.index.max())
    _dark(curve, 330, "Yield (%)")

    sp = repo.frame([s[0] for s in SPREADS], since)
    spreads = go.Figure()
    if not sp.empty:
        for sid, label, colour, dash_ in SPREADS:
            if sid not in sp.columns or sp[sid].isna().all():
                continue
            spreads.add_trace(go.Scatter(
                x=sp.index, y=sp[sid], mode="lines", name=label,
                line=dict(color=colour, width=1.7, dash=dash_),
                hovertemplate="%{y:.2f}%<extra>" + label + "</extra>"))
        spreads.add_hline(y=0, line_dash="dash", line_color="#6B5C51",
                          opacity=.8)
        _shade(spreads, sp.index.min(), sp.index.max())
    _dark(spreads, 262, "Spread (%)", zero=True)

    rl = repo.frame([r[0] for r in REAL], since)
    real = go.Figure()
    if not rl.empty:
        for sid, label, colour, dash_ in REAL:
            if sid not in rl.columns or rl[sid].isna().all():
                continue
            real.add_trace(go.Scatter(
                x=rl.index, y=rl[sid], mode="lines", name=label,
                line=dict(color=colour, width=1.6, dash=dash_),
                hovertemplate="%{y:.2f}%<extra>" + label + "</extra>"))
        _shade(real, rl.index.min(), rl.index.max())
    _dark(real, 262, "Yield (%)", zero=True)

    return curve, spreads, real


def _curve_on(dates):
    """Curve shape on given dates. Uses the last observation on or before
    each, so a weekend or a holiday still draws."""
    frames = {sid: repo.observations(sid) for sid, _ in TENORS}
    out = []
    for d in dates:
        stamp = pd.Timestamp(d)
        xs, ys = [], []
        for sid, label in TENORS:
            f = frames.get(sid)
            if f is None or f.empty:
                continue
            prior = f[f.index <= stamp]
            if prior.empty:
                continue
            xs.append(label)
            ys.append(float(prior["value"].iloc[-1]))
        out.append((d, xs, ys))
    return out


@callback(Output("ind-snapshot", "figure"), Input("ind-from", "value"))
def _snapshot(_since):
    dates = ["2007-06-01", "2020-01-01", "2023-07-01",
             pd.Timestamp.today().strftime("%Y-%m-%d")]
    labels = ["2007 pre-GFC", "2020 pre-COVID", "2023 inversion", "Latest"]
    colours = ["#4A3E35", "#6B5C51", "#8A7A6D", POS]
    widths = [1.4, 1.4, 1.4, 2.6]

    fig, drawn = go.Figure(), 0
    for (d, xs, ys), label, colour, w in zip(_curve_on(dates), labels,
                                             colours, widths):
        if not xs:
            continue
        drawn += 1
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=label,
                                 line=dict(color=colour, width=w),
                                 marker=dict(size=4 if w < 2 else 7),
                                 hovertemplate="%{x}: %{y:.2f}%<extra>"
                                               + label + "</extra>"))
    if not drawn:
        return _blank("No curve data", 262)
    return _dark(fig, 262, "Yield (%)")


@callback(Output("ind-evolution", "figure"), Input("ind-from", "value"))
def _evolution(_since):
    today = pd.Timestamp.today()
    dates = []
    for i in range(8, -1, -1):
        d = today - pd.DateOffset(months=i * 3)
        eom = (pd.Timestamp(year=d.year, month=d.month, day=1)
               + pd.DateOffset(months=1, days=-1))
        dates.append(eom.strftime("%Y-%m-%d"))

    # Dark to light to green: recency reads without the legend.
    colours = ["#2E2721", "#3A312A", "#4A3E35", "#5C4E43",
               "#6B5C51", "#4A6E8A", "#4E86B0", BLUE, POS]
    widths = [1, 1, 1, 1, 1.3, 1.3, 1.7, 2.1, 2.6]

    fig, drawn = go.Figure(), 0
    for (d, xs, ys), colour, w in zip(_curve_on(dates), colours, widths):
        if not xs:
            continue
        drawn += 1
        label = pd.Timestamp(d).strftime("%b %y")
        fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines+markers", name=label,
                                 line=dict(color=colour, width=w),
                                 marker=dict(size=3 if w < 2 else 6,
                                             color=colour),
                                 hovertemplate="%{x}: %{y:.2f}%<extra>"
                                               + label + "</extra>"))
    if not drawn:
        return _blank("No curve data", 262)
    fig = _dark(fig, 262, "Yield (%)")
    fig.update_layout(legend=dict(orientation="h", y=1.10, x=0,
                                  font=dict(size=8.5),
                                  bgcolor="rgba(0,0,0,0)"))
    return fig


# --- series browser -------------------------------------------------------

@callback(Output("ind-series", "children"), Input("ind-from", "value"))
def _series_table(_since):
    df = repo.series(active_only=True)
    if df.empty:
        return html.Div("No series yet.", style={"color": MUTED,
                                                 "fontSize": "12px"})

    def th(text, align="left"):
        return html.Th(text, style={
            "padding": "0 8px 7px", "fontSize": "9px", "fontWeight": 600,
            "textTransform": "uppercase", "letterSpacing": "0.08em",
            "color": MUTED, "textAlign": align,
            "borderBottom": f"1px solid {LINE}"})

    rows, last_cat = [], None
    for r in df.to_dict("records"):
        if r["category"] != last_cat:
            last_cat = r["category"]
            rows.append(html.Tr([html.Td(last_cat, colSpan=5, style={
                "padding": "11px 8px 4px", "fontSize": "9px",
                "fontWeight": 700, "letterSpacing": "0.1em",
                "textTransform": "uppercase", "color": ACCENT})]))

        charted = bool(r.get("dashboard"))
        rows.append(html.Tr([
            html.Td(r["id"], style={"padding": "4px 8px", "fontFamily": MONO,
                                    "fontSize": "11px", "color": PAPER}),
            html.Td(r["name"], style={
                "padding": "4px 8px", "fontSize": "11px", "color": DIM,
                "maxWidth": "210px", "overflow": "hidden",
                "textOverflow": "ellipsis", "whiteSpace": "nowrap"}),
            html.Td(f"{int(r['observations'] or 0):,}", style={
                "padding": "4px 8px", "fontFamily": MONO, "fontSize": "10.5px",
                "textAlign": "right", "color": MUTED}),
            html.Td(r["latest"] or "—", style={
                "padding": "4px 8px", "fontFamily": MONO, "fontSize": "10.5px",
                "textAlign": "right", "color": MUTED}),
            html.Td("●" if charted else "", style={
                "padding": "4px 8px", "fontSize": "9px", "textAlign": "right",
                "color": POS if charted else LINE}),
        ], id={"type": "ind-row", "id": r["id"]},
            style={"cursor": "pointer",
                   "borderBottom": f"1px solid {GRID}"}))

    return html.Div(
        html.Table([
            html.Thead(html.Tr([th("Series"), th("Name"), th("Rows", "right"),
                                th("Latest", "right"), th("", "right")])),
            html.Tbody(rows)],
            style={"width": "100%", "borderCollapse": "collapse"}),
        style={"maxHeight": "440px", "overflowY": "auto"})


@callback(Output("ind-picked", "figure"), Output("ind-picked-title", "children"),
          Input({"type": "ind-row", "id": dash.ALL}, "n_clicks"),
          prevent_initial_call=True)
def _plot_picked(clicks):
    trigger = dash.ctx.triggered_id
    if not trigger or not any(c for c in (clicks or []) if c):
        return no_update, no_update

    sid = trigger["id"]
    df = repo.observations(sid)
    if df.empty:
        return _blank(f"No observations for {sid}", 300), sid

    meta = repo.series(active_only=False)
    row = meta[meta["id"] == sid]
    name = row["name"].iloc[0] if not row.empty else sid
    units = row["units"].iloc[0] if not row.empty else ""

    fig = go.Figure(go.Scatter(
        x=df.index, y=df["value"], mode="lines",
        line=dict(color=ACCENT, width=1.5),
        hovertemplate="%{x|%d %b %Y}: %{y:,.2f}<extra></extra>"))
    _shade(fig, df.index.min(), df.index.max())
    _dark(fig, 300, units or "", legend=False)
    return fig, f"{name} · {sid}"


# ============================================================================
# Everything below is appended to pages/indicators.py.
# The imports at the top of that file need two additions:
#
#     from core import finance as fin
#     from core.repo import portfolio as pf
#
# ============================================================================


# --- bottom row: equity, commodities, themes -------------------------------

# Yahoo fund_ids rather than macro series: these come from the prices table.
EQUITY = [
    ("YF:^GSPC", "S&P 500",  BLUE),
    ("YF:^IXIC", "Nasdaq",   POS),
    ("YF:^FTSE", "FTSE 100", WARN),
    ("YF:^KS11", "KOSPI",    ACCENT),
]

# Ratios rather than prices. A level tells you where something is; a ratio
# tells you what it is doing relative to something else, which is the only
# reason to put copper and gold on the same page.
#
# Gold is USD/oz and copper USD/lb, so gold/copper lands around 600 - the
# conventional quote, and the one whose history means anything. Copper/gold is
# the same information upside down, so it is not shown twice.
GOLD, COPPER, BRENT, HH, TTF, SILVER = (
    "YF:GC=F", "YF:HG=F", "YF:BZ=F", "YF:NG=F", "YF:TTF=F", "YF:SI=F")

# TTF quotes in EUR per MWh and Henry Hub in USD per mmBtu, so the raw
# difference is meaningless. One MWh is 3.412 mmBtu; the euro leg then needs
# converting. Computed here rather than stored, because a derived series has
# to be kept in step with its inputs and this is two divisions.
MWH_TO_MMBTU = 3.412

# The gilt curve has three points, not four: the BoE interactive database
# publishes short, medium and long and nothing below about five years, so
# there is no 2Y to pair with the Treasury 2Y. Bank Rate is drawn as a floor
# instead, which is the honest way to show a curve that starts mid-way.
GILT = [("GBP5Y", "5Y", NEG), ("GBP10Y", "10Y", BLUE),
        ("GBP20Y", "20Y", POS), ("GBP30YZC", "30Y+", ACCENT)]

GILT_REAL = [("GBP10YR", "10Y real", POS, None),
             ("GBP20YR", "20Y real", ACCENT, None),
             ("GBP10YIE", "10Y breakeven", WARN, "3 2")]


def _series_for(fund_id, since):
    df = pf.price_series(fund_id, since)
    if df.empty:
        return pd.Series(dtype=float)
    return pd.Series(df["close"].values,
                     index=pd.to_datetime(df["date"].values))
    
def _last(fund_id, n=1):
    """Most recent close for a Yahoo instrument, or None."""
    df = pf.price_series(fund_id)
    if df.empty:
        return None
    return float(df["close"].iloc[-1])


def _ttf_spread_series(since):
    """TTF less Henry Hub, both in USD per mmBtu."""
    ttf = _series_for(TTF, since)
    hh = _series_for(HH, since)
    eur = _series_for("YF:EURUSD=X", since)
    if ttf.empty or hh.empty:
        return pd.Series(dtype=float)

    idx = ttf.index.union(hh.index)
    ttf = ttf.reindex(idx).ffill()
    hh = hh.reindex(idx).ffill()
    # A missing EURUSD leaves the spread unconvertible rather than wrong: 1.0
    # would silently understate it by about fifteen per cent.
    if eur.empty:
        return pd.Series(dtype=float)
    eur = eur.reindex(idx).ffill()
    return (ttf / MWH_TO_MMBTU * eur) - hh


def _pct(series, days):
    """Change over a trailing window, in percent."""
    if series is None or series.empty or len(series) < 2:
        return None
    cutoff = series.index[-1] - pd.Timedelta(days=days)
    prior = series[series.index <= cutoff]
    if prior.empty or not prior.iloc[-1]:
        return None
    return (series.iloc[-1] / prior.iloc[-1] - 1) * 100


def _band(series, invert=False):
    """Where the latest value sits in its own year, as a signal.

    No judgement in it: top third of the range means high, bottom third means
    low, and invert says which of those is the bad news. A threshold that
    encodes a view would be a view dressed as a measurement.
    """
    if series is None or series.empty:
        return None, None
    year = series[series.index >= series.index[-1] - pd.Timedelta(days=365)]
    if len(year) < 10:
        return None, None
    lo, hi = float(year.min()), float(year.max())
    if hi <= lo:
        return None, None
    pos = (float(series.iloc[-1]) - lo) / (hi - lo)
    if pos >= 0.66:
        state = "against" if invert else "support"
    elif pos <= 0.33:
        state = "support" if invert else "against"
    else:
        state = "neutral"
    return state, pos


# Categories are per-instrument and single-valued; a theme is neither. BHP is
# copper and iron ore, and Industrial Metals sits under the energy transition
# rather than with the miners. So the mapping is written out rather than
# derived, and is meant to be argued with.
#
# (name, categories, driver, high_is_bad, driver label)
THEMES = [
    ("Gold & miners", ["Physical Gold", "Gold Miners", "ETC Gold", "NEM"],
     ("macro", "DFII10"), True,  "Real yields"),
    ("Copper",        ["Copper", "BHP"],
     ("ratio", "goldcopper"), True, "Gold / copper"),
    ("Semis / AI",    ["Semi", "Microsoft"],
     None, None, "no driver yet"),
    ("Gas / LNG",     ["Energy", "Industrial Metals"],
     ("ratio", "ttf"), False, "TTF − Henry Hub"),
    ("Uranium",       ["Uranium"],
     None, None, "no driver yet"),
    ("Financials",    ["Financials", "HSBC", "S&P Global"],
     ("macro", "T10Y2Y"), False, "2s10s"),
]

SIG_STYLE = {
    "support": {"background": "rgba(79,185,138,.13)", "color": POS,
                "border": "1px solid rgba(79,185,138,.3)"},
    "neutral": {"background": "rgba(138,122,109,.12)", "color": MUTED,
                "border": f"1px solid {LINE}"},
    "against": {"background": "rgba(229,96,91,.11)", "color": NEG,
                "border": "1px solid rgba(229,96,91,.28)"},
}


def _chip(state, text=None):
    st = SIG_STYLE.get(state or "neutral", SIG_STYLE["neutral"])
    label = text or (state or "—")
    return html.Span(label.upper(), style={
        **st, "fontSize": "8.5px", "letterSpacing": "0.11em",
        "fontWeight": 600, "padding": "2px 7px", "borderRadius": "2px",
        "whiteSpace": "nowrap", "minWidth": "70px", "textAlign": "center",
        "display": "inline-block"})


@callback(Output("ind-equity", "figure"), Output("ind-equity-rows", "children"),
          Input("ind-from", "value"))
def _equity(since):
    since = (since or DEFAULT_FROM)[:10]
    fig = go.Figure()
    rows = []
    drawn = 0

    for fund_id, label, colour in EQUITY:
        s = _series_for(fund_id, since)
        if s.empty or not s.iloc[0]:
            continue
        drawn += 1
        # Rounded here rather than in the hovertemplate: plotly formats what
        # it is given, and a template that does not match the trace silently
        # falls back to full float precision.
        rebased = ((s / s.iloc[0] - 1) * 100).round(1)
        fig.add_trace(go.Scatter(
            x=rebased.index, y=rebased.values, mode="lines", name=label,
            line=dict(color=colour, width=1.5),
            hovertemplate="%{y:+.1f}%<extra>" + label + "</extra>"))

        cells = [html.Td([
            html.Span(style={"display": "inline-block", "width": "9px",
                             "height": "2px", "background": colour,
                             "verticalAlign": "middle",
                             "marginRight": "7px"}),
            label,
        ], style={"padding": "5px 8px", "fontSize": "11px", "color": DIM})]
        for days in (1, 30, None):
            v = (_pct(s, days) if days
                 else ((s.iloc[-1] / s.iloc[0] - 1) * 100))
            cells.append(html.Td(
                "—" if v is None else f"{v:+.1f}%",
                style={"padding": "5px 8px", "fontFamily": MONO,
                       "fontSize": "11px", "textAlign": "right",
                       "color": MUTED if v is None
                                else (POS if v >= 0 else NEG)}))
        rows.append(html.Tr(cells,
                            style={"borderBottom": f"1px solid {GRID}"}))

    if not drawn:
        return _blank("No index prices", 200), []

    fig.add_hline(y=0, line_dash="dot", line_color="#4A3E35", opacity=.7)
    _dark(fig, 210, "", legend=False)
    fig.update_yaxes(ticksuffix="%")
    return fig, rows


@callback(Output("ind-commodities", "children"),
          Output("ind-ratios", "figure"),
          Input("ind-from", "value"))
def _commodities(since):
    since = (since or DEFAULT_FROM)[:10]

    gold = _series_for(GOLD, since)
    copper = _series_for(COPPER, since)
    brent = _series_for(BRENT, since)
    silver = _series_for(SILVER, since)
    hh = _series_for(HH, since)

    gold_copper = pd.Series(dtype=float)
    if not gold.empty and not copper.empty:
        idx = gold.index.union(copper.index)
        gold_copper = (gold.reindex(idx).ffill()
                       / copper.reindex(idx).ffill()).dropna()

    gold_silver = pd.Series(dtype=float)
    if not gold.empty and not silver.empty:
        idx = gold.index.union(silver.index)
        gold_silver = (gold.reindex(idx).ffill()
                       / silver.reindex(idx).ffill()).dropna()

    ttf_spread = _ttf_spread_series(since)

    signals = [
        ("Brent",           brent,       "{:,.2f}",  False),
        ("Gold",            gold,        "{:,.0f}",  False),
        ("Henry Hub",       hh,          "{:,.2f}",  False),
        ("TTF − Henry Hub", ttf_spread,  "${:,.2f}", False),
        ("Gold / copper",   gold_copper, "{:,.0f}",  True),
        ("Gold / silver",   gold_silver, "{:,.1f}",  True),
    ]

    def th(text, align="left"):
        return html.Th(text, style={
            "padding": "0 8px 7px", "fontSize": "9px", "fontWeight": 600,
            "textTransform": "uppercase", "letterSpacing": "0.08em",
            "color": MUTED, "textAlign": align,
            "borderBottom": f"1px solid {LINE}"})

    rows = []
    for label, s, fmt, invert in signals:
        if s is None or s.empty:
            rows.append(html.Tr([
                html.Td(label, style={"padding": "5px 8px",
                                      "fontSize": "11px", "color": DIM}),
                html.Td("no data", colSpan=3, style={
                    "padding": "5px 8px", "fontSize": "10.5px",
                    "textAlign": "right", "color": LINE}),
            ], style={"borderBottom": f"1px solid {GRID}"}))
            continue

        m1 = _pct(s, 30)
        state, _pos = _band(s, invert)
        rows.append(html.Tr([
            html.Td(label, style={"padding": "5px 8px", "fontSize": "11px",
                                  "color": DIM}),
            html.Td(fmt.format(float(s.iloc[-1])), style={
                "padding": "5px 8px", "fontFamily": MONO, "fontSize": "11.5px",
                "textAlign": "right", "color": PAPER}),
            html.Td("—" if m1 is None else f"{m1:+.1f}%", style={
                "padding": "5px 8px", "fontFamily": MONO, "fontSize": "11px",
                "textAlign": "right",
                "color": MUTED if m1 is None else (POS if m1 >= 0 else NEG)}),
            html.Td(_chip(state), style={"padding": "5px 8px",
                                         "textAlign": "right"}),
        ], style={"borderBottom": f"1px solid {GRID}"}))

    table = html.Table([
        html.Thead(html.Tr([th("Signal"), th("Level", "right"),
                            th("1M", "right"), th("In its year", "right")])),
        html.Tbody(rows)],
        style={"width": "100%", "borderCollapse": "collapse"})

    # Two ratios on one chart would need two axes to be readable, so each is
    # rebased to its own start. The shape is the point, not the level.
    fig = go.Figure()
    for s, label, colour in ((gold_copper, "Gold / copper", WARN),
                             (ttf_spread, "TTF − Henry Hub", ACCENT)):
        if s is None or s.empty or not s.iloc[0]:
            continue
        fig.add_trace(go.Scatter(
            x=s.index, y=((s / s.iloc[0] - 1) * 100).round(1), mode="lines",
            name=label, line=dict(color=colour, width=1.4),
            customdata=s.round(2).values,
            hovertemplate="%{customdata:,.2f} (%{y:+.1f}%)<extra>"
                          + label + "</extra>"))
    if not fig.data:
        fig = _blank("No ratio data", 150)
    else:
        _dark(fig, 150, "", legend=True)
        fig.update_yaxes(ticksuffix="%")
    return table, fig


@callback(Output("ind-themes", "children"), Input("ind-from", "value"))
def _themes(since):
    since = (since or DEFAULT_FROM)[:10]

    # Weight comes from the live portfolio, so a theme's size is what you
    # actually hold rather than a number typed in and left to rot.
    instruments = pf.instruments()
    prices = pf.prices()
    rates = fin.fx_rates(prices)
    price_map = fin.latest_price_map(prices)
    valued = val.value_holdings(pf.holdings(), instruments, price_map,
                                rates["USD"], rates)

    total = 0.0
    by_cat = {}
    if not valued.empty:
        for r in valued.to_dict("records"):
            v = val.clean(r.get("value"))
            if v is None:
                continue
            total += v
            by_cat[r["category"]] = by_cat.get(r["category"], 0.0) + v

    gold = _series_for(GOLD, since)
    copper = _series_for(COPPER, since)
    gold_copper = pd.Series(dtype=float)
    if not gold.empty and not copper.empty:
        idx = gold.index.union(copper.index)
        gold_copper = (gold.reindex(idx).ffill()
                       / copper.reindex(idx).ffill()).dropna()
    ttf_spread = _ttf_spread_series(since)

    out = []
    for name, cats, driver, high_bad, label in THEMES:
        weight = sum(by_cat.get(c, 0.0) for c in cats)
        pct = (weight / total * 100) if total else 0.0

        state = None
        if driver:
            kind, key = driver
            if kind == "macro":
                obs = repo.observations(key)
                s = obs["value"] if not obs.empty else pd.Series(dtype=float)
            else:
                s = gold_copper if key == "goldcopper" else ttf_spread
            state, _ = _band(s, bool(high_bad))

        out.append(html.Div([
            html.Div([
                html.Div(name, style={"fontSize": "12px", "color": PAPER}),
                html.Div(label, style={"fontSize": "10px", "color": MUTED,
                                       "marginTop": "1px"}),
            ]),
            html.Div([
                html.Span(f"{pct:.1f}%", style={
                    "fontFamily": MONO, "fontSize": "12px", "color": DIM,
                    "marginRight": "10px"}),
                _chip(state, None if state else "no driver"),
            ], style={"display": "flex", "alignItems": "center",
                      "justifyContent": "flex-end"}),
        ], style={"display": "grid", "gridTemplateColumns": "1fr auto",
                  "gap": "10px", "alignItems": "center", "padding": "9px 0",
                  "borderBottom": f"1px solid {GRID}"}))

    if total:
        covered = sum(sum(by_cat.get(c, 0.0) for c in t[1]) for t in THEMES)
        out.append(html.Div(
            f"{covered/total*100:.0f}% of the portfolio is in a theme. "
            f"The rest — property, cash, HSBC stock — is not.",
            style={"fontSize": "10px", "color": MUTED, "marginTop": "10px"}))
    return out


@callback(Output("ind-vix", "figure"), Input("ind-from", "value"))
def _vix(since):
    """VIX over the chosen window, with the year's range banded behind it.

    The band is the same idea as the regime strip: a level reads differently
    depending on where it sits in its own recent history, and drawing the
    range removes the need to remember it.
    """
    since = (since or DEFAULT_FROM)[:10]
    df = repo.observations("VIXCLS", since)
    if df.empty:
        return _blank("No VIX data", 330)

    st = repo.range_stats("VIXCLS", 365)
    fig = go.Figure()
    if st:
        fig.add_hrect(y0=st["lo"], y1=st["hi"], fillcolor="#3A312A",
                      opacity=.30, layer="below", line_width=0)

    fig.add_trace(go.Scatter(
        x=df.index, y=df["value"], mode="lines", name="VIX",
        line=dict(color=ACCENT, width=1.4),
        hovertemplate="%{x|%d %b %Y}: %{y:.2f}<extra></extra>"))

    # Twenty is the line above which the market is generally said to be
    # nervous. Convention rather than a rule, so it is drawn faintly.
    fig.add_hline(y=20, line_dash="dot", line_color="#6B5C51", opacity=.7)

    _dark(fig, 330, "", legend=False)
    return fig

@callback(Output("ind-gilt", "figure"), Input("ind-from", "value"))
def _gilt(since):
    """The gilt curve, with Bank Rate drawn as a floor.

    Three tenors rather than four, and starting at five years: the BoE
    interactive database has no point below that, and every letter of the
    alphabet was tried in the tenor position before accepting it. Bank Rate is
    added as a dashed line so the shape reads against something at the front
    rather than floating.

    The curve is steeper than the Treasury one - 4.39 to 5.67 against 4.20 to
    5.17 - which is the comparison the two panels side by side are for.
    """
    since = (since or DEFAULT_FROM)[:10]

    df = repo.frame([g[0] for g in GILT], since)
    if df.empty:
        return _blank("No gilt data — run the BoE importer", 330)

    fig = go.Figure()
    for sid, label, colour in GILT:
        if sid not in df.columns or df[sid].isna().all():
            continue
        fig.add_trace(go.Scatter(
            x=df.index, y=df[sid], mode="lines", name=label,
            line=dict(color=colour, width=1.5),
            hovertemplate="%{y:.2f}%<extra>" + label + "</extra>"))

    rate = repo.frame(["BANKRATE"], since)
    if not rate.empty and "BANKRATE" in rate.columns:
        fig.add_trace(go.Scatter(
            x=rate.index, y=rate["BANKRATE"], mode="lines", name="Bank Rate",
            line=dict(color=MUTED, width=1.2, dash="3 3"),
            hovertemplate="%{y:.2f}%<extra>Bank Rate</extra>"))

    _dark(fig, 330, "Yield (%)")
    return fig

@callback(Output("ind-gilt-real", "figure"), Input("ind-from", "value"))
def _gilt_real(since):
    """Real gilt yields and implied inflation.

    The 20Y real yield is the LDI gauge: it is what moved in 2022, and a
    better read on that stress than any single bond's price - which is why the
    2061 does not need its own importer to answer the question it was meant to.
    """
    since = (since or DEFAULT_FROM)[:10]
    df = repo.frame([g[0] for g in GILT_REAL], since)
    if df.empty:
        return _blank("No gilt real data", 262)

    fig = go.Figure()
    for sid, label, colour, dash_ in GILT_REAL:
        if sid not in df.columns or df[sid].isna().all():
            continue
        fig.add_trace(go.Scatter(
            x=df.index, y=df[sid], mode="lines", name=label,
            line=dict(color=colour, width=1.6, dash=dash_),
            hovertemplate="%{y:.2f}%<extra>" + label + "</extra>"))
    _dark(fig, 262, "Yield (%)", zero=True)
    return fig