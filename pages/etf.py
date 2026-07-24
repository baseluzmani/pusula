import dash
from dash import html, dcc, callback, Input, Output, State, ctx, no_update

import plotly.graph_objects as go

from core import theme, reference, config, universe
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


# --- Ticker map tab ------------------------------------------------------

MAP_FILTERS = [
    {"label": "Unreviewed only", "value": "unreviewed"},
    {"label": "All records", "value": "all"},
    {"label": "Reviewed only", "value": "reviewed"},
    {"label": "Missing Yahoo ID", "value": "empty"},
    {"label": "Has Yahoo ID", "value": "has_yahoo"},
]


def _text_input(id_, placeholder, width="100%"):
    return dcc.Input(id=id_, type="text", placeholder=placeholder, debounce=True,
                     style={"width": width, "padding": "6px 9px", "fontSize": "13px",
                            "border": f"1px solid {theme.LINE}", "borderRadius": "4px"})


def _button(label, id_, primary=False):
    base = {"padding": "7px 15px", "borderRadius": "4px", "fontSize": "12.5px",
            "fontWeight": 600, "cursor": "pointer", "border": "none"}
    if primary:
        base |= {"backgroundColor": theme.POSITIVE, "color": "#fff"}
    else:
        base |= {"backgroundColor": theme.SURFACE, "color": theme.TEXT,
                 "border": f"1px solid {theme.LINE}"}
    return html.Button(label, id=id_, n_clicks=0, style=base)


def _map_view():
    return html.Div([
        html.Div(id="etf-m-summary", style={"marginBottom": "16px"}),
        html.Div([
            _control("Show", dcc.Dropdown(id="etf-m-status", options=MAP_FILTERS,
                                          value="unreviewed", clearable=False), "185px"),
            _control("Name or ticker", _text_input("etf-m-search", "filter..."), "200px"),
            _control("Yahoo ID", _text_input("etf-m-yahoo", "e.g. NVDA or .KS"), "160px"),
            _control("Group FIGI", _text_input("etf-m-group", "e.g. BBG000BCY2S8"), "180px"),
        ], style={"display": "flex", "alignItems": "flex-end", "flexWrap": "wrap",
                  "marginBottom": "14px"}),

        html.Div(id="etf-m-feedback", style={"marginBottom": "10px"}),
        html.Div(id="etf-m-edit", style={"marginBottom": "14px"}),
        card("Identifier records", html.Div(id="etf-m-table")),

        dcc.Store(id="etf-m-selected", data=None),
        dcc.Store(id="etf-m-figis", data=[]),
        dcc.Store(id="etf-m-refresh", data=0),
    ])


def _feedback(message, ok=True):
    colour = theme.POSITIVE if ok else theme.NEGATIVE
    return html.Div(message, style={
        "padding": "9px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour,
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})


def _cell(value, dash_if_empty=True):
    text = str(value) if value is not None else ""
    if text.lower() in ("nan", "none", ""):
        return "-" if dash_if_empty else ""
    return text


