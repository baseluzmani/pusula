import dash
from dash import html, dcc, callback, Input, Output

import plotly.graph_objects as go

from core import theme, reference
from core.repo import etf as repo
from ui import figures
from ui.layout import page_header, subtabs, card, placeholder

dash.register_page(__name__, path="/etf", name="ETF", order=4)

TABS = ["Holdings", "Overlap", "Changes", "Compare", "Ticker map", "Sources"]

TREND_TOP_N = 20


def _etf_options():
    return [{"label": reference.short_name(e), "value": e}
            for e in repo.list_etfs()]


def _control(label, component, width="220px"):
    return html.Div([
        html.Label(label, style={
            "display": "block", "fontSize": "10.5px", "fontWeight": 600,
            "letterSpacing": "0.07em", "textTransform": "uppercase",
            "color": theme.SLATE, "marginBottom": "5px"}),
        component,
    ], style={"width": width, "marginRight": "14px"})


layout = html.Div([
    subtabs("etf-tabs", TABS),
    html.Div([
        page_header("ETF and fund holdings",
                    "Look-through holdings consolidated by FIGI, so the same "
                    "company held under different tickers counts once."),
        html.Div(id="etf-body"),
    ], style=theme.PAGE),
])


def _holdings_view():
    options = _etf_options()
    default = options[0]["value"] if options else None
    return html.Div([
        html.Div([
            _control("ETF", dcc.Dropdown(id="etf-h-fund", options=options,
                                         value=default, clearable=False)),
            _control("Snapshot date", dcc.Dropdown(id="etf-h-date",
                                                   clearable=False)),
        ], style={"display": "flex", "alignItems": "flex-end",
                  "marginBottom": "18px", "flexWrap": "wrap"}),

        html.Div(id="etf-h-stats", style={"marginBottom": "18px"}),
        card("Portfolio composition", html.Div(id="etf-h-table")),
        card(f"Weight trend - top {TREND_TOP_N} holdings",
             dcc.Graph(id="etf-h-heatmap", config={"displayModeBar": False})),
        card("Weight trend - line",
             dcc.Graph(id="etf-h-line", config={"displayModeBar": False})),
    ])


def _stat(label, value, sub=""):
    return html.Div([
        html.Div(value, style={"fontSize": "21px", "fontWeight": 600,
                               "color": theme.TEXT, **theme.NUM}),
        html.Div(label, style={"fontSize": "10.5px", "fontWeight": 600,
                               "letterSpacing": "0.07em", "color": theme.SLATE,
                               "textTransform": "uppercase", "marginTop": "3px"}),
        html.Div(sub, style={"fontSize": "11px", "color": theme.NEUTRAL,
                             "marginTop": "2px", "whiteSpace": "nowrap",
                             "overflow": "hidden", "textOverflow": "ellipsis"}),
    ], style={"flex": "1", "minWidth": "140px", "backgroundColor": theme.SURFACE,
              "border": f"1px solid {theme.LINE}", "borderRadius": "6px",
              "padding": "14px 16px"})


def _weight_bar(weight, max_weight):
    pct = (weight or 0) / (max_weight or 1) * 100
    return html.Div([
        html.Div(style={"width": f"{pct:.0f}%", "height": "6px",
                        "backgroundColor": "#2E75B6", "borderRadius": "3px",
                        "minWidth": "1px"}),
        html.Span(f"{weight:.2f}%" if weight is not None else "-",
                  style={"marginLeft": "10px", "fontSize": "12px",
                         "color": theme.TEXT, **theme.NUM}),
    ], style={"display": "flex", "alignItems": "center"})


def _holdings_table(df):
    max_w = df["weight_pct"].max() or 1
    head = html.Thead(html.Tr([
        html.Th("#", style={"width": "36px"}),
        html.Th("Name"),
        html.Th("Sector"),
        html.Th("FIGI", style={"width": "130px"}),
        html.Th("Weight", style={"width": "200px"}),
    ]))
    rows = []
    for i, r in enumerate(df.itertuples(), start=1):
        figi = str(r.canonical_id)
        sector = r.sector if r.sector and str(r.sector) != "nan" else "-"
        rows.append(html.Tr([
            html.Td(i, style={"color": theme.NEUTRAL, **theme.NUM}),
            html.Td(r.name, style={"fontWeight": 500}),
            html.Td(sector, style={"color": theme.SLATE}),
            html.Td(figi[:14] + ("..." if len(figi) > 14 else ""),
                    style={"color": theme.NEUTRAL, "fontSize": "11px",
                           **theme.NUM}),
            html.Td(_weight_bar(r.weight_pct, max_w)),
        ]))
    return html.Div(html.Table([head, html.Tbody(rows)], className="pz"),
                    style={"overflowX": "auto"})


