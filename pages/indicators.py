"""
Indicators — registered page.

The weather around the portfolio, in three tabs: Rates, Volatility and
Commodity. Each tab opens with its own strip of readings, every one placed in
its own year, and one From date drives every chart on every tab.

It was dark and styled apart from the rest of Pusula. It now takes its
colours, type and cards from core/theme.py like every other page: one look
across the app is worth more than a page that announces it is different.

Two stores feed it. FRED, BoE and CNN series live in macro_series /
macro_observations and are read through core.repo.macro. Yahoo series are
instruments: rows in the instruments table, prices in prices, read through
core.repo.portfolio. A Yahoo id used here that has no instrument row simply
draws as "no data" - which is how a missing one shows itself.

Each callback's outputs sit in a single tab. Only the open tab is in the
layout, so a callback whose outputs straddled two tabs would always be
missing half of them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import dash
from dash import html, dcc, callback, Input, Output, no_update
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core import finance as fin, sentiment, theme, valuation as val
from core.repo import macro as repo
from core.repo import portfolio as pf
from ui.figures import SEQUENCE, base_layout, empty
from ui.layout import subtabs

dash.register_page(__name__, path="/indicators", name="Indicators", order=3)

DEFAULT_FROM = "2025-12-01"
TABS = ["Rates", "Volatility", "Commodity"]

# --- colours ---------------------------------------------------------------
# Series colours are the app's chart sequence, named so a list of series
# reads as a legend rather than a row of indices.
NAVY, BLUE, AMBER, GREEN, PURPLE, CLAY, TEAL = SEQUENCE[:7]
POS, NEG, MUTED = theme.POSITIVE, theme.NEGATIVE, theme.NEUTRAL
REF = theme.CHART_ZERO          # reference lines: zero, VIX 20, ratio 1

FG_COLOURS = {"Extreme fear": theme.ZONE_EXTREME_FEAR,
              "Fear": theme.ZONE_FEAR,
              "Neutral": theme.ZONE_NEUTRAL,
              "Greed": theme.ZONE_GREED,
              "Extreme greed": theme.ZONE_EXTREME_GREED}

# --- series ----------------------------------------------------------------
# Macro series ids (macro_series) and Yahoo fund_ids (instruments). Yahoo ids
# start YF:, which is how range_stats tells the two apart.

CURVE = [("DGS2", "2Y", CLAY), ("DGS5", "5Y", AMBER),
         ("DGS10", "10Y", BLUE), ("DGS30", "30Y", NAVY)]

TENORS = [("DTB3", "3M"), ("DGS1", "1Y"), ("DGS2", "2Y"), ("DGS3", "3Y"),
          ("DGS5", "5Y"), ("DGS7", "7Y"), ("DGS10", "10Y"),
          ("DGS20", "20Y"), ("DGS30", "30Y")]

SPREADS = [("T10Y2Y", "10Y − 2Y", BLUE, None),
           ("T10Y3M", "10Y − 3M", AMBER, "dash")]

REAL = [("DGS10", "10Y nominal", BLUE, None),
        ("DFII10", "10Y real (TIPS)", GREEN, None),
        ("T10YIE", "10Y breakeven", AMBER, "dash")]

# The gilt curve has three points, not four: the BoE interactive database
# publishes short, medium and long and nothing below about five years, so
# there is no 2Y to pair with the Treasury 2Y. Bank Rate is drawn as a floor
# instead, which is the honest way to show a curve that starts mid-way.
GILT = [("GBP5Y", "5Y", CLAY), ("GBP10Y", "10Y", BLUE),
        ("GBP20Y", "20Y", GREEN), ("GBP30YZC", "30Y+", NAVY)]

GILT_REAL = [("GBP10YR", "10Y real", GREEN, None),
             ("GBP20YR", "20Y real", PURPLE, None),
             ("GBP10YIE", "10Y breakeven", AMBER, "dash")]

# VIX from Yahoo rather than FRED's VIXCLS: FRED posts the close a day or two
# late. VIXCLS is still collected and stays in the series browser.
VIX = "YF:^VIX"
VIX3M = "YF:^VIX3M"
MOVE = "YF:^MOVE"
FG = "CNNFG"

EQUITY = [("YF:^GSPC", "S&P 500", NAVY), ("YF:^IXIC", "Nasdaq", BLUE),
          ("YF:^FTSE", "FTSE 100", AMBER), ("YF:^KS11", "KOSPI", CLAY)]
SPX, SOX = "YF:^GSPC", "YF:^SOX"

GOLD, COPPER, BRENT, WTI, HH, TTF, SILVER = (
    "YF:GC=F", "YF:HG=F", "YF:BZ=F", "YF:CL=F", "YF:NG=F", "YF:TTF=F",
    "YF:SI=F")
EURUSD = "YF:EURUSD=X"
GDX, GLD = "YF:GDX", "YF:GLD"
URANIUM = "YF:U-UN.TO"          # Sprott Physical Uranium Trust, spot proxy

# TTF quotes in EUR per MWh and Henry Hub in USD per mmBtu, so the raw
# difference is meaningless. One MWh is 3.412 mmBtu; the euro leg then needs
# converting. Computed here rather than stored, because a derived series has
# to be kept in step with its inputs and this is two divisions.
MWH_TO_MMBTU = 3.412

# The strip at the top of each tab: the readings worth seeing first. Each is
# (source, label, sub, invert, unit). source is a series id - a macro id or a
# YF: fund_id - or (RATIO, a, b) for one Yahoo series over another. invert
# marks the ones where a fall is the risk-on reading, so the colour is not
# simply "up is green": VIX rising is not good news.
RATIO = "ratio"
STRIPS = {
    "Rates": [
        ("DGS2",               "2Y",            "US",       False, "%"),
        ("DGS10",              "10Y",           "US",       False, "%"),
        ("DGS30",              "30Y",           "US long",  False, "%"),
        ("T10Y2Y",             "2s10s",         "US",       False, "%"),
        ("DFII10",             "10Y real",      "TIPS",     False, "%"),
        ("T10YIE",             "10Y breakeven", "US",       False, "%"),
        ("GBP10Y",             "10Y gilt",      "UK",       False, "%"),
        ("BAMLH0A0HYM2",       "HY OAS",        "ICE BofA", True,  "%"),
    ],
    "Volatility": [
        (VIX,                  "VIX",           "CBOE",     True,  ""),
        ((RATIO, VIX, VIX3M),  "VIX / VIX3M",   "Term",     True,  ""),
        (MOVE,                 "MOVE",          "ICE",      True,  ""),
        (FG,                   "Fear & Greed",  "CNN",      False, ""),
        (SPX,                  "S&P 500",       "US",       False, ""),
        ("YF:DX-Y.NYB",        "DXY",           "ICE",      True,  ""),
    ],
    "Commodity": [
        (BRENT,                "Brent",         "ICE",      False, ""),
        (GOLD,                 "Gold",          "COMEX",    False, ""),
        (SILVER,               "Silver",        "COMEX",    False, ""),
        (COPPER,               "Copper",        "COMEX",    False, ""),
        (HH,                   "Henry Hub",     "NYMEX",    False, ""),
        ((RATIO, GOLD, COPPER), "Gold / copper", "Ratio",   True,  ""),
        ((RATIO, GDX, GLD),    "GDX / GLD",     "Miners",   False, ""),
    ],
}

# --- layout pieces ----------------------------------------------------------

CARD = {**theme.CARD, "marginBottom": 0, "minWidth": 0,
        "padding": "16px 18px"}

LABEL = {"fontSize": "10px", "letterSpacing": "0.11em",
         "textTransform": "uppercase", "color": theme.SLATE,
         "fontWeight": 600}

NOTE = {"fontSize": "12px", "color": theme.SLATE, "marginTop": "2px"}

NUM = {"fontFamily": theme.FONT_NUM, "fontVariantNumeric": "tabular-nums"}

GRAPH = {"displayModeBar": False}


def _head(title, note=None):
    return html.Div([
        html.Div(title, style={**theme.CARD_TITLE, "marginBottom": "2px"}),
        html.Div(note, style=NOTE) if note else None,
    ], style={"marginBottom": "10px"})


def _card(title, note, *children):
    return html.Div([_head(title, note), *children], style=CARD)


def _row(*cards, min_width=360):
    """Cards side by side, wrapping to one per row on a narrow screen."""
    return html.Div(list(cards), style={
        "display": "grid",
        "gridTemplateColumns": f"repeat(auto-fit, minmax({min_width}px, 1fr))",
        "gap": "16px", "marginBottom": "16px"})


def _graph(gid):
    return dcc.Graph(id=gid, config=GRAPH)


def _picker():
    """A native date input, not dcc.DatePickerSingle: react-dates' DOM takes
    no inline styles, and a native input looks like the other pages'."""
    return html.Div([
        html.Label("From", style={**LABEL, "marginRight": "8px"}),
        dcc.Input(id="ind-from", type="date", value=DEFAULT_FROM,
                  debounce=True,
                  style={**NUM, "fontSize": "12.5px", "padding": "5px 8px",
                         "border": f"1px solid {theme.LINE}",
                         "borderRadius": "4px", "color": theme.TEXT,
                         "background": theme.SURFACE, "width": "140px"}),
    ], style={"display": "flex", "alignItems": "center"})


