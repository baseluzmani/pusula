"""
Portfolio - Charts tab.

Four views:
  1. Sunburst breakdown - asset type on the inner ring, category on the outer -
     with the numbers beside it.
  2. Net worth history from the nightly networth_history table.
  3. Portfolio value by category over time, stacked, with small categories
     rolled into Other so the legend stays readable.
  4. The same composition as a share of the total, with property excluded.

Negative positions are netted into their category rather than dropped. The
mortgage is held at -1 unit against the house, and the old code filtered
`value > 0` before charting, so the house showed gross while the debt against
it vanished - the one position that most changes what the chart means. A
sunburst cannot draw a negative wedge, so the arithmetic happens first:
Property arrives as a single net slice, the way cash already did.
"""

from __future__ import annotations

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd
import plotly.graph_objects as go

from core import theme, finance as fin, valuation as val
from core.repo import portfolio as repo


def _threshold():
    return val.chart_category_threshold()


# Sunburst colours: each asset type takes a palette colour on the inner ring,
# and its categories are progressively lighter tints of that same colour on the
# outer ring - so a whole asset type reads as one colour family at a glance.
ROOT_COLOUR = "#1F2A3A"

SUNBURST_PALETTE = ["#2E6FB5", "#1A7A4C", "#C0392B", "#D06018", "#7D5BA6",
                    "#137E9E", "#B8860F", "#4A6FA5", "#2C3E50", "#27AE60",
                    "#2980B9", "#7F8C8D", "#1ABC9C", "#E74C3C", "#95A5A6",
                    "#8E44AD"]

# Excluded from the composition chart. Property is over a third of the total
# and moves with the house rather than with anything else, so as a share it
# swamps every other band. It stays in the value chart, where the legend can
# be clicked to hide it.
COMPOSITION_EXCLUDE = ("Property",)

# Stacked-area charts keep their own palette.
PALETTE = ["#2E6FB5", "#1A7A4C", "#C0392B", "#D06018", "#7D5BA6", "#137E9E",
           "#B8860F", "#4A6FA5", "#2C3E50", "#27AE60", "#2980B9", "#7F8C8D",
           "#1ABC9C", "#E74C3C", "#95A5A6", "#8E44AD"]

DEFAULT_FROM = "2025-12-01"


def _tint(hex_colour: str, factor: float) -> str:
    """Blend a colour toward white. factor 0 leaves it alone, 1 is white."""
    h = hex_colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    r = int(r + (255 - r) * factor)
    g = int(g + (255 - g) * factor)
    b = int(b + (255 - b) * factor)
    return f"#{r:02x}{g:02x}{b:02x}"


def _child_tints(base: str, count: int) -> list:
    """Lighter shades of base, one per child, spread so they stay distinct."""
    if count <= 1:
        return [_tint(base, 0.35)]
    return [_tint(base, 0.25 + 0.45 * i / (count - 1)) for i in range(count)]


def _date_picker(picker_id):
    return html.Div([
        html.Label("From", style={
            "fontSize": "10px", "fontWeight": 700,
            "letterSpacing": "0.06em", "textTransform": "uppercase",
            "color": theme.SLATE, "marginRight": "8px"}),
        dcc.DatePickerSingle(id=picker_id, date=DEFAULT_FROM,
                             display_format="DD MMM YYYY"),
    ], style={"display": "flex", "alignItems": "center"})


def _card_head(title, picker_id=None):
    left = html.Div(title, style=theme.CARD_TITLE)
    if picker_id is None:
        return left
    return html.Div([left, _date_picker(picker_id)],
                    style={"display": "flex",
                           "justifyContent": "space-between",
                           "alignItems": "center"})