@callback(
    Output("etf-m-summary", "children"),
    Output("etf-m-table", "children"),
    Output("etf-m-figis", "data"),
    Input("etf-m-status", "value"), Input("etf-m-search", "value"),
    Input("etf-m-yahoo", "value"), Input("etf-m-group", "value"),
    Input("etf-m-refresh", "data"),
)
def _map_table(status, search, yahoo, group, _refresh):
    s = repo.map_summary()
    summary = html.Div([
        _pill(f"{s['total']:,}", "total", theme.TEXT),
        _pill(f"{s['unreviewed']:,}", "unreviewed", theme.NEEDLE),
        _pill(f"{s['reviewed']:,}", "reviewed", theme.POSITIVE),
    ], style={"display": "flex", "gap": "26px", "alignItems": "center"})

    df = repo.map_records(status, search or "", yahoo or "", group or "",
                          config.MAP_ROW_LIMIT)
    if df.empty:
        return summary, placeholder("No records match these filters."), []

    head = html.Thead(html.Tr([
        html.Th("", style={"width": "26px"}),
        html.Th("FIGI", style={"width": "140px"}),
        html.Th("Name"),
        html.Th("Bloomberg", style={"width": "100px"}),
        html.Th("Raw", style={"width": "90px"}),
        html.Th("SEDOL", style={"width": "85px"}),
        html.Th("Yahoo ID", style={"width": "95px"}),
        html.Th("Group FIGI", style={"width": "130px"}),
        html.Th("Max wt", style={"width": "72px"}),
    ]))

    rows = []
    for i, r in enumerate(df.itertuples()):
        figi = str(r.figi)
        gfigi = _cell(r.group_figi)
        gfigi = figi if gfigi == "-" else gfigi
        is_child = gfigi != figi
        reviewed = bool(r.reviewed)
        yahoo_display = _cell(r.yahoo_id).replace("YF:", "")
        heavy = r.max_weight is not None and str(r.max_weight) != "nan" and r.max_weight >= 1.0

        rows.append(html.Tr(
            id={"type": "etf-m-row", "index": i}, n_clicks=0,
            children=[
                html.Td("✓" if reviewed else "",
                        style={"textAlign": "center", "color": theme.POSITIVE,
                               "fontWeight": 700}),
                html.Td(figi[:18], style={"fontSize": "10.5px",
                                          "color": theme.NEUTRAL, **theme.NUM}),
                html.Td(_cell(r.name), style={"fontWeight": 500, "maxWidth": "230px",
                                              "overflow": "hidden",
                                              "textOverflow": "ellipsis",
                                              "whiteSpace": "nowrap"}),
                html.Td(_cell(r.bloomberg_code), style={"fontSize": "11px",
                                                        "color": theme.SLATE, **theme.NUM}),
                html.Td(_cell(r.raw_ticker), style={"fontSize": "11px",
                                                    "color": theme.SLATE, **theme.NUM}),
                html.Td(_cell(r.sedol), style={"fontSize": "11px",
                                               "color": theme.NEUTRAL, **theme.NUM}),
                html.Td(yahoo_display or "-", style={"fontSize": "11px", **theme.NUM}),
                html.Td(gfigi[:16], style={"fontSize": "10.5px", **theme.NUM,
                                           "color": theme.NEEDLE if is_child
                                                    else theme.NEUTRAL}),
                html.Td(f"{r.max_weight:.2f}%" if heavy or (r.max_weight and str(r.max_weight) != "nan")
                        else "-", className="num",
                        style={"fontWeight": 600 if heavy else 400,
                               "color": theme.TEXT if heavy else theme.NEUTRAL}),
            ],
            style={"cursor": "pointer"},
        ))

    table = html.Div(html.Table([head, html.Tbody(rows)], className="pz"),
                     style={"overflowX": "auto", "maxHeight": "620px",
                            "overflowY": "auto"})
    return summary, table, df["figi"].tolist()


def _pill(value, label, colour):
    return html.Div([
        html.Span(value, style={"fontSize": "19px", "fontWeight": 600,
                                "color": colour, **theme.NUM}),
        html.Span(label, style={"fontSize": "11px", "color": theme.SLATE,
                                "marginLeft": "6px"}),
    ])


@callback(
    Output("etf-m-selected", "data"),
    Input({"type": "etf-m-row", "index": dash.ALL}, "n_clicks"),
    State("etf-m-figis", "data"),
    prevent_initial_call=True,
)
def _select_row(clicks, figis):
    triggered = ctx.triggered_id
    if not triggered or not figis or not any(clicks or []):
        return no_update
    idx = triggered["index"]
    return figis[idx] if idx < len(figis) else no_update


