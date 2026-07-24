import dash
from dash import html, dcc, callback, Input, Output

import plotly.graph_objects as go

from core import theme, reference
from core.repo import etf as repo
from core.repo import funds as funds_repo
from ui import figures
from ui.layout import subtabs, card, placeholder

dash.register_page(__name__, path="/etf", name="ETF", order=4)

TABS = ["Holdings", "Changes", "Compare", "Fund compare", "Ticker map", "Sources"]

# One line per tab, explaining what this tab does that the others don't.
# Leave a tab out entirely if its name is self-explanatory.
TAB_INTRO = {
    "Holdings": "What a fund owns on a given date, consolidated by FIGI so a "
                "company held under several tickers counts once.",
    "Changes": "What the manager bought, sold, added to and trimmed between "
               "two snapshot dates.",
    "Compare": "Where two funds hold the same names, and how much capital "
               "genuinely sits in the same companies.",
    "Fund compare": "Any two holdings sets side by side - ETF, pension fund, "
                    "or a composite blended from several funds.",
    "Ticker map": "The identifier table tying provider tickers, ISINs and "
                  "SEDOLs to a single FIGI. Everything else depends on it.",
    "Sources": "Where each fund's holdings file comes from.",
}

TREND_TOP_N = 20
MIN_CHANGE_WEIGHT = 0.5     # ignore long-tail noise in Changes
COMPARE_HEATMAP_N = 30


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
    html.Div(html.Div(id="etf-body"), style=theme.PAGE),
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