def render():
    return html.Div([
        html.Div([
            html.Div("Portfolio breakdown", style=theme.CARD_TITLE),
            html.Div("Inner ring: asset type. Outer ring: category within it. "
                     "Liabilities are netted against the assets they sit "
                     "against.",
                     style={"fontSize": "11px", "color": theme.NEUTRAL,
                            "marginBottom": "8px"}),
            html.Div([
                html.Div(dcc.Graph(id="pch-sunburst",
                         config={"displayModeBar": False},
                         style={"height": "480px"}), style={"flex": "1",
                         "minWidth": 0}),
                html.Div(id="pch-breakdown", style={
                    "flexShrink": "0", "width": "340px", "marginLeft": "14px",
                    "overflowY": "auto", "maxHeight": "480px"}),
            ], style={"display": "flex", "alignItems": "flex-start"}),
        ], style={**theme.CARD, "marginBottom": "12px"}),

        html.Div([
            _card_head("Net worth history", "pch-networth-from"),
            dcc.Graph(id="pch-networth", config={"displayModeBar": False}),
        ], style={**theme.CARD, "marginBottom": "12px"}),

        html.Div([
            _card_head("Portfolio value by category", "pch-categories-from"),
            html.Div("Categories below "
                     f"{_threshold():.0%} of the latest total are grouped "
                     "as Other. Click a legend entry to hide it.",
                     style={"fontSize": "11px", "color": theme.NEUTRAL,
                            "marginBottom": "6px"}),
            dcc.Graph(id="pch-categories", config={"displayModeBar": False}),
        ], style={**theme.CARD, "marginBottom": "12px"}),

        html.Div([
            _card_head("Composition", "pch-composition-from"),
            html.Div("Each category as a share of the total, property "
                     "excluded. Reads drift in the mix rather than growth - "
                     "a band can widen while its value falls.",
                     style={"fontSize": "11px", "color": theme.NEUTRAL,
                            "marginBottom": "6px"}),
            dcc.Graph(id="pch-composition", config={"displayModeBar": False}),
        ], style=theme.CARD),
    ])


# --- breakdown ------------------------------------------------------------

def _valued_frame():
    """Every priced holding, negatives included.

    Only unpriceable rows are dropped. Negatives are kept and netted later:
    filtering them here is what hid the mortgage.
    """
    instruments = repo.instruments()
    prices = repo.latest_prices()
    rates = fin.fx_rates(prices)
    price_map = fin.latest_price_map(prices)
    valued = val.value_holdings(repo.holdings(), instruments, price_map,
                                rates["USD"], rates)
    if not valued.empty:
        valued = valued[valued["value"].notna() & (valued["value"] != 0)]

    cash_total = val.cash_total_gbp(repo.cash_accounts(), rates)
    if cash_total:
        extra = pd.DataFrame([{"fund_id": "CASH:TOTAL", "name": "Cash",
                               "asset_type": "Cash", "category": "Cash",
                               "units": 1, "price_gbp": cash_total,
                               "value": cash_total}])
        valued = pd.concat([valued, extra], ignore_index=True)
    return valued


def _netted(valued):
    """
    Category totals with liabilities netted off, ready to chart.

    Returns (grouped, offset, dropped) where grouped has one row per
    (asset_type, category) with a positive net, offset lists the categories
    that contained a negative so the table can say so, and dropped holds any
    category netting to zero or less.

    A category that nets non-positive is dropped: a sunburst has no way to
    draw it, and including it would distort every percentage on the chart.
    That does not arise today - the mortgage is smaller than the house - but
    it would if the debt ever exceeded the asset.
    """
    grouped = (valued.groupby(["asset_type", "category"], as_index=False)
               ["value"].sum())

    negatives = valued[valued["value"] < 0]
    offset = {}
    for r in negatives.itertuples():
        offset.setdefault((r.asset_type, r.category), []).append(
            (r.name, r.value))

    dropped = grouped[grouped["value"] <= 0]
    grouped = grouped[grouped["value"] > 0]
    return grouped, offset, dropped


@callback(Output("pch-sunburst", "figure"), Output("pch-breakdown", "children"),
          Input("pf-tabs", "value"))