def layout():
    return html.Div([
        subtabs("ind-tabs", TABS),
        html.Div([
            html.Div([
                html.Div([
                    html.H1("Indicators", style=theme.H1),
                    html.P("Rates, volatility and commodities: the weather "
                           "around the portfolio.",
                           style={**theme.SUBTITLE, "margin": 0}),
                ]),
                html.Div([
                    _picker(),
                    html.Div(id="ind-asof", style={
                        **NUM, "fontSize": "11px", "color": theme.SLATE,
                        "textAlign": "right", "marginTop": "6px"}),
                ]),
            ], style={"display": "flex", "justifyContent": "space-between",
                      "alignItems": "flex-start", "gap": "20px",
                      "flexWrap": "wrap", "marginBottom": "18px"}),

            html.Div(id="ind-strip", style={
                "display": "grid",
                "gridTemplateColumns": "repeat(auto-fit, minmax(150px, 1fr))",
                "gap": "1px", "background": theme.LINE,
                "border": f"1px solid {theme.LINE}", "borderRadius": "6px",
                "overflow": "hidden", "marginBottom": "18px"}),

            html.Div(id="ind-body"),
        ], style=theme.PAGE),
    ])


def _rates_tab():
    return html.Div([
        _row(_card("Treasury curve",
                   "2Y, 5Y, 10Y and 30Y, NBER recessions shaded.",
                   _graph("ind-curve")),
             _card("Gilt curve",
                   "5Y, 10Y, 20Y and 30Y+, with Bank Rate as the floor.",
                   _graph("ind-gilt"))),
        _row(_card("Recession spreads", "Below zero is inverted.",
                   _graph("ind-spreads")),
             _card("Real vs nominal", "The gap is expected inflation.",
                   _graph("ind-real")),
             _card("Gilt real & breakeven",
                   "Real yields and implied inflation, direct from the "
                   "index-linked curve.", _graph("ind-gilt-real")),
             min_width=320),
        _row(_card("Curve shape", "The curve on chosen dates.",
                   _graph("ind-snapshot")),
             _card("Curve evolution", "One curve per quarter.",
                   _graph("ind-evolution"))),
        _row(_card("All series",
                   "Everything collected, including what is not charted. "
                   "Click a row to plot it.", html.Div(id="ind-series")),
             html.Div([
                 html.Div("Series", id="ind-picked-title",
                          style={**theme.CARD_TITLE, "marginBottom": "10px"}),
                 dcc.Graph(id="ind-picked", config=GRAPH,
                           figure=empty("Click a series on the left to "
                                        "plot it", 300)),
             ], style=CARD)),
    ])