@callback(
    Output("etf-h-date", "options"), Output("etf-h-date", "value"),
    Input("etf-h-fund", "value"),
)
def _dates(etf_id):
    if not etf_id:
        return [], None
    dates = repo.list_dates(etf_id)
    return [{"label": d, "value": d} for d in dates], (dates[0] if dates else None)


@callback(
    Output("etf-h-stats", "children"),
    Output("etf-h-table", "children"),
    Output("etf-h-heatmap", "figure"),
    Output("etf-h-line", "figure"),
    Input("etf-h-fund", "value"), Input("etf-h-date", "value"),
)
def _holdings(etf_id, date):
    blank = figures.empty()
    if not etf_id or not date:
        return html.Div(), placeholder("Select an ETF and a date."), blank, blank

    df = repo.holdings(etf_id, date)
    if df.empty:
        return html.Div(), placeholder("No holdings for this snapshot."), blank, blank

    s = repo.summary(df)
    stats = html.Div([
        _stat("Holdings", f"{s['holdings']:,}", "distinct positions"),
        _stat("Top 5", f"{s['top5']:.1f}%", "concentration"),
        _stat("Top 10", f"{s['top10']:.1f}%", "concentration"),
        _stat("Sectors", str(s["sectors"]), f"largest: {s['top_sector'][:20]}"),
        _stat("Top sector", f"{s['top_sector_weight']:.1f}%", s["top_sector"][:22]),
        _stat("Largest", f"{s['largest_weight']:.1f}%", s["largest_name"][:22]),
    ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap"})

    table = _holdings_table(df)

    top_ids = df.head(TREND_TOP_N)["canonical_id"].tolist()
    hist = repo.holdings_history(etf_id, top_ids)
    if hist.empty:
        return stats, table, blank, blank

    pivot = hist.pivot_table(index="name", columns="scraped_date",
                             values="weight_pct", aggfunc="mean")
    anchor = date if date in pivot.columns else pivot.columns[-1]
    pivot = pivot.reindex(pivot[anchor].sort_values().index)
    x = [str(c)[:10] for c in pivot.columns]

    heat = go.Figure(go.Heatmap(
        z=pivot.values, x=x, y=[str(n)[:34] for n in pivot.index],
        colorscale=figures.HEATMAP_SCALE,
        hovertemplate="<b>%{y}</b><br>%{x}<br>%{z:.2f}%<extra></extra>",
        colorbar=dict(title="Weight %", thickness=10, outlinewidth=0),
    ))
    heat.update_layout(**figures.base_layout(
        height=max(300, len(pivot) * 28 + 90),
        margin=dict(l=210, r=60, t=14, b=58),
        xaxis=dict(tickangle=-45), yaxis=dict(automargin=True),
    ))

    line = go.Figure()
    for i, name in enumerate(reversed(pivot.index.tolist())):
        line.add_trace(go.Scatter(
            x=x, y=pivot.loc[name].values, mode="lines+markers",
            name=str(name)[:28],
            line=dict(color=figures.SEQUENCE[i % len(figures.SEQUENCE)], width=2),
            marker=dict(size=4),
            hovertemplate=f"<b>{str(name)[:28]}</b><br>%{{x}}<br>%{{y:.2f}}%<extra></extra>",
        ))
    line.update_layout(**figures.base_layout(
        height=400, margin=dict(l=44, r=180, t=14, b=58),
        legend=dict(font=dict(size=10), x=1.01, y=1),
        yaxis=dict(title="Weight %", gridcolor=theme.LINE_SOFT),
        xaxis=dict(tickangle=-45, gridcolor=theme.LINE_SOFT),
        hovermode="x unified",
    ))
    return stats, table, heat, line


@callback(Output("etf-body", "children"), Input("etf-tabs", "value"))
def _render(tab):
    if tab == "Holdings":
        return _holdings_view()
    return placeholder(f"{tab} - not migrated yet")