def _sunburst(_tab):
    valued = _valued_frame()
    if valued.empty:
        return _blank("No holdings to chart"), html.Div()

    grouped, offset, dropped = _netted(valued)
    total = val.total(grouped["value"])
    if not total:
        return _blank("Nothing to chart"), html.Div()

    # A "Portfolio" root makes percentParent meaningful on the inner ring:
    # asset types read as a share of the whole, categories as a share of
    # their asset type.
    labels = ["Portfolio"]
    parents = [""]
    values = [total]
    colours = [ROOT_COLOUR]

    types = (grouped.groupby("asset_type")["value"].sum()
             .sort_values(ascending=False))
    asset_type_names = set(types.index)

    for i, (atype, type_total) in enumerate(types.items()):
        base = SUNBURST_PALETTE[i % len(SUNBURST_PALETTE)]
        labels.append(atype)
        parents.append("Portfolio")
        values.append(type_total)
        colours.append(base)

        subset = (grouped[grouped["asset_type"] == atype]
                  .sort_values("value", ascending=False))
        tints = _child_tints(base, len(subset))
        for tint, r in zip(tints, subset.itertuples()):
            cat = r.category
            # Plotly keys nodes by label, so a category sharing a name with an
            # asset type (or an earlier category) needs disambiguating.
            label = (f"{cat} ({atype})"
                     if cat in asset_type_names or cat in labels else cat)
            labels.append(label)
            parents.append(atype)
            values.append(r.value)
            colours.append(tint)

    fig = go.Figure(go.Sunburst(
        labels=labels, parents=parents, values=values,
        marker=dict(colors=colours), branchvalues="total", maxdepth=3,
        textinfo="label+percent parent",
        insidetextfont=dict(size=10), outsidetextfont=dict(size=10),
        hovertemplate="<b>%{label}</b><br>\u00A3%{value:,.0f}<br>"
                      "%{percentParent:.1%} of group | "
                      "%{percentRoot:.1%} of total<extra></extra>"))
    fig.update_layout(height=480, margin=dict(l=0, r=0, t=10, b=0),
                      paper_bgcolor="white")

    return fig, _breakdown_table(types, grouped, total, offset, dropped)


def _breakdown_table(types, grouped, total, offset=None, dropped=None):
    offset = offset or {}
    rows = []
    for atype, tval in types.items():
        rows.append(html.Tr([
            html.Td(atype, style={"padding": "5px 8px", "fontSize": "11.5px",
                    "fontWeight": 700, "color": theme.INK}),
            html.Td(f"{tval/1000:,.1f}k", style={"padding": "5px 8px",
                    "fontSize": "11.5px", "textAlign": "right", **theme.NUM,
                    "fontWeight": 700, "color": theme.INK}),
            html.Td(f"{tval/total*100:.1f}%", style={"padding": "5px 8px",
                    "fontSize": "11px", "textAlign": "right", **theme.NUM,
                    "color": theme.SLATE}),
        ], style={"borderTop": f"1px solid {theme.LINE}"}))
        subset = grouped[grouped["asset_type"] == atype]
        for r in subset.sort_values("value", ascending=False).itertuples():
            netted = offset.get((atype, r.category))
            label = r.category
            title = None
            if netted:
                # The figure is a net, so say what came off it - otherwise the
                # category silently disagrees with the Portfolio tab, where
                # the two rows are listed separately.
                shown = ", ".join(f"{n} \u00A3{v:,.0f}" for n, v in netted)
                label = f"{r.category} (net)"
                title = f"after {shown}"
            rows.append(html.Tr([
                html.Td(html.Span(label, title=title) if title else label,
                        style={"padding": "3px 8px 3px 20px",
                               "fontSize": "11px", "color": theme.SLATE}),
                html.Td(f"{r.value/1000:,.1f}k", style={"padding": "3px 8px",
                        "fontSize": "11px", "textAlign": "right", **theme.NUM,
                        "color": theme.TEXT}),
                html.Td(f"{r.value/total*100:.1f}%",
                        style={"padding": "3px 8px", "fontSize": "10.5px",
                               "textAlign": "right", **theme.NUM,
                               "color": theme.NEUTRAL}),
            ]))
    rows.append(html.Tr([
        html.Td("TOTAL", style={"padding": "6px 8px", "fontSize": "12px",
                "fontWeight": 700, "color": theme.INK,
                "borderTop": f"2px solid {theme.INK}"}),
        html.Td(f"{total/1000:,.1f}k", style={"padding": "6px 8px",
                "fontSize": "12px", "textAlign": "right", **theme.NUM,
                "fontWeight": 700, "color": theme.INK,
                "borderTop": f"2px solid {theme.INK}"}),
        html.Td("", style={"borderTop": f"2px solid {theme.INK}"}),
    ]))

    table = html.Table(html.Tbody(rows),
                       style={"width": "100%", "borderCollapse": "collapse"})

    if dropped is not None and not dropped.empty:
        note = html.Div(
            "Not charted, net zero or negative: "
            + ", ".join(f"{r.category} \u00A3{r.value:,.0f}"
                        for r in dropped.itertuples()),
            style={"fontSize": "10.5px", "color": theme.NEGATIVE,
                   "marginTop": "8px"})
        return html.Div([table, note])
    return table