def _volatility_tab():
    return html.Div([
        _row(_card("VIX", "Against its own year's range. 20 is the "
                          "conventional line for nervous.", _graph("ind-vix")),
             _card("VIX term structure",
                   "VIX over VIX3M. Above 1, near-term fear exceeds the "
                   "three-month: acute stress.", _graph("ind-vix-term")),
             _card("MOVE", "Treasury implied volatility: VIX for bonds.",
                   _graph("ind-move")),
             min_width=320),
        _row(_card("Fear & Greed",
                   "Seven signals, one mood. Below 25 is extreme fear.",
                   _graph("ind-fg")),
             _card("Equity", "Rebased to the From date.",
                   _graph("ind-equity"),
                   html.Table([
                       html.Thead(html.Tr([
                           html.Th("Index"),
                           *[html.Th(h, style={"textAlign": "right"})
                             for h in ("1D", "1M", "Period")]])),
                       html.Tbody(id="ind-equity-rows")],
                       className="pz", style={"marginTop": "6px"}))),
    ])


def _commodity_tab():
    return html.Div([
        _row(_card("Commodity signals", "Ratios and spreads, not just prices.",
                   html.Div(id="ind-commodities"),
                   html.Div(_graph("ind-ratios"), style={"marginTop": "8px"})),
             _card("Copper / gold and the 10Y",
                   "Copper over gold tends to track the 10Y yield. When they "
                   "part, one of the two is mispriced.", _graph("ind-cg10"))),
        _row(_card("Miners vs gold",
                   "GDX over GLD, rebased. Rising: the miners confirm gold's "
                   "move.", _graph("ind-miners")),
             _card("Your themes", "Held, against its own driver.",
                   html.Div(id="ind-themes"))),
    ])


BODIES = {"Rates": _rates_tab, "Volatility": _volatility_tab,
          "Commodity": _commodity_tab}


@callback(Output("ind-body", "children"), Input("ind-tabs", "value"))
def _body(tab):
    return BODIES.get(tab, _rates_tab)()


# --- data helpers -----------------------------------------------------------

def _since(since):
    return (since or DEFAULT_FROM)[:10]


def _year_back():
    """Start date for anything that places today in its own year: a little
    over a year, so the band never depends on the From date."""
    return (datetime.today() - timedelta(days=400)).strftime("%Y-%m-%d")


def _series_for(fund_id, since=None):
    """A Yahoo instrument's closes as a date-indexed Series."""
    df = pf.price_series(fund_id, since)
    if df.empty:
        return pd.Series(dtype=float)
    return pd.Series(df["close"].values,
                     index=pd.to_datetime(df["date"].values), dtype=float)


def _align(a, b):
    """Two series on their joint dates, each carried forward over the other's
    gaps - a holiday in one market should not erase the other's day."""
    if a.empty or b.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    idx = a.index.union(b.index)
    a, b = a.reindex(idx).ffill(), b.reindex(idx).ffill()
    both = a.notna() & b.notna()
    return a[both], b[both]


def _ratio(a, b):
    a, b = _align(a, b)
    if a.empty:
        return a
    r = a / b
    return r.replace([float("inf"), -float("inf")], float("nan")).dropna()


def _spread(a, b):
    a, b = _align(a, b)
    return a - b


def _ttf_spread_series(since):
    """TTF less Henry Hub, both in USD per mmBtu."""
    ttf = _series_for(TTF, since)
    hh = _series_for(HH, since)
    eur = _series_for(EURUSD, since)
    # A missing EURUSD leaves the spread unconvertible rather than wrong: 1.0
    # would silently understate it by about fifteen per cent.
    if ttf.empty or hh.empty or eur.empty:
        return pd.Series(dtype=float)
    ttf, eur = _align(ttf, eur)
    return _spread(ttf / MWH_TO_MMBTU * eur, hh)


def _signed(v, dp=1, suffix="%"):
    """+1.2% / -0.4% / +0.0%, never -0.0%. Rounded first, and the rounded
    value is returned too, so its colour matches what is shown."""
    if v is None:
        return "—", None
    v = round(v, dp) + 0.0            # + 0.0 turns -0.0 into 0.0
    return f"{v:+.{dp}f}{suffix}", v


def _pct(series, days):
    """Change over a trailing window, in percent."""
    if series is None or series.empty or len(series) < 2:
        return None
    cutoff = series.index[-1] - pd.Timedelta(days=days)
    prior = series[series.index <= cutoff]
    if prior.empty or not prior.iloc[-1]:
        return None
    return (series.iloc[-1] / prior.iloc[-1] - 1) * 100


def _chg(series, days):
    """Change over a trailing window, in the series' own units. For spreads,
    where a percentage of something that can sit near zero means nothing."""
    if series is None or series.empty or len(series) < 2:
        return None
    cutoff = series.index[-1] - pd.Timedelta(days=days)
    prior = series[series.index <= cutoff]
    if prior.empty:
        return None
    return float(series.iloc[-1] - prior.iloc[-1])


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


# --- plotting ---------------------------------------------------------------

def _style(fig, height=300, ytitle="", zero=False, legend=True):
    """The app's chart look (ui.figures.base_layout) plus what a time series
    page wants on top: unified hover, a legend above the plot, quiet axes."""
    fig.update_layout(**base_layout(
        height=height, hovermode="x unified", showlegend=legend,
        margin=dict(l=48, r=12, t=30 if legend else 8, b=30),
        yaxis_title=ytitle,
        legend=dict(orientation="h", y=1.12, x=0, font=dict(size=10.5),
                    bgcolor="rgba(0,0,0,0)")))
    fig.update_xaxes(showgrid=False, linecolor=theme.LINE, zeroline=False,
                     tickfont=dict(size=10, color=theme.SLATE))
    fig.update_yaxes(showgrid=True, gridcolor=theme.LINE_SOFT,
                     linecolor=theme.LINE, zeroline=zero,
                     zerolinecolor=theme.CHART_ZERO,
                     tickfont=dict(size=10, color=theme.SLATE,
                                   family=theme.FONT_NUM),
                     title_font=dict(size=10.5, color=theme.SLATE))
    return fig


def _lines(fig, df, spec, unit="%"):
    """One trace per (id, label, colour[, dash]) present in df."""
    for item in spec:
        sid, label, colour = item[:3]
        dash_ = item[3] if len(item) > 3 else None
        if sid not in df.columns or df[sid].isna().all():
            continue
        fig.add_trace(go.Scatter(
            x=df.index, y=df[sid], mode="lines", name=label,
            line=dict(color=colour, width=1.6, dash=dash_),
            hovertemplate="%{y:.2f}" + unit + "<extra>" + label
                          + "</extra>"))


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
            fig.add_vrect(x0=began, x1=when, fillcolor=theme.CHART_ZERO,
                          opacity=.35, layer="below", line_width=0)
            inside = False
    if inside and began is not None:
        fig.add_vrect(x0=began, x1=hi, fillcolor=theme.CHART_ZERO,
                      opacity=.35, layer="below", line_width=0)