@callback(
    Output("etf-m-edit", "children"),
    Input("etf-m-selected", "data"), Input("etf-m-refresh", "data"),
)
def _edit_panel(figi, _refresh):
    if not figi:
        return html.Div()
    r = repo.map_record(figi)
    if not r:
        return html.Div()

    def field(label, id_, value, placeholder, width="130px"):
        val = "" if value is None or str(value).lower() in ("nan", "none") else str(value)
        return _control(label, _text_input(id_, placeholder), width) if False else html.Div([
            html.Label(label, style={"display": "block", "fontSize": "10px",
                                     "fontWeight": 600, "letterSpacing": "0.06em",
                                     "textTransform": "uppercase",
                                     "color": theme.SLATE, "marginBottom": "4px"}),
            dcc.Input(id=id_, value=val, placeholder=placeholder, debounce=True,
                      style={"width": width, "padding": "6px 9px", "fontSize": "12.5px",
                             "border": f"1px solid {theme.LINE}", "borderRadius": "4px"}),
        ], style={"marginRight": "9px", "marginBottom": "8px"})

    current = str(r["figi"])
    group = "" if (not r["group_figi"] or str(r["group_figi"]) in ("nan", current)) \
            else str(r["group_figi"])
    yahoo = _cell(r["yahoo_id"], False).replace("YF:", "")
    max_w = r.get("max_weight")

    meta = html.Div([
        html.Span("FIGI ", style={"color": theme.NEUTRAL}),
        html.Span(current, style={**theme.NUM, "marginRight": "18px"}),
        html.Span("Type ", style={"color": theme.NEUTRAL}),
        html.Span(_cell(r["security_type"]), style={"marginRight": "18px"}),
        html.Span("Max weight ", style={"color": theme.NEUTRAL}),
        html.Span(f"{max_w:.2f}%" if max_w and str(max_w) != "nan" else "-",
                  style={**theme.NUM, "fontWeight": 600}),
    ], style={"fontSize": "11.5px", "color": theme.SLATE, "marginBottom": "12px"})

    return html.Div([
        html.Div("Edit mapping", style=theme.CARD_TITLE),
        meta,
        html.Div([
            field("FIGI", "etf-m-f-figi",
                  "" if current.startswith("UNRESOLVED") else current,
                  "BBG000BP5H35", "150px"),
            field("Name", "etf-m-f-name", r["name"], "Company name", "185px"),
            field("Bloomberg", "etf-m-f-bbg", r["bloomberg_code"], "BA. LN", "110px"),
            field("Raw ticker", "etf-m-f-raw", r["raw_ticker"], "as in file", "105px"),
            field("SEDOL", "etf-m-f-sedol", r["sedol"], "0263494", "95px"),
            field("ISIN", "etf-m-f-isin", r["isin"], "GB0002634946", "140px"),
            field("Yahoo ID", "etf-m-f-yahoo", yahoo, "BA.L", "110px"),
            field("Group FIGI", "etf-m-f-group", group, "parent, or blank", "150px"),
            field("Notes", "etf-m-f-notes", r["notes"], "optional", "130px"),
            html.Div([
                _button("Save and approve", "etf-m-save", primary=True),
                html.Span(" "),
                _button("No price feed", "etf-m-empty"),
            ], style={"display": "flex", "gap": "8px", "marginBottom": "8px"}),
        ], style={"display": "flex", "alignItems": "flex-end", "flexWrap": "wrap"}),
    ], style={**theme.CARD, "borderLeft": f"3px solid {theme.NEEDLE}",
              "borderRadius": "0 6px 6px 0"})


@callback(
    Output("etf-m-refresh", "data"), Output("etf-m-feedback", "children"),
    Input("etf-m-save", "n_clicks"),
    Input("etf-m-empty", "n_clicks"),
    State("etf-m-selected", "data"), State("etf-m-refresh", "data"),
    State("etf-m-f-figi", "value"), State("etf-m-f-name", "value"),
    State("etf-m-f-bbg", "value"), State("etf-m-f-raw", "value"),
    State("etf-m-f-sedol", "value"), State("etf-m-f-isin", "value"),
    State("etf-m-f-yahoo", "value"), State("etf-m-f-group", "value"),
    State("etf-m-f-notes", "value"),
    prevent_initial_call=True,
)
def _map_actions(save_n, empty_n, selected, refresh,
                 figi, name, bbg, raw, sedol, isin, yahoo, group, notes):
    triggered = ctx.triggered_id
    if not triggered:
        return no_update, no_update

    fields = {"name": name, "bloomberg_code": bbg, "raw_ticker": raw,
              "sedol": sedol, "isin": isin, "notes": notes}
    try:
        if not selected:
            return no_update, _feedback("Select a row first.", ok=False)

        if triggered == "etf-m-empty":
            repo.map_mark_empty(selected, fields, group)
            return refresh + 1, _feedback(
                f"{selected[:20]} marked reviewed with no price feed.")

        if triggered == "etf-m-save":
            result = repo.map_save(selected, fields, yahoo, group, figi)
            label = f"YF:{yahoo}" if yahoo else "no Yahoo ID"
            return refresh + 1, _feedback(f"{label} saved for {result[:20]}.")
    except Exception as exc:                                   # noqa: BLE001
        return no_update, _feedback(str(exc)[:200], ok=False)

    return no_update, no_update


# --- Sources tab ---------------------------------------------------------