# --- net worth ------------------------------------------------------------

@callback(Output("pch-networth", "figure"),
          Input("pf-tabs", "value"),
          Input("pch-networth-from", "date"))
def _networth(_tab, since):
    df = repo.networth_history()
    if df.empty:
        return _blank("No net worth history yet")

    if since:
        df = df[df["date"] >= since[:10]]
    if df.empty:
        return _blank("No net worth history in that range")

    # The series is daily since the history rebuild but sparse before it, so
    # neither markers nor monthly ticks suit every range. Both follow from how
    # far apart the points actually are.
    span_days = (pd.Timestamp(df["date"].max())
                 - pd.Timestamp(df["date"].min())).days or 1
    sparse = span_days / max(len(df), 1) > 5

    fig = go.Figure(go.Scatter(
        x=df["date"], y=df["value"],
        mode="lines+markers" if sparse else "lines", name="Net worth",
        line=dict(color=theme.INK, width=2),
        marker=dict(size=4, color=theme.INK),
        customdata=df["value"] / 1000,
        hovertemplate="%{x|%d %b}: \u00A3%{customdata:,.0f}k<extra></extra>"))

    # Label the first reading of each month on the line itself. Every point
    # would be unreadable on a daily series, and month starts give a regular
    # spine to read the trend against.
    dates = pd.to_datetime(df["date"])
    first_of_month = df[~dates.dt.to_period("M").duplicated()]
    if not first_of_month.empty:
        fig.add_trace(go.Scatter(
            x=first_of_month["date"], y=first_of_month["value"],
            mode="markers+text", showlegend=False,
            marker=dict(size=5, color=theme.INK),
            text=[f"{v/1000:,.0f}k" for v in first_of_month["value"]],
            textposition="top center",
            textfont=dict(size=9, color=theme.SLATE),
            hoverinfo="skip"))

    fig.update_layout(height=340, hovermode="closest", showlegend=False,
                      plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=58, r=20, t=24, b=40))

    if span_days > 400:
        # Sixty monthly labels would be unreadable; let plotly choose.
        fig.update_xaxes(showgrid=False, tickfont=dict(size=10))
    else:
        fig.update_xaxes(showgrid=False, tickfont=dict(size=10),
                         dtick="M1", tickformat="%b %y",
                         ticklabelmode="period")

    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5",
                     tickfont=dict(size=10), tickprefix="\u00A3",
                     tickformat=",.0f")
    return fig


# --- category history -----------------------------------------------------

