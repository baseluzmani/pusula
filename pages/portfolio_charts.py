"""
Portfolio - Charts tab.

Three views:
  1. Sunburst breakdown - asset type on the inner ring, category on the outer -
     with the numbers beside it.
  2. Net worth history from the nightly networth_history table.
  3. Portfolio value by category over time, stacked, with small categories
     rolled into Other so the legend stays readable.
"""

from __future__ import annotations

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd
import plotly.graph_objects as go

from core import theme, finance as fin, valuation as val
from core.repo import portfolio as repo

# Composite definitions and the chart threshold come from FTScrapper's
# config, loaded by file path - a plain `import config` fails from Pusula.
def _composites():
    return val.composite_definitions()


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


# Stacked-area chart keeps its own palette.
PALETTE = ["#2E6FB5", "#1A7A4C", "#C0392B", "#D06018", "#7D5BA6", "#137E9E",
           "#B8860F", "#4A6FA5", "#2C3E50", "#27AE60", "#2980B9", "#7F8C8D",
           "#1ABC9C", "#E74C3C", "#95A5A6", "#8E44AD"]


def render():
    return html.Div([
        html.Div([
            html.Div("Portfolio breakdown", style=theme.CARD_TITLE),
            html.Div("Inner ring: asset type. Outer ring: category within it.",
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
            html.Div("Net worth history", style=theme.CARD_TITLE),
            dcc.Graph(id="pch-networth", config={"displayModeBar": False}),
        ], style={**theme.CARD, "marginBottom": "12px"}),

        html.Div([
            html.Div("Portfolio value by category", style=theme.CARD_TITLE),
            html.Div("Categories below "
                     f"{_threshold():.0%} of the latest total are grouped "
                     "as Other.",
                     style={"fontSize": "11px", "color": theme.NEUTRAL,
                            "marginBottom": "6px"}),
            dcc.Graph(id="pch-categories", config={"displayModeBar": False}),
        ], style=theme.CARD),
    ])


# --- breakdown ------------------------------------------------------------

def _valued_frame():
    instruments = repo.instruments()
    prices = repo.prices()
    rates = fin.fx_rates(prices)
    price_map = fin.latest_price_map(prices)
    valued = val.value_holdings(repo.holdings(), instruments, price_map,
                                rates["USD"], rates, _composites())
    if not valued.empty:
        valued = valued[valued["value"].notna() & (valued["value"] > 0)]

    cash_total = val.cash_total_gbp(repo.cash_accounts(), rates)
    if cash_total:
        extra = pd.DataFrame([{"fund_id": "CASH:TOTAL", "name": "Cash",
                               "asset_type": "Cash", "category": "Cash",
                               "units": 1, "price_gbp": cash_total,
                               "value": cash_total}])
        valued = pd.concat([valued, extra], ignore_index=True)
    return valued


@callback(Output("pch-sunburst", "figure"), Output("pch-breakdown", "children"),
          Input("pf-tabs", "value"))
def _sunburst(_tab):
    valued = _valued_frame()
    if valued.empty:
        return _blank("No holdings to chart"), html.Div()

    grouped = (valued.groupby(["asset_type", "category"], as_index=False)
               ["value"].sum())
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

    return fig, _breakdown_table(types, grouped, total)


def _breakdown_table(types, grouped, total):
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
            rows.append(html.Tr([
                html.Td(r.category, style={"padding": "3px 8px 3px 20px",
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
    return html.Table(html.Tbody(rows),
                      style={"width": "100%", "borderCollapse": "collapse"})


# --- net worth ------------------------------------------------------------

@callback(Output("pch-networth", "figure"), Input("pf-tabs", "value"))
def _networth(_tab):
    df = repo.networth_history()
    if df.empty:
        return _blank("No net worth history yet")

    fig = go.Figure(go.Scatter(
        x=df["date"], y=df["value"], mode="lines+markers", name="Net worth",
        line=dict(color=theme.INK, width=2),
        marker=dict(size=4, color=theme.INK),
        hovertemplate="%{x|%b %Y}: \u00A3%{y:,.0f}<extra></extra>"))
    fig.update_layout(height=320, hovermode="x unified", showlegend=False,
                      plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=58, r=20, t=14, b=40))
    fig.update_xaxes(showgrid=False, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5",
                     tickfont=dict(size=10), tickprefix="\u00A3",
                     tickformat=",.0f")
    return fig


# --- category history -----------------------------------------------------

@callback(Output("pch-categories", "figure"), Input("pf-tabs", "value"))
def _categories(_tab):
    df = repo.snapshot_category_history()
    if df.empty or df["date"].nunique() < 2:
        return _blank("Need at least two snapshots to chart category history")

    pivot = df.pivot_table(index="date", columns="category",
                           values="value_gbp", aggfunc="sum").fillna(0)
    latest_total = pivot.iloc[-1].sum()
    if latest_total == 0:
        return _blank("Latest snapshot has no value")

    threshold = _threshold()
    shares = pivot.iloc[-1] / latest_total
    above = [c for c in pivot.columns if shares.get(c, 0) >= threshold]
    below = [c for c in pivot.columns if shares.get(c, 0) < threshold]
    if below:
        pivot["Other"] = pivot[below].sum(axis=1)

    cols = sorted(above, key=lambda c: pivot[c].iloc[-1], reverse=True)
    if below:
        cols.append("Other")

    fig = go.Figure()
    for i, cat in enumerate(reversed(cols)):
        colour = PALETTE[i % len(PALETTE)]
        fig.add_trace(go.Scatter(
            x=pivot.index, y=pivot[cat], name=cat, mode="lines",
            stackgroup="one", line=dict(width=0.5, color=colour),
            fillcolor=colour,
            hovertemplate=f"<b>{cat}</b><br>%{{x|%d %b %Y}}: "
                          "\u00A3%{y:,.0f}<extra></extra>"))
    fig.update_layout(height=420, hovermode="x unified",
                      plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=58, r=20, t=16, b=96),
                      legend=dict(orientation="h", y=-0.22, x=0,
                                  font=dict(size=10), traceorder="reversed"))
    fig.update_xaxes(showgrid=False, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=True, gridcolor="#F0F2F5",
                     tickfont=dict(size=10), tickprefix="\u00A3",
                     tickformat=",.0f")
    return fig


def _blank(msg):
    fig = go.Figure()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False,
                       font=dict(size=12, color=theme.NEUTRAL))
    fig.update_layout(height=320, plot_bgcolor="white", paper_bgcolor="white",
                      margin=dict(l=40, r=40, t=10, b=40))
    return fig