def _sources_view():
    return html.Div([
        html.Div(id="etf-s-feedback", style={"marginBottom": "10px"}),
        card("Holdings file sources", html.Div(id="etf-s-table")),
        dcc.Store(id="etf-s-refresh", data=0),
    ])


@callback(Output("etf-s-table", "children"), Input("etf-s-refresh", "data"))
def _sources_table(_refresh):
    df = repo.sources()
    if df.empty:
        return placeholder("No ETFs with holdings data yet.")

    providers = universe.etf_providers()
    names = universe.etf_names()

    head = html.Thead(html.Tr([
        html.Th("ETF", style={"width": "230px"}),
        html.Th("Provider", style={"width": "95px"}),
        html.Th("Last import", style={"width": "100px"}),
        html.Th("Snaps", style={"width": "60px"}),
        html.Th("Download URL"),
        html.Th("", style={"width": "130px"}),
    ]))

    rows = []
    for r in df.itertuples():
        etf_id = r.etf_fund_id
        label = names.get(etf_id, reference.short_name(etf_id))
        has_url = bool(r.url)
        rows.append(html.Tr([
            html.Td(label, style={"fontWeight": 500, "fontSize": "12.5px"}),
            html.Td(providers.get(etf_id, "-"),
                    style={"color": theme.SLATE, "fontSize": "11.5px"}),
            html.Td(r.last_import, className="num",
                    style={"color": theme.SLATE, "fontSize": "11.5px"}),
            html.Td(r.snapshots, className="num", style={"color": theme.NEUTRAL}),
            html.Td(dcc.Input(
                id={"type": "etf-s-url", "index": etf_id}, value=r.url,
                placeholder="paste the provider download link",
                debounce=True,
                style={"width": "100%", "padding": "5px 8px", "fontSize": "11.5px",
                       "border": f"1px solid {theme.LINE}", "borderRadius": "4px",
                       **theme.NUM})),
            html.Td(html.Div([
                html.Button("Save", id={"type": "etf-s-save", "index": etf_id},
                            n_clicks=0,
                            style={"padding": "5px 11px", "fontSize": "11.5px",
                                   "fontWeight": 600, "cursor": "pointer",
                                   "border": f"1px solid {theme.LINE}",
                                   "borderRadius": "4px",
                                   "background": theme.SURFACE}),
                html.A("Open", href=r.url if has_url else "#", target="_blank",
                       style={"marginLeft": "8px", "fontSize": "11.5px",
                              "color": "#2E75B6" if has_url else theme.NEUTRAL,
                              "textDecoration": "none",
                              "pointerEvents": "auto" if has_url else "none"}),
            ], style={"display": "flex", "alignItems": "center"})),
        ]))

    return html.Div(html.Table([head, html.Tbody(rows)], className="pz"),
                    style={"overflowX": "auto"})


@callback(
    Output("etf-s-refresh", "data"), Output("etf-s-feedback", "children"),
    Input({"type": "etf-s-save", "index": dash.ALL}, "n_clicks"),
    State({"type": "etf-s-url", "index": dash.ALL}, "value"),
    State({"type": "etf-s-url", "index": dash.ALL}, "id"),
    State("etf-s-refresh", "data"),
    prevent_initial_call=True,
)
def _save_source(clicks, urls, ids, refresh):
    triggered = ctx.triggered_id
    if not triggered or not any(clicks or []):
        return no_update, no_update

    etf_id = triggered["index"]
    url = next((u for u, i in zip(urls, ids) if i["index"] == etf_id), None)
    try:
        repo.set_source(etf_id, (url or "").strip())
        label = reference.short_name(etf_id)
        return refresh + 1, _feedback(
            f"URL saved for {label}." if url else f"URL cleared for {label}.")
    except Exception as exc:                                   # noqa: BLE001
        return no_update, _feedback(str(exc)[:200], ok=False)


@callback(Output("etf-body", "children"), Input("etf-tabs", "value"))
def _render(tab):
    views = {
        "Holdings": _holdings_view,
        "Changes": _changes_view,
        "Compare": _compare_view,
        "Fund compare": _fund_compare_view,
        "Ticker map": _map_view,
        "Sources": _sources_view,
    }
    intro = TAB_INTRO.get(tab, "")
    body = views[tab]() if tab in views else placeholder(f"{tab} - not migrated yet")
    return html.Div([
        html.P(intro, style=theme.SUBTITLE) if intro else None,
        body,
    ])