def _category_pivot(since):
    """
    Date x category matrix, filtered and with small categories grouped.

    Returns (pivot, ordered_columns), or (None, message) if there is nothing
    to chart. Shared by both stacked charts so they always show the same
    categories and the same Other grouping.
    """
    df = repo.snapshot_category_history()
    if df.empty:
        return None, "No category history yet"

    if since:
        df = df[df["date"] >= since[:10]]
    if df.empty or df["date"].nunique() < 2:
        return None, "Need at least two snapshots in that range"

    pivot = df.pivot_table(index="date", columns="category",
                           values="value_gbp", aggfunc="sum").fillna(0)

    # A stacked area cannot show a negative band: it would fold back over the
    # ones beneath it. Categories are netted by the query, so this only bites
    # if one ever nets below zero.
    for c in pivot.columns:
        if (pivot[c] < 0).any():
            pivot[c] = pivot[c].clip(lower=0)

    latest_total = pivot.iloc[-1].sum()
    if latest_total == 0:
        return None, "Latest snapshot has no value"

    # The threshold is applied per date, not against the latest column. Using
    # today's shares meant a category that was 20% in May and is 1% now got
    # folded into Other for its whole history - which is why Other was a third
    # of the chart on the left and shrank steadily, hiding exactly the
    # composition change the chart exists to show.
    threshold = _threshold()
    totals = pivot.sum(axis=1)
    shares = pivot.div(totals.replace(0, pd.NA), axis=0).fillna(0)

    small = shares < threshold
    other = pivot.where(small, 0).sum(axis=1)
    kept = pivot.where(~small, 0)

    kept = kept.loc[:, (kept != 0).any()]

    if (other > 0).any():
        kept["Other"] = other

    cols = [c for c in kept.columns if c != "Other"]
    cols = sorted(cols, key=lambda c: kept[c].iloc[-1], reverse=True)
    if "Other" in kept.columns:
        cols.append("Other")
    return kept, cols
    
    cols = sorted(above, key=lambda c: pivot[c].iloc[-1], reverse=True)
    if below:
        cols.append("Other")
    return pivot, cols


def _stack_layout(fig, height=420):
    # Not "x unified": with twenty-plus categories the box fills the screen,
    # and most rows are a category that was below the threshold that day.
    # Plotly renders those as NaN rather than omitting them, so the fix is to
    # show one band at a time.
    fig.update_layout(height=height, hovermode="closest",
                      plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=58, r=20, t=16, b=96),
                      hoverlabel=dict(font_size=10),
                      legend=dict(orientation="h", y=-0.22, x=0,
                                  font=dict(size=10), traceorder="reversed"))
    fig.update_xaxes(showgrid=False, tickfont=dict(size=10))
    return fig


@callback(Output("pch-categories", "figure"),
          Input("pf-tabs", "value"),
          Input("pch-categories-from", "date"))
def _categories(_tab, since):
    pivot, cols = _category_pivot(since)
    if pivot is None:
        return _blank(cols)

    fig = go.Figure()
    for i, cat in enumerate(reversed(cols)):
        colour = PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(
            x=pivot.index, y=pivot[cat].fillna(0), name=cat, mode="lines",
            stackgroup="one", line=dict(width=0.5, color=colour),
            fillcolor=colour,
            customdata=pivot[cat] / 1000,
            hovertemplate=f"<b>{cat}</b> "
                          "\u00A3%{customdata:,.0f}k<extra></extra>"))
    _stack_layout(fig)
    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5",
                     tickfont=dict(size=10), tickprefix="\u00A3",
                     tickformat=",.0f")
    return fig


# --- composition ----------------------------------------------------------

@callback(Output("pch-composition", "figure"),
          Input("pf-tabs", "value"),
          Input("pch-composition-from", "date"))
def _composition(_tab, since):
    pivot, cols = _category_pivot(since)
    if pivot is None:
        return _blank(cols)

    cols = [c for c in cols if c not in COMPOSITION_EXCLUDE]
    if not cols:
        return _blank("Nothing to chart once property is excluded")

    # Shares are taken over the columns actually charted, so the bands total
    # 100% rather than leaving a gap where property was.
    totals = pivot[cols].sum(axis=1)
    share = pivot[cols].div(totals.replace(0, pd.NA), axis=0) * 100

    fig = go.Figure()
    for i, cat in enumerate(reversed(cols)):
        colour = PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(
            x=share.index, y=share[cat].fillna(0), name=cat, mode="lines",
            stackgroup="one", line=dict(width=0.5, color=colour),
            fillcolor=colour,
            customdata=pivot[cat] / 1000,
            hovertemplate=f"<b>{cat}</b> %{{y:.1f}}% "
                          "\u00A3%{customdata:,.0f}k<extra></extra>"))
    _stack_layout(fig)
    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5",
                     tickfont=dict(size=10), ticksuffix="%",
                     tickformat=".0f", range=[0, 100])
    return fig


def _blank(msg):
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False,
                       font=dict(size=12, color=theme.NEUTRAL))
    fig.update_layout(height=320, plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=40, r=40, t=10, b=40))
    return fig