# --- regime strip -------------------------------------------------------------

CELL = {"background": theme.SURFACE, "padding": "11px 13px 8px"}
CELL_HEAD = {**LABEL, "fontSize": "9.5px", "display": "flex",
             "justifyContent": "space-between", "gap": "6px",
             "marginBottom": "6px"}
CELL_VALUE = {**NUM, "fontSize": "20px", "fontWeight": 500,
              "letterSpacing": "-0.02em", "color": theme.TEXT}
CELL_CHANGE = {**NUM, "fontSize": "11.5px", "fontWeight": 500}
CELL_RANGE = {**NUM, "position": "absolute", "top": "13px", "left": 0,
              "right": 0, "display": "flex",
              "justifyContent": "space-between", "fontSize": "9.5px",
              "color": MUTED}


def _cell_head(label, sub):
    return html.Div([html.Span(label),
                     html.Span(sub, style={"fontWeight": 500,
                                           "color": MUTED})],
                    style=CELL_HEAD)


def _marker(pct):
    return html.Div(style={"position": "absolute", "top": "1px",
                           "left": f"calc({pct * 100:.1f}% - 0.75px)",
                           "width": "1.5px", "height": "10px",
                           "background": theme.TEXT})


def _fg_cell(st, label, sub):
    """The Fear & Greed box: the same frame as its neighbours, another bar.

    The others place today in its own year's range, because a level means
    little without one. This score is already a scale - 0 to 100, cut into
    CNN's five zones - so the bar is that scale in its zone colours, and the
    word under it names the zone today is in.
    """
    value, chg = st["value"], st.get("change")
    word = sentiment.zone(value)

    if chg is None or abs(chg) < 1e-9:
        colour, sign = MUTED, ""
    else:
        colour, sign = (POS, "+") if chg > 0 else (NEG, "")

    segments = [html.Div(style={"flex": f"{hi - lo:g}", "height": "2px",
                                "background": FG_COLOURS[name],
                                "opacity": .6})
                for lo, hi, name in sentiment.ZONES]

    return html.Div([
        _cell_head(label, sub),
        html.Div([
            html.B(f"{value:.1f}", style=CELL_VALUE),
            html.Span("" if chg is None else f"{sign}{chg:.1f}",
                      style={**CELL_CHANGE, "color": colour}),
        ], style={"display": "flex", "alignItems": "baseline",
                  "gap": "8px", "marginBottom": "9px"}),
        html.Div([
            html.Div(segments, style={"position": "absolute", "top": "5px",
                                      "left": 0, "right": 0,
                                      "display": "flex"}),
            _marker(value / 100),
            html.Div([
                html.Span("0"),
                html.Span(word, style={"color": FG_COLOURS[word],
                                       "textTransform": "uppercase",
                                       "letterSpacing": "0.08em",
                                       "fontWeight": 600}),
                html.Span("100"),
            ], style=CELL_RANGE),
        ], style={"position": "relative", "height": "24px"}),
    ], style=CELL)


def _dec(value):
    """Decimals that show movement at this size: 0.912, 16.74, 684.3, 4,261."""
    a = abs(value)
    return 3 if a < 1 else 2 if a < 100 else 1 if a < 1000 else 0


def _ratio_stats(num, den, days=365):
    """range_stats for one Yahoo series over another, worked out here: a
    ratio is one division, not something worth storing and keeping in step
    with its two inputs."""
    r = _ratio(_series_for(num, _year_back()), _series_for(den, _year_back()))
    if r.empty:
        return {}
    year = r[r.index >= r.index[-1] - pd.Timedelta(days=days)]
    return {"lo": float(year.min()), "hi": float(year.max()),
            "value": float(r.iloc[-1]),
            "date": r.index[-1].strftime("%Y-%m-%d"),
            "change": float(r.iloc[-1] - r.iloc[-2]) if len(r) > 1 else None}


@callback(Output("ind-strip", "children"), Output("ind-asof", "children"),
          Input("ind-tabs", "value"))
def _strip(tab):
    """The open tab's strip. The as-of stamp is the latest date among its
    readings, so the Rates tab shows FRED's lag honestly rather than
    borrowing a Yahoo date from another tab."""
    cells = []
    latest_date = None

    for source, label, sub, invert, unit in STRIPS.get(tab, STRIPS["Rates"]):
        if isinstance(source, tuple):
            st = _ratio_stats(source[1], source[2])
        else:
            st = repo.range_stats(source, 365)
        if not st:
            cells.append(html.Div([
                _cell_head(label, sub),
                html.Div("no data", style={**NUM, "fontSize": "13px",
                                           "color": MUTED,
                                           "marginTop": "8px"}),
            ], style=CELL))
            continue

        latest_date = max(latest_date or st["date"], st["date"])
        if source == FG:
            cells.append(_fg_cell(st, label, sub))
            continue

        rng = (st["hi"] - st["lo"]) or 1
        pct = max(0.0, min(1.0, (st["value"] - st["lo"]) / rng))
        chg = st.get("change")

        # Colour follows meaning, not sign: a falling VIX and a falling
        # spread are not the same news, which is what invert encodes. A
        # change too small to show at the value's own decimals is no change,
        # rather than a red -0.00.
        dec = 2 if unit == "%" else _dec(st["value"])
        if chg is None or abs(chg) < 0.5 * 10 ** -dec:
            chg = None if chg is None else 0.0
            colour, arrow = MUTED, ""
        else:
            good = (chg < 0) if invert else (chg > 0)
            colour = POS if good else NEG
            arrow = "+" if chg > 0 else ""

        cells.append(html.Div([
            _cell_head(label, sub),
            html.Div([
                html.B(f"{st['value']:,.{dec}f}{unit}", style=CELL_VALUE),
                html.Span("" if chg is None else f"{arrow}{chg:,.{dec}f}",
                          style={**CELL_CHANGE, "color": colour}),
            ], style={"display": "flex", "alignItems": "baseline",
                      "gap": "8px", "marginBottom": "9px"}),

            # Where today sits in its own year. A level means little without
            # it - VIX at 17 is a different statement in a year that ranged
            # 12-28 than one that ranged 15-18.
            html.Div([
                html.Div(style={"position": "absolute", "top": "5px",
                                "left": 0, "right": 0, "height": "2px",
                                "background": theme.LINE}),
                html.Div(style={"position": "absolute", "top": "5px",
                                "left": 0, "height": "2px",
                                "right": f"{(1 - pct) * 100:.1f}%",
                                "background": theme.CHART_ZERO}),
                _marker(pct),
                html.Div([html.Span(f"{st['lo']:,.{dec}f}"),
                          html.Span(f"{st['hi']:,.{dec}f}")],
                         style=CELL_RANGE),
            ], style={"position": "relative", "height": "24px"}),
        ], style=CELL))

    stamp = (f"Latest observation {latest_date}" if latest_date
             else "No data yet — run the importers")
    return cells, stamp