def _weight_bar(weight, max_weight, colour="#2E75B6"):
    pct = (weight or 0) / (max_weight or 1) * 100
    return html.Div([
        html.Div(style={"width": f"{pct:.0f}%", "height": "6px",
                        "backgroundColor": colour, "borderRadius": "3px",
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


# --- Changes tab ---------------------------------------------------------

def _changes_view():
    options = _etf_options()
    default = options[0]["value"] if options else None
    return html.Div([
        html.Div([
            _control("ETF", dcc.Dropdown(id="etf-c-fund", options=options,
                                         value=default, clearable=False)),
            _control("From", dcc.Dropdown(id="etf-c-from", clearable=False), "190px"),
            _control("To", dcc.Dropdown(id="etf-c-to", clearable=False), "190px"),
        ], style={"display": "flex", "alignItems": "flex-end",
                  "marginBottom": "18px", "flexWrap": "wrap"}),
        html.Div(id="etf-c-body"),
    ])


def _change_section(title, df, accent, show_delta):
    n = len(df)
    if n == 0:
        body = html.Div("None", style={"color": theme.NEUTRAL, "fontSize": "12.5px"})
    else:
        if show_delta:
            head = html.Thead(html.Tr([
                html.Th("Name"), html.Th("From"), html.Th("To"), html.Th("Change")]))
            rows = [html.Tr([
                html.Td(r.name, style={"fontWeight": 500}),
                html.Td(f"{r.weight_from:.2f}%", className="num",
                        style={"color": theme.SLATE}),
                html.Td(f"{r.weight_to:.2f}%", className="num"),
                html.Td(f"{r.change:+.2f}%", className="num",
                        style={"color": theme.colour_for(r.change), "fontWeight": 600}),
            ]) for r in df.itertuples()]
        else:
            weight_col = "weight_to" if "new" in title.lower() else "weight_from"
            head = html.Thead(html.Tr([html.Th("Name"), html.Th("Weight")]))
            rows = [html.Tr([
                html.Td(r.name, style={"fontWeight": 500}),
                html.Td(f"{getattr(r, weight_col):.2f}%", className="num",
                        style={"color": accent, "fontWeight": 600}),
            ]) for r in df.itertuples()]
        body = html.Div(html.Table([head, html.Tbody(rows)], className="pz"),
                        style={"overflowX": "auto"})

    return html.Div([
        html.Div(f"{title} ({n})", style={**theme.CARD_TITLE, "color": accent}),
        body,
    ], style={**theme.CARD, "borderLeft": f"3px solid {accent}", "borderRadius": "0 6px 6px 0"})


@callback(
    Output("etf-c-from", "options"), Output("etf-c-from", "value"),
    Output("etf-c-to", "options"), Output("etf-c-to", "value"),
    Input("etf-c-fund", "value"),
)
def _change_dates(etf_id):
    if not etf_id:
        return [], None, [], None
    dates = repo.list_dates(etf_id)
    opts = [{"label": d, "value": d} for d in dates]
    frm = dates[1] if len(dates) > 1 else (dates[0] if dates else None)
    return opts, frm, opts, (dates[0] if dates else None)


@callback(
    Output("etf-c-body", "children"),
    Input("etf-c-fund", "value"), Input("etf-c-from", "value"),
    Input("etf-c-to", "value"),
)
def _changes(etf_id, date_from, date_to):
    if not etf_id or not date_from or not date_to or date_from == date_to:
        return placeholder("Select an ETF and two different dates.")

    c = repo.changes(etf_id, date_from, date_to, MIN_CHANGE_WEIGHT)
    row = {"display": "flex", "gap": "14px", "flexWrap": "wrap"}
    half = {"flex": "1", "minWidth": "320px"}

    return html.Div([
        html.Div(f"{reference.short_name(etf_id)}   {date_from} to {date_to}   "
                 f"(holdings below {MIN_CHANGE_WEIGHT}% excluded)",
                 style={"fontSize": "12.5px", "color": theme.SLATE,
                        "marginBottom": "14px"}),
        html.Div([
            html.Div(_change_section("New positions", c["new"],
                                     theme.POSITIVE, False), style=half),
            html.Div(_change_section("Removed positions", c["removed"],
                                     theme.NEGATIVE, False), style=half),
        ], style=row),
        html.Div([
            html.Div(_change_section("Increased", c["increased"],
                                     "#2E75B6", True), style=half),
            html.Div(_change_section("Decreased", c["decreased"],
                                     theme.NEEDLE, True), style=half),
        ], style=row),
    ])


# --- Compare tab ---------------------------------------------------------

def _compare_view():
    options = _etf_options()
    a = options[0]["value"] if options else None
    b = options[1]["value"] if len(options) > 1 else None
    return html.Div([
        html.Div([
            _control("ETF A", dcc.Dropdown(id="etf-cmp-a", options=options,
                                           value=a, clearable=False)),
            _control("ETF B", dcc.Dropdown(id="etf-cmp-b", options=options,
                                           value=b, clearable=False)),
        ], style={"display": "flex", "alignItems": "flex-end",
                  "marginBottom": "18px", "flexWrap": "wrap"}),
        html.Div(id="etf-cmp-body"),
    ])


def _simple_table(df, weight_col, accent, numbered=False):
    if df.empty:
        return html.Div("None", style={"color": theme.NEUTRAL, "fontSize": "12.5px"})
    headers = ([html.Th("#", style={"width": "34px"})] if numbered else []) + \
              [html.Th("Name"), html.Th("Weight", style={"width": "90px"})]
    rows = []
    for i, r in enumerate(df.itertuples(), start=1):
        cells = ([html.Td(i, className="num",
                          style={"color": theme.NEUTRAL})] if numbered else [])
        cells += [
            html.Td(r.name, style={"fontWeight": 500}),
            html.Td(f"{getattr(r, weight_col):.2f}%", className="num",
                    style={"color": accent, "fontWeight": 600}),
        ]
        rows.append(html.Tr(cells))
    return html.Div(html.Table([html.Thead(html.Tr(headers)), html.Tbody(rows)],
                               className="pz"), style={"overflowX": "auto"})


def _common_table(df, name_a, name_b):
    if df.empty:
        return html.Div("No common holdings.",
                        style={"color": theme.NEUTRAL, "fontSize": "12.5px"})
    max_w = max(df["weight_a"].max(), df["weight_b"].max()) or 1
    head = html.Thead(html.Tr([
        html.Th("#", style={"width": "34px"}),
        html.Th("Name"), html.Th("Sector"),
        html.Th(name_a, style={"width": "160px"}),
        html.Th(name_b, style={"width": "160px"}),
        html.Th("A - B", style={"width": "80px"}),
    ]))
    rows = []
    for i, r in enumerate(df.itertuples(), start=1):
        sector = r.sector if r.sector and str(r.sector) != "nan" else "-"
        rows.append(html.Tr([
            html.Td(i, className="num", style={"color": theme.NEUTRAL}),
            html.Td(r.name, style={"fontWeight": 500}),
            html.Td(sector, style={"color": theme.SLATE}),
            html.Td(_weight_bar(r.weight_a, max_w)),
            html.Td(_weight_bar(r.weight_b, max_w, theme.NEEDLE)),
            html.Td(f"{r.diff:+.2f}%", className="num",
                    style={"color": theme.colour_for(r.diff), "fontWeight": 600}),
        ]))
    return html.Div(html.Table([head, html.Tbody(rows)], className="pz"),
                    style={"overflowX": "auto"})


@callback(
    Output("etf-cmp-body", "children"),
    Input("etf-cmp-a", "value"), Input("etf-cmp-b", "value"),
)
def _compare(etf_a, etf_b):
    if not etf_a or not etf_b or etf_a == etf_b:
        return placeholder("Select two different ETFs.")

    c = repo.compare(etf_a, etf_b)
    if not c:
        return placeholder("No holdings data for one of these ETFs.")

    na, nb = reference.short_name(etf_a), reference.short_name(etf_b)

    stats = html.Div([
        _stat("Common", f"{len(c['common']):,}", "held by both"),
        _stat(f"Only {na}", f"{len(c['only_a']):,}",
              f"{len(c['holdings_a'])} total"),
        _stat(f"Only {nb}", f"{len(c['only_b']):,}",
              f"{len(c['holdings_b'])} total"),
        _stat("Overlap", f"{c['overlap']:.1f}%", "sum of min(A, B)"),
        _stat(f"Top 10 {na}", f"{c['top10_weight_a']:.1f}%", "concentration"),
        _stat(f"Top 10 {nb}", f"{c['top10_weight_b']:.1f}%", "concentration"),
    ], style={"display": "flex", "gap": "12px", "flexWrap": "wrap",
              "marginBottom": "18px"})

    half = {"flex": "1", "minWidth": "320px"}
    top10 = html.Div([
        html.Div(card(f"Top 10 - {na} ({c['top10_weight_a']:.1f}% of fund)",
                      _simple_table(c["top10_a"], "weight_pct", "#2E75B6", True)),
                 style=half),
        html.Div(card(f"Top 10 - {nb} ({c['top10_weight_b']:.1f}% of fund)",
                      _simple_table(c["top10_b"], "weight_pct", theme.NEEDLE, True)),
                 style=half),
    ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap"})

    only = html.Div([
        html.Div(card(f"Only in {na} ({len(c['only_a'])})",
                      _simple_table(c["only_a"], "weight_a", "#2E75B6")),
                 style=half),
        html.Div(card(f"Only in {nb} ({len(c['only_b'])})",
                      _simple_table(c["only_b"], "weight_b", theme.NEEDLE)),
                 style=half),
    ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap"})

    heat = figures.empty()
    if not c["common"].empty:
        top = c["common"].head(COMPARE_HEATMAP_N).copy()
        top = top.iloc[::-1]
        heat = go.Figure(go.Heatmap(
            z=top[["weight_a", "weight_b"]].values, x=[na, nb],
            y=[str(n)[:34] for n in top["name"]],
            colorscale=figures.HEATMAP_SCALE,
            hovertemplate="<b>%{y}</b><br>%{x}: %{z:.2f}%<extra></extra>",
            colorbar=dict(title="Weight %", thickness=10, outlinewidth=0),
        ))
        heat.update_layout(**figures.base_layout(
            height=max(300, len(top) * 22 + 90),
            margin=dict(l=210, r=60, t=14, b=40),
            yaxis=dict(automargin=True),
        ))

    return html.Div([
        html.Div(f"Latest snapshot - {na}: {c['date_a']}   |   {nb}: {c['date_b']}",
                 style={"fontSize": "12.5px", "color": theme.SLATE,
                        "marginBottom": "14px"}),
        stats,
        top10,
        card("Common holdings", _common_table(c["common"], na, nb)),
        only,
        card(f"Overlap heatmap - top {COMPARE_HEATMAP_N} common holdings",
             dcc.Graph(figure=heat, config={"displayModeBar": False})),
    ])


# --- Fund compare tab ----------------------------------------------------

def _fund_compare_view():
    options = funds_repo.options()
    _, latest = funds_repo.date_bounds()
    return html.Div([
        html.Div([
            _control("As of", dcc.DatePickerSingle(
                id="etf-fc-date", date=latest, display_format="YYYY-MM-DD",
                style={"width": "100%"}), "170px"),
            _control("Left", dcc.Dropdown(id="etf-fc-a", options=options,
                                          placeholder="Select a fund"), "300px"),
            _control("Right", dcc.Dropdown(id="etf-fc-b", options=options,
                                           placeholder="Select a fund"), "300px"),
        ], style={"display": "flex", "alignItems": "flex-end",
                  "marginBottom": "18px", "flexWrap": "wrap"}),
        html.Div(id="etf-fc-body"),
    ])


def _fc_table(df, accent):
    if df.empty:
        return html.Div("No holdings for this date.",
                        style={"color": theme.NEUTRAL, "fontSize": "12.5px"})
    head = html.Thead(html.Tr([
        html.Th("#", style={"width": "34px"}),
        html.Th("Company"),
        html.Th("Ticker", style={"width": "110px"}),
        html.Th("Weight", style={"width": "90px"}),
    ]))
    rows = [html.Tr([
        html.Td(r.rank, className="num", style={"color": theme.NEUTRAL}),
        html.Td(r.name, style={"fontWeight": 500}),
        html.Td(str(r.ticker or "-")[:14],
                style={"color": theme.NEUTRAL, "fontSize": "11px", **theme.NUM}),
        html.Td(f"{r.weight_pct:.2f}%", className="num",
                style={"color": accent, "fontWeight": 600}),
    ]) for r in df.itertuples()]

    total = df["weight_pct"].sum()
    rows.append(html.Tr([
        html.Td("", colSpan=3),
        html.Td(f"{total:.1f}%", className="num",
                style={"fontWeight": 600, "borderTop": f"2px solid {theme.LINE}"}),
    ]))
    return html.Div(html.Table([head, html.Tbody(rows)], className="pz"),
                    style={"maxHeight": "620px", "overflowY": "auto"})


def _fc_side(df, name, date, as_of, accent):
    stale = date and as_of and date < as_of
    return card(
        name,
        html.Div([
            html.Span(f"As of {date}" if date else "No data"),
            html.Span("  (latest available on or before the selected date)"
                      if stale else "",
                      style={"color": theme.NEEDLE}),
        ], style={"fontSize": "11.5px", "color": theme.SLATE,
                  "marginBottom": "10px", **theme.NUM}),
        _fc_table(df, accent),
    )


@callback(
    Output("etf-fc-body", "children"),
    Input("etf-fc-a", "value"), Input("etf-fc-b", "value"),
    Input("etf-fc-date", "date"),
)
def _fund_compare(sel_a, sel_b, as_of):
    if not sel_a and not sel_b:
        return placeholder("Select a fund on either side.")

    df_a, name_a, date_a = funds_repo.holdings_for(sel_a, as_of)
    df_b, name_b, date_b = funds_repo.holdings_for(sel_b, as_of)

    half = {"flex": "1", "minWidth": "340px"}
    return html.Div([
        html.Div(_fc_side(df_a, name_a, date_a, as_of, "#2E75B6"), style=half),
        html.Div(_fc_side(df_b, name_b, date_b, as_of, theme.NEEDLE), style=half),
    ], style={"display": "flex", "gap": "14px", "flexWrap": "wrap"})


@callback(Output("etf-body", "children"), Input("etf-tabs", "value"))
def _render(tab):
    views = {
        "Holdings": _holdings_view,
        "Changes": _changes_view,
        "Compare": _compare_view,
        "Fund compare": _fund_compare_view,
    }
    intro = TAB_INTRO.get(tab, "")
    body = views[tab]() if tab in views else placeholder(f"{tab} - not migrated yet")
    return html.Div([
        html.P(intro, style=theme.SUBTITLE) if intro else None,
        body,
    ])