# --- Rates tab ----------------------------------------------------------------

@callback(Output("ind-curve", "figure"), Output("ind-spreads", "figure"),
          Output("ind-real", "figure"), Input("ind-from", "value"))
def _timeseries(since):
    since = _since(since)

    df = repo.frame([c[0] for c in CURVE], since)
    if df.empty:
        b = empty("No macro data — run the FRED importer", 330)
        return b, b, b

    curve = go.Figure()
    _lines(curve, df, CURVE)
    _shade(curve, df.index.min(), df.index.max())
    _style(curve, 330, "Yield (%)")

    sp = repo.frame([s[0] for s in SPREADS], since)
    spreads = go.Figure()
    if not sp.empty:
        _lines(spreads, sp, SPREADS)
        spreads.add_hline(y=0, line_dash="dash", line_color=REF)
        _shade(spreads, sp.index.min(), sp.index.max())
    _style(spreads, 262, "Spread (%)", zero=True)

    rl = repo.frame([r[0] for r in REAL], since)
    real = go.Figure()
    if not rl.empty:
        _lines(real, rl, REAL)
        _shade(real, rl.index.min(), rl.index.max())
    _style(real, 262, "Yield (%)", zero=True)

    return curve, spreads, real


@callback(Output("ind-gilt", "figure"), Input("ind-from", "value"))
def _gilt(since):
    """The gilt curve, with Bank Rate drawn as a floor.

    Three tenors rather than four, and starting at five years: the BoE
    interactive database has no point below that. Bank Rate is added as a
    dashed line so the shape reads against something at the front rather
    than floating.
    """
    since = _since(since)
    df = repo.frame([g[0] for g in GILT], since)
    if df.empty:
        return empty("No gilt data — run the BoE importer", 330)

    fig = go.Figure()
    _lines(fig, df, GILT)

    rate = repo.frame(["BANKRATE"], since)
    if not rate.empty and "BANKRATE" in rate.columns:
        fig.add_trace(go.Scatter(
            x=rate.index, y=rate["BANKRATE"], mode="lines", name="Bank Rate",
            line=dict(color=MUTED, width=1.2, dash="dot"),
            hovertemplate="%{y:.2f}%<extra>Bank Rate</extra>"))

    return _style(fig, 330, "Yield (%)")


@callback(Output("ind-gilt-real", "figure"), Input("ind-from", "value"))
def _gilt_real(since):
    """Real gilt yields and implied inflation.

    The 20Y real yield is the LDI gauge: it is what moved in 2022, and a
    better read on that stress than any single bond's price.
    """
    since = _since(since)
    df = repo.frame([g[0] for g in GILT_REAL], since)
    if df.empty:
        return empty("No gilt real data", 262)
    fig = go.Figure()
    _lines(fig, df, GILT_REAL)
    return _style(fig, 262, "Yield (%)", zero=True)


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
    colours = [theme.CHART_ZERO, MUTED, theme.SLATE, BLUE]
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
        return empty("No curve data", 262)
    return _style(fig, 262, "Yield (%)")


@callback(Output("ind-evolution", "figure"), Input("ind-from", "value"))
def _evolution(_since):
    today = pd.Timestamp.today()
    dates = []
    for i in range(8, -1, -1):
        d = today - pd.DateOffset(months=i * 3)
        eom = (pd.Timestamp(year=d.year, month=d.month, day=1)
               + pd.DateOffset(months=1, days=-1))
        dates.append(eom.strftime("%Y-%m-%d"))

    # Pale to dark: recency reads without the legend.
    colours = [theme.CHART_ZERO, theme.CHART_ZERO, theme.CHART_ZERO,
               MUTED, MUTED, theme.SLATE, theme.SLATE, BLUE, NAVY]
    widths = [1, 1, 1, 1.2, 1.2, 1.5, 1.7, 2.1, 2.6]

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
        return empty("No curve data", 262)
    fig = _style(fig, 262, "Yield (%)")
    fig.update_layout(legend=dict(font=dict(size=9.5)))
    return fig


@callback(Output("ind-series", "children"), Input("ind-from", "value"))
def _series_table(_since):
    df = repo.series(active_only=True)
    if df.empty:
        return html.Div("No series yet.", style=NOTE)

    rows, last_cat = [], None
    for r in df.to_dict("records"):
        if r["category"] != last_cat:
            last_cat = r["category"]
            rows.append(html.Tr([html.Td(last_cat, colSpan=5, style={
                **LABEL, "padding": "12px 10px 4px", "color": theme.NEEDLE,
                "borderBottom": "none"})]))

        charted = bool(r.get("dashboard"))
        rows.append(html.Tr([
            html.Td(r["id"], style={**NUM, "fontSize": "12px"}),
            html.Td(r["name"], style={
                "fontSize": "12px", "color": theme.SLATE,
                "maxWidth": "220px", "overflow": "hidden",
                "textOverflow": "ellipsis", "whiteSpace": "nowrap"}),
            html.Td(f"{int(r['observations'] or 0):,}", className="num",
                    style={"color": theme.SLATE}),
            html.Td(r["latest"] or "—", className="num",
                    style={"color": theme.SLATE}),
            html.Td("●" if charted else "", style={
                "fontSize": "9px", "textAlign": "right",
                "color": POS if charted else theme.LINE}),
        ], id={"type": "ind-row", "id": r["id"]},
            style={"cursor": "pointer"}))

    return html.Div(
        html.Table([
            html.Thead(html.Tr([
                html.Th("Series"), html.Th("Name"),
                html.Th("Rows", style={"textAlign": "right"}),
                html.Th("Latest", style={"textAlign": "right"}),
                html.Th("")])),
            html.Tbody(rows)], className="pz"),
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
        return empty(f"No observations for {sid}", 300), sid

    meta = repo.series(active_only=False)
    row = meta[meta["id"] == sid]
    name = row["name"].iloc[0] if not row.empty else sid
    units = row["units"].iloc[0] if not row.empty else ""

    fig = go.Figure(go.Scatter(
        x=df.index, y=df["value"], mode="lines",
        line=dict(color=BLUE, width=1.5),
        hovertemplate="%{x|%d %b %Y}: %{y:,.2f}<extra></extra>"))
    _shade(fig, df.index.min(), df.index.max())
    _style(fig, 300, units or "", legend=False)
    return fig, f"{name} · {sid}"


# --- Volatility tab -------------------------------------------------------------

def _level_in_year(fund_id, since, label, height=262, ref=None):
    """A volatility index over the window, with its own year's range banded
    behind it - the same idea as the strip: a level reads differently
    depending on where it sits in its own recent history."""
    s = _series_for(fund_id, since)
    if s.empty:
        return empty(f"No {label} data — add {fund_id[3:]} as a Yahoo "
                     f"instrument", height)

    st = repo.range_stats(fund_id, 365)
    fig = go.Figure()
    if st:
        fig.add_hrect(y0=st["lo"], y1=st["hi"], fillcolor=theme.LINE,
                      opacity=.7, layer="below", line_width=0)
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines", name=label,
        line=dict(color=NAVY, width=1.5),
        hovertemplate="%{y:.2f}<extra>" + label + "</extra>"))
    if ref is not None:
        fig.add_hline(y=ref, line_dash="dot", line_color=REF)
    return _style(fig, height, "", legend=False)


@callback(Output("ind-vix", "figure"), Input("ind-from", "value"))
def _vix(since):
    # Twenty is the line above which the market is generally said to be
    # nervous. Convention rather than a rule, so it is drawn faintly.
    return _level_in_year(VIX, _since(since), "VIX", ref=20)


@callback(Output("ind-move", "figure"), Input("ind-from", "value"))
def _move(since):
    return _level_in_year(MOVE, _since(since), "MOVE")


@callback(Output("ind-vix-term", "figure"), Input("ind-from", "value"))
def _vix_term(since):
    """VIX over VIX3M.

    The term structure normally slopes up - the next month is calmer than the
    next three - so the ratio sits below 1. It crosses 1 when fear is about
    now rather than later, which is when it is worth looking at. The region
    above 1 is tinted so a crossing reads without the axis.
    """
    since = _since(since)
    ratio = _ratio(_series_for(VIX, since), _series_for(VIX3M, since))
    if ratio.empty:
        return empty("No VIX3M data — add ^VIX3M as a Yahoo instrument", 262)

    top = max(1.05, float(ratio.max()) * 1.03)
    fig = go.Figure()
    fig.add_hrect(y0=1, y1=top, fillcolor=NEG, opacity=.06, layer="below",
                  line_width=0)
    fig.add_hline(y=1, line_dash="dot", line_color=REF)
    fig.add_trace(go.Scatter(
        x=ratio.index, y=ratio.values, mode="lines", name="VIX / VIX3M",
        line=dict(color=NAVY, width=1.5),
        hovertemplate="%{y:.2f}<extra>VIX / VIX3M</extra>"))
    fig = _style(fig, 262, "", legend=False)
    fig.update_yaxes(range=[min(0.7, float(ratio.min()) * 0.97), top])
    return fig


@callback(Output("ind-fg", "figure"), Input("ind-from", "value"))
def _fear_greed(since):
    """CNN's headline score over the window, on its own five zones.

    The zones are shaded behind the line and named on the y-axis in place of
    numbers, and the line takes the colour of the zone it is in - so where the
    mood sits reads at a glance, and the number is on the strip and in the
    hover. Only the headline is charted: CNN publishes the component scores
    for today alone, so their history is only what this machine has kept.
    """
    since = _since(since)
    df = repo.observations(FG, since)
    if df.empty:
        return empty("No Fear & Greed data — run the CNN importer", 300)
    s = df["value"]

    fig = go.Figure()
    for lo, hi, name in sentiment.ZONES:
        fig.add_hrect(y0=lo, y1=hi, fillcolor=FG_COLOURS[name], opacity=.07,
                      layer="below", line_width=0)

    for name, (xs, ys) in sentiment.split_by_zone(s).items():
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines", hoverinfo="skip",
            line=dict(color=FG_COLOURS[name], width=1.8)))

    # One invisible trace carries the hover, so each day shows once with its
    # zone rather than once per coloured piece it happens to touch.
    fig.add_trace(go.Scatter(
        x=s.index, y=s.values, mode="lines",
        line=dict(width=0, color="rgba(0,0,0,0)"),
        customdata=[sentiment.zone(v) for v in s.values],
        hovertemplate="%{y:.1f} · %{customdata}<extra></extra>"))

    last = float(s.iloc[-1])
    fig.add_trace(go.Scatter(
        x=[s.index[-1]], y=[last], mode="markers", hoverinfo="skip",
        marker=dict(size=8, color=FG_COLOURS[sentiment.zone(last)],
                    line=dict(color=theme.SURFACE, width=2))))

    _style(fig, 300, "", legend=False)
    fig.update_yaxes(
        range=[0, 100], showgrid=False,
        tickvals=[(lo + hi) / 2 for lo, hi, _ in sentiment.ZONES],
        ticktext=[f"<span style='color:{FG_COLOURS[n]}'>{n}</span>"
                  for _, _, n in sentiment.ZONES],
        tickfont=dict(size=10, family=theme.FONT_UI))
    # The end marker would otherwise pad the time axis a month past the data.
    fig.update_xaxes(range=[s.index[0], s.index[-1] + pd.Timedelta(days=4)])
    return fig


@callback(Output("ind-equity", "figure"), Output("ind-equity-rows", "children"),
          Input("ind-from", "value"))
def _equity(since):
    since = _since(since)
    fig = go.Figure()
    rows = []

    for fund_id, label, colour in EQUITY:
        s = _series_for(fund_id, since)
        if s.empty or not s.iloc[0]:
            continue
        # Rounded here rather than in the hovertemplate: plotly formats what
        # it is given, and a template that does not match the trace silently
        # falls back to full float precision.
        rebased = ((s / s.iloc[0] - 1) * 100).round(1)
        fig.add_trace(go.Scatter(
            x=rebased.index, y=rebased.values, mode="lines", name=label,
            line=dict(color=colour, width=1.5),
            hovertemplate="%{y:+.1f}%<extra>" + label + "</extra>"))

        cells = [html.Td([
            html.Span(style={"display": "inline-block", "width": "10px",
                             "height": "2px", "background": colour,
                             "verticalAlign": "middle",
                             "marginRight": "8px"}),
            label])]
        for days in (1, 30, None):
            v = (_pct(s, days) if days
                 else ((s.iloc[-1] / s.iloc[0] - 1) * 100))
            text, v = _signed(v)
            cells.append(html.Td(text, className="num",
                                 style={"color": theme.colour_for(v)}))
        rows.append(html.Tr(cells))

    if not fig.data:
        return empty("No index prices", 220), []

    fig.add_hline(y=0, line_dash="dot", line_color=REF)
    _style(fig, 220, "", legend=False)
    fig.update_yaxes(ticksuffix="%")
    return fig, rows


# --- Commodity tab --------------------------------------------------------------

SIG_STYLE = {
    "support": {"background": f"{POS}14", "color": POS,
                "border": f"1px solid {POS}40"},
    "neutral": {"background": theme.LINE_SOFT, "color": theme.SLATE,
                "border": f"1px solid {theme.LINE}"},
    "against": {"background": f"{NEG}12", "color": NEG,
                "border": f"1px solid {NEG}40"},
}


def _chip(state, text=None):
    st = SIG_STYLE.get(state or "neutral", SIG_STYLE["neutral"])
    label = text or (state or "—")
    return html.Span(label.upper(), style={
        **st, "fontSize": "9.5px", "letterSpacing": "0.09em",
        "fontWeight": 600, "padding": "2px 8px", "borderRadius": "3px",
        "whiteSpace": "nowrap", "minWidth": "72px", "textAlign": "center",
        "display": "inline-block"})


@callback(Output("ind-commodities", "children"),
          Output("ind-ratios", "figure"),
          Input("ind-from", "value"))
def _commodities(since):
    since = _since(since)
    # Read from a year back whatever the From date, so "In its year" is
    # always a year; the chart below is cut to the From date.
    start = min(since, _year_back())

    gold = _series_for(GOLD, start)
    copper = _series_for(COPPER, start)
    brent = _series_for(BRENT, start)
    wti = _series_for(WTI, start)
    silver = _series_for(SILVER, start)
    hh = _series_for(HH, start)

    gold_copper = _ratio(gold, copper)
    gold_silver = _ratio(gold, silver)
    brent_wti = _spread(brent, wti)
    ttf_spread = _ttf_spread_series(start)

    # (label, series, format, invert, change) - change is "pct" for prices
    # and ratios, "abs" for spreads, where a percentage of a number that can
    # sit near zero says nothing.
    signals = [
        ("Brent",           brent,       "{:,.2f}",  False, "pct"),
        ("Brent − WTI",     brent_wti,   "${:,.2f}", True,  "abs"),
        ("Gold",            gold,        "{:,.0f}",  False, "pct"),
        ("Henry Hub",       hh,          "{:,.2f}",  False, "pct"),
        ("TTF − Henry Hub", ttf_spread,  "${:,.2f}", False, "abs"),
        ("Gold / copper",   gold_copper, "{:,.0f}",  True,  "pct"),
        ("Gold / silver",   gold_silver, "{:,.1f}",  True,  "pct"),
    ]

    rows = []
    for label, s, fmt, invert, mode in signals:
        if s is None or s.empty:
            rows.append(html.Tr([
                html.Td(label),
                html.Td("no data", colSpan=3, className="num",
                        style={"color": MUTED}),
            ]))
            continue

        if mode == "abs":
            m1_text, m1 = _signed(_chg(s, 30), 2, "")
        else:
            m1_text, m1 = _signed(_pct(s, 30))
        state, _pos = _band(s, invert)
        rows.append(html.Tr([
            html.Td(label),
            html.Td(fmt.format(float(s.iloc[-1])), className="num"),
            html.Td(m1_text, className="num",
                    style={"color": theme.colour_for(m1)}),
            html.Td(_chip(state), style={"textAlign": "right"}),
        ]))

    table = html.Table([
        html.Thead(html.Tr([
            html.Th("Signal"),
            html.Th("Level", style={"textAlign": "right"}),
            html.Th("1M", style={"textAlign": "right"}),
            html.Th("In its year", style={"textAlign": "right"})])),
        html.Tbody(rows)], className="pz")

    # Two ratios on one chart would need two axes to be readable, so each is
    # rebased to its own start. The shape is the point, not the level.
    fig = go.Figure()
    for s, label, colour in ((gold_copper, "Gold / copper", AMBER),
                             (ttf_spread, "TTF − Henry Hub", CLAY)):
        s = s[s.index >= since] if not s.empty else s
        if s.empty or not s.iloc[0]:
            continue
        fig.add_trace(go.Scatter(
            x=s.index, y=((s / s.iloc[0] - 1) * 100).round(1), mode="lines",
            name=label, line=dict(color=colour, width=1.5),
            customdata=s.round(2).values,
            hovertemplate="%{customdata:,.2f} (%{y:+.1f}%)<extra>"
                          + label + "</extra>"))
    if not fig.data:
        fig = empty("No ratio data", 170)
    else:
        _style(fig, 170, "", legend=True)
        fig.update_yaxes(ticksuffix="%")
    return table, fig


@callback(Output("ind-cg10", "figure"), Input("ind-from", "value"))
def _copper_gold_10y(since):
    """Copper over gold above, the 10Y yield below, on a shared time axis.

    Stacked rather than overlaid: they have nothing in common as units, and
    two y-axes on one plot let the scaling decide whether they look related.
    Copper is USD per lb and gold USD per oz, so the ratio is about 0.0015;
    it is shown per thousand to keep the axis readable.
    """
    since = _since(since)
    cg = _ratio(_series_for(COPPER, since), _series_for(GOLD, since)) * 1000
    ten = repo.frame(["DGS10"], since)
    if cg.empty and ten.empty:
        return empty("No copper, gold or 10Y data", 440)

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True,
                        vertical_spacing=0.08)
    if not cg.empty:
        fig.add_trace(go.Scatter(
            x=cg.index, y=cg.values, mode="lines", name="Copper / gold ×1000",
            line=dict(color=CLAY, width=1.5),
            hovertemplate="%{y:.3f}<extra>Copper / gold ×1000</extra>"),
            row=1, col=1)
    if not ten.empty and "DGS10" in ten.columns:
        fig.add_trace(go.Scatter(
            x=ten.index, y=ten["DGS10"], mode="lines", name="10Y yield",
            line=dict(color=BLUE, width=1.5),
            hovertemplate="%{y:.2f}%<extra>10Y yield</extra>"),
            row=2, col=1)
    _style(fig, 440, "", legend=True)
    fig.update_yaxes(title_text="Cu / Au ×1000", row=1, col=1)
    fig.update_yaxes(title_text="10Y (%)", row=2, col=1)
    return fig


@callback(Output("ind-miners", "figure"), Input("ind-from", "value"))
def _miners(since):
    """GDX over GLD, rebased to the From date.

    Miners are operationally geared to the gold price, so in a healthy gold
    move they should outrun it. A gold rally the miners do not follow is
    worth noticing - it has tended to be the less durable kind.
    """
    since = _since(since)
    r = _ratio(_series_for(GDX, since), _series_for(GLD, since))
    if r.empty or not r.iloc[0]:
        return empty("No GDX or GLD data — add both as Yahoo instruments",
                     262)
    rebased = ((r / r.iloc[0] - 1) * 100).round(1)
    fig = go.Figure(go.Scatter(
        x=rebased.index, y=rebased.values, mode="lines", name="GDX / GLD",
        line=dict(color=AMBER, width=1.6), customdata=r.round(3).values,
        hovertemplate="%{customdata:.3f} (%{y:+.1f}%)<extra>GDX / GLD"
                      "</extra>"))
    fig.add_hline(y=0, line_dash="dot", line_color=REF)
    fig = _style(fig, 262, "", legend=False)
    fig.update_yaxes(ticksuffix="%")
    return fig


# Categories are per-instrument and single-valued; a theme is neither. BHP is
# copper and iron ore, and Industrial Metals sits under the energy transition
# rather than with the miners. So the mapping is written out rather than
# derived, and is meant to be argued with.
#
# (name, categories, driver, high_is_bad, driver label). A driver is
# ("macro", series id), ("price", fund_id) or ("ratio", key) for the ratios
# built in _driver_series.
THEMES = [
    ("Gold & miners", ["Physical Gold", "Gold Miners", "ETC Gold", "NEM"],
     ("macro", "DFII10"), True,  "Real yields"),
    ("Copper",        ["Copper", "BHP"],
     ("ratio", "goldcopper"), True, "Gold / copper"),
    ("Semis / AI",    ["Semi", "Microsoft"],
     ("ratio", "sox"), False, "SOX / S&P 500"),
    ("Gas / LNG",     ["Energy", "Industrial Metals"],
     ("ratio", "ttf"), False, "TTF − Henry Hub"),
    ("Uranium",       ["Uranium"],
     ("price", URANIUM), False, "Spot proxy (Sprott)"),
    ("Financials",    ["Financials", "HSBC", "S&P Global"],
     ("macro", "T10Y2Y"), False, "2s10s"),
]


def _driver_series(kind, key, start):
    if kind == "macro":
        obs = repo.observations(key, start)
        return obs["value"] if not obs.empty else pd.Series(dtype=float)
    if kind == "price":
        return _series_for(key, start)
    if key == "goldcopper":
        return _ratio(_series_for(GOLD, start), _series_for(COPPER, start))
    if key == "sox":
        return _ratio(_series_for(SOX, start), _series_for(SPX, start))
    if key == "ttf":
        return _ttf_spread_series(start)
    return pd.Series(dtype=float)


@callback(Output("ind-themes", "children"), Input("ind-from", "value"))
def _themes(_since):
    """Each theme's weight, and where its driver sits in its own year.

    Drivers are read from a year back regardless of the From date: "in its
    year" should not change because the charts were scrolled.
    """
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

    start = _year_back()
    out = []
    for name, cats, driver, high_bad, label in THEMES:
        weight = sum(by_cat.get(c, 0.0) for c in cats)
        pct = (weight / total * 100) if total else 0.0

        state = None
        if driver:
            state, _ = _band(_driver_series(*driver, start), bool(high_bad))

        out.append(html.Div([
            html.Div([
                html.Div(name, style={"fontSize": "13px",
                                      "color": theme.TEXT}),
                html.Div(label, style={"fontSize": "11px",
                                       "color": theme.SLATE,
                                       "marginTop": "1px"}),
            ]),
            html.Div([
                html.Span(f"{pct:.1f}%", style={
                    **NUM, "fontSize": "12.5px", "color": theme.TEXT,
                    "marginRight": "10px"}),
                _chip(state, None if state else "no data"),
            ], style={"display": "flex", "alignItems": "center",
                      "justifyContent": "flex-end"}),
        ], style={"display": "grid", "gridTemplateColumns": "1fr auto",
                  "gap": "10px", "alignItems": "center", "padding": "9px 0",
                  "borderBottom": f"1px solid {theme.LINE_SOFT}"}))

    if total:
        covered = sum(sum(by_cat.get(c, 0.0) for c in t[1]) for t in THEMES)
        out.append(html.Div(
            f"{covered / total * 100:.0f}% of the portfolio is in a theme. "
            f"The rest — property, cash, HSBC stock — is not.",
            style={**NOTE, "marginTop": "10px"}))
    return out
