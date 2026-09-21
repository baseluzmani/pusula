"""
pages.pnl_analysis
==================

Portfolio > P&L analysis.

Return on capital employed per instrument, with manual basket aggregation.
All figures come from core.roce and cover the full ledger — this page has no
date filter by design, because a windowed ROCE needs an opening and closing
mark and the unrealised P&L at a window start is not zero.

Rendered as a tab inside pages/portfolio.py, which calls render().
Deliberately does NOT call dash.register_page - it is a sub-tab, not a
top-level page, so it must stay out of dash.page_registry and the top bar.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, State, callback, ctx, dash_table, dcc, html
from dash.dash_table.Format import Format, Group, Scheme

from core.roce import (
    MIN_DAYS_TO_ANNUALISE,
    Position,
    aggregate,
    compute_all,
    load_latest_prices,
    load_trades,
    validate,
)


PREFIX = "basket-"

def get_data() -> dict:
    """Run the engine and return everything the page needs.

    Returns {'trades', 'members', 'rows', 'by_id', 'asof', 'approx',
    'unpriceable', 'n_closed', 'n_unpriced'}.

    Deliberately uncached. There was a module-level dict here, and it was the
    source of a bug that took a while to find: it filled on the first render
    after the process started and never refilled, so this page showed prices
    from whenever it was first opened while the P&L tab, which recomputes per
    render, moved with the market. The two tabs disagreed by whatever sterling
    and the market had done in between, and nothing on screen said so.

    A stamp-based invalidation fixed the common cases but not in-place edits -
    a corrected fx_rate or a hand-patched close changes no row count and no max
    date - so the Refresh button had to stay as an escape hatch, and a button
    you must remember to press is a worse bug than a slow page.

    The whole run is ~130ms: the ledger walk is about 100ms and the rest is two
    indexed queries. That is cheap enough to pay on every callback in exchange
    for never having to wonder whether the number on screen is current.

    If this ever becomes slow enough to matter, cache it properly with
    Flask-Caching and a short TTL rather than reintroducing a dict that nothing
    invalidates.
    """
    members = compute_all()
    _, approx, unpriceable = load_latest_prices()
    return dict(
        trades=load_trades(),
        members=members,
        rows=positions_to_rows(members),
        by_id={p.instrument: p for p in members},
        asof=pd.Timestamp.today().normalize(),
        approx=approx,
        unpriceable=unpriceable,
        n_closed=sum(1 for p in members if p.status == "closed"),
        # Open positions with no mark, or ledgers that failed to walk. These
        # report realised P&L only and are left out of the basket.
        n_unpriced=sum(1 for p in members if not p.priced),
    )


# --------------------------------------------------------------------------
# Table styling, matched to the P&L tab: dark header, compact rows, coloured
# fill on the rate columns and coloured text on the money columns.
# If core.theme exposes these values, prefer them over these literals.
# --------------------------------------------------------------------------

HEADER_BG = "#111827"
POS_TEXT, NEG_TEXT = "#15803d", "#b91c1c"
POS_FILL = ["#f0fdf4", "#dcfce7", "#86efac", "#4ade80"]
NEG_FILL = ["#fef2f2", "#fee2e2", "#fecaca", "#f87171"]

# Rate columns hold fractions (0.059 = 5.9%), so the edges are fractions too.
RATE_EDGES = [0.05, 0.15, 0.30]


def _rate_bands(col: str) -> list[dict]:
    """Graduated green/red fill, strongest at the extremes.

    Edges are fractions because the column holds 0.059 for 5.9%.
    """
    out: list[dict] = []
    bounds = [0.0] + RATE_EDGES
    for i in range(len(RATE_EDGES)):
        lo, hi = bounds[i], bounds[i + 1]
        out.append({
            "if": {"filter_query": f"{{{col}}} >= {lo} && {{{col}}} < {hi}",
                   "column_id": col},
            "backgroundColor": POS_FILL[i],
        })
        # Mirror of the positive band, half-open the other way: (-hi, -lo].
        # Strict "<" at the zero end keeps 0.0 in the positive band only, and
        # inclusive ">=" at the far end stops values landing on an edge
        # (-0.05, -0.15) falling through with no colour at all.
        near = f"{{{col}}} < {-lo}" if lo == 0.0 else f"{{{col}}} <= {-lo}"
        out.append({
            "if": {"filter_query": f"{near} && {{{col}}} > {-hi}",
                   "column_id": col},
            "backgroundColor": NEG_FILL[i],
        })
    top = RATE_EDGES[-1]
    out.append({"if": {"filter_query": f"{{{col}}} >= {top}", "column_id": col},
                "backgroundColor": POS_FILL[-1]})
    out.append({"if": {"filter_query": f"{{{col}}} <= {-top}", "column_id": col},
                "backgroundColor": NEG_FILL[-1]})
    return out


def _money_text(col: str) -> list[dict]:
    return [
        {"if": {"filter_query": f"{{{col}}} > 0", "column_id": col},
         "color": POS_TEXT},
        {"if": {"filter_query": f"{{{col}}} < 0", "column_id": col},
         "color": NEG_TEXT},
    ]


TABLE_STYLE = dict(
    style_as_list_view=False,
    style_table={"overflowX": "auto"},
    style_cell={
        "padding": "4px 10px",
        "fontSize": "12.5px",
        "fontFamily": "system-ui, sans-serif",
        "textAlign": "right",
        "border": "none",
        "borderBottom": "1px solid #f1f5f9",
    },
    style_header={
        "backgroundColor": HEADER_BG,
        "color": "white",
        "fontWeight": 600,
        "fontSize": "12px",
        "border": "none",
        "padding": "8px 10px",
    },
    style_cell_conditional=[
        {"if": {"column_id": "name"}, "textAlign": "left", "minWidth": "230px",
         "fontWeight": 500},
        {"if": {"column_id": "status"}, "textAlign": "left", "width": "70px"},
        {"if": {"column_id": "wac"}, "color": "#9ca3af"},
    ],
    style_data_conditional=(
        _rate_bands("roce_total")
        + _rate_bands("roce_annualised")
        + _money_text("total_pnl")
        + _money_text("realised")
        + _money_text("unrealised")
        + _money_text("fx_pnl")
        + [
            {"if": {"filter_query": "{_short} = 1",
                    "column_id": "roce_annualised"},
             "color": "#c0c4cc", "fontStyle": "italic",
             "backgroundColor": "transparent"},
            {"if": {"filter_query": "{status} = closed"},
             "backgroundColor": "#fafafa"},
            {"if": {"filter_query": "{status} = closed", "column_id": "status"},
             "color": "#9ca3af", "fontStyle": "italic"},
        ]
    ),
)


TABLE_COLUMNS = [
    dict(id="name", name="Instrument", type="text"),
    dict(id="status", name="Status", type="text"),
    dict(id="qty", name="Qty", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="wac", name="Avg cost", type="numeric",
         format=Format(precision=2, scheme=Scheme.fixed)),
    dict(id="days_deployed", name="Days deployed", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="avg_capital", name="Avg capital £", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="peak_capital", name="Peak capital £", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="realised", name="Realised £", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="unrealised", name="Unrealised £", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="fx_pnl", name="of which FX £", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="total_pnl", name="Total P&L £", type="numeric",
         format=Format(precision=0, scheme=Scheme.fixed, group=Group.yes)),
    dict(id="roce_total", name="ROCE", type="numeric",
         format=Format(precision=1, scheme=Scheme.percentage)),
    dict(id="roce_annualised", name="Annualised", type="numeric",
         format=Format(precision=1, scheme=Scheme.percentage)),
]


def positions_to_rows(members: list[Position]) -> list[dict]:
    rows = []
    for p in members:
        rows.append({
            "id": p.instrument,
            "name": p.name,
            "status": p.status,
            "qty": p.qty,
            "wac": p.wac,
            "days_deployed": p.days_deployed,
            "avg_capital": p.avg_capital,
            "peak_capital": p.peak_capital,
            "realised": p.realised,
            "unrealised": p.unrealised,
            "fx_pnl": p.fx_pnl,
            "total_pnl": p.total_pnl,
            "roce_total": p.roce_total,
            "roce_annualised": p.roce_annualised,
            "_short": p.days_deployed < MIN_DAYS_TO_ANNUALISE,
        })
    return rows


def metric(label: str, value: str, sub: str = "") -> html.Div:
    return html.Div(
        [
            html.Div(label, style={"fontSize": "12px", "color": "#6b7280"}),
            html.Div(value, style={"fontSize": "22px", "fontWeight": 600}),
            html.Div(sub, style={"fontSize": "11px", "color": "#9ca3af"}),
        ],
        style={"minWidth": "150px"},
    )


def _render_inner() -> html.Div:
    """Tab body. Called by pages/portfolio.py via the TABS map."""
    data = get_data()
    return html.Div(
        style={"fontFamily": "system-ui, sans-serif"},
        children=[
            html.P(
                "Return on capital employed per instrument. Tick rows to pool them "
                "into a basket — capital-days and P&L are summed, then the rate is "
                "computed once. Figures cover the full ledger and ignore any date "
                "filter set elsewhere.",
                style={"color": "#6b7280", "maxWidth": "70ch"},
            ),

            html.Div([
                dcc.Input(
                    id=PREFIX + "search", type="text",
                    placeholder="Search instruments",
                    style={"width": "300px", "padding": "8px"},
                ),
                html.Button(id=PREFIX + "toggle-closed", n_clicks=0,
                            children=f"Show closed ({data['n_closed']})",
                            style={"marginLeft": "10px", "padding": "8px 12px"}),
                html.Button("Clear selection", id=PREFIX + "clear", n_clicks=0,
                            style={"marginLeft": "10px", "padding": "8px 12px"}),
                html.Span(id=PREFIX + "asof",
                          children=f"as of {data['asof']:%d %b %Y}",
                          style={"marginLeft": "14px", "fontSize": "12px",
                                 "color": "#9ca3af"}),
            ], style={"marginBottom": "12px"}),

            html.Div(
                f"FX note: {len(data['approx'])} non-GBP instruments are marked "
                "using the rate from their most recent trade, not a live rate. "
                "GBP-quoted instruments are exact."
                if data["approx"] else "",
                style={"fontSize": "12px", "color": "#92400e",
                       "background": "#fffbeb", "padding": "8px",
                       "borderRadius": "4px", "marginBottom": "10px"}
                if data["approx"] else {"display": "none"},
            ),

            # Selection lives here, not in the table, so it survives filtering.
            dcc.Store(id=PREFIX + "selected", data=[]),
            dcc.Store(id=PREFIX + "version", data=0),

            dash_table.DataTable(
                id=PREFIX + "table",
                columns=TABLE_COLUMNS,
                data=data["rows"],
                row_selectable="multi",
                selected_row_ids=[],
                sort_action="native",
                page_size=50,
                **TABLE_STYLE,
            ),

            html.Div(id=PREFIX + "summary", style={
                "display": "flex", "gap": "28px", "flexWrap": "wrap",
                "margin": "28px 0 8px 0", "padding": "16px",
                "background": "#f9fafb", "borderRadius": "6px",
            }),
            html.Div(id=PREFIX + "note",
                     style={"fontSize": "12px", "color": "#6b7280"}),

            dcc.Graph(id=PREFIX + "exposure"),

            html.Details([
                html.Summary("Validation",
                             style={"cursor": "pointer", "fontWeight": 600}),
                html.Div(id=PREFIX + "validation",
                         style={"fontSize": "13px", "marginTop": "8px"}),
            ], style={"marginTop": "16px"}),
        ],
    )


def render() -> html.Div:
    """Wrapper so a data problem shows inside the tab instead of taking the
    gunicorn worker down and blacking out all of Pusula on 8060."""
    try:
        return _render_inner()
    except Exception as exc:                      # noqa: BLE001 - deliberate
        import traceback
        return html.Div(
            style={"padding": "16px", "fontFamily": "system-ui, sans-serif"},
            children=[
                html.H3("P&L analysis"),
                html.P("This tab could not load. The rest of Pusula is unaffected.",
                       style={"color": "#b91c1c", "fontWeight": 600}),
                html.Pre(
                    f"{type(exc).__name__}: {exc}\n\n{traceback.format_exc()}",
                    style={"background": "#fef2f2", "padding": "12px",
                           "fontSize": "12px", "whiteSpace": "pre-wrap",
                           "borderRadius": "4px", "overflowX": "auto"},
                ),
            ],
        )


# --------------------------------------------------------------------------
# Callbacks. @callback (not @app.callback) because use_pages imports this
# module while Dash(...) is still being constructed, so no app object exists
# yet. app.py already sets suppress_callback_exceptions=True, which these need
# since the ids are absent from the DOM while another page is showing.
# --------------------------------------------------------------------------


@callback(
    Output(PREFIX + "table", "data"),
    Output(PREFIX + "toggle-closed", "children"),
    Input(PREFIX + "search", "value"),
    Input(PREFIX + "toggle-closed", "n_clicks"),
    Input(PREFIX + "version", "data"),
)
def filter_rows(term, n_clicks, _version):
    """Open positions only by default; the button reveals closed ones.

    Hiding a closed row does not deselect it - track_selection keeps picks that
    are currently out of view, so a basket can mix open and closed positions.
    """
    data = get_data()
    show_closed = bool((n_clicks or 0) % 2)

    rows = data["rows"]
    if not show_closed:
        rows = [r for r in rows if r["status"] == "open"]
    if term:
        t = term.lower()
        rows = [r for r in rows if t in r["name"].lower() or t in r["id"].lower()]

    label = "Hide closed" if show_closed else f"Show closed ({data['n_closed']})"
    return rows, label


@callback(
    Output(PREFIX + "selected", "data"),
    Input(PREFIX + "table", "selected_row_ids"),
    Input(PREFIX + "clear", "n_clicks"),
    State(PREFIX + "table", "data"),
    State(PREFIX + "selected", "data"),
    prevent_initial_call=True,
)
def track_selection(selected_row_ids, _clear, visible, stored):
    """Merge the table's selection with rows currently hidden by the search.

    DataTable only reports selections among rows it can see, so ticking one
    instrument, searching for another and ticking that would otherwise drop
    the first. Keep hidden picks, replace visible ones.
    """
    if ctx.triggered_id == PREFIX + "clear":
        return []
    visible_ids = {r["id"] for r in (visible or [])}
    kept = [i for i in (stored or []) if i not in visible_ids]
    return kept + list(selected_row_ids or [])


@callback(
    Output(PREFIX + "table", "selected_row_ids"),
    Input(PREFIX + "selected", "data"),
    State(PREFIX + "table", "data"),
)
def reflect_selection(stored, visible):
    visible_ids = {r["id"] for r in (visible or [])}
    return [i for i in (stored or []) if i in visible_ids]


@callback(
    Output(PREFIX + "summary", "children"),
    Output(PREFIX + "note", "children"),
    Output(PREFIX + "exposure", "figure"),
    Output(PREFIX + "validation", "children"),
    Input(PREFIX + "selected", "data"),
    Input(PREFIX + "version", "data"),
)
def update_basket(selected, _version):
    data = get_data()
    by_id = data["by_id"]
    chosen = [by_id[i] for i in (selected or []) if i in by_id]

    if not chosen:
        empty = go.Figure()
        empty.update_layout(
            template="simple_white", height=420,
            annotations=[dict(text="Tick instruments above to build a basket",
                              showarrow=False,
                              font=dict(size=14, color="#9ca3af"))],
            xaxis=dict(visible=False), yaxis=dict(visible=False),
        )
        return [], "", empty, ""

    b = aggregate(chosen)
    can_annualise = b.days_deployed >= MIN_DAYS_TO_ANNUALISE

    cards = [
        metric("Instruments", f"{len(chosen)}"),
        metric("Total P&L", f"£{b.total_pnl:,.0f}",
               f"realised £{b.realised:,.0f} / unrealised £{b.unrealised:,.0f}"),
        metric("Avg capital", f"£{b.avg_capital:,.0f}",
               f"peak £{b.peak_capital:,.0f}"),
        metric("Days deployed", f"{b.days_deployed:,}",
               "union, flat days excluded"),
        metric("ROCE", f"{b.roce_total:.1%}", "over the period"),
        metric("Annualised",
               f"{b.roce_annualised:.1%}" if can_annualise else "—",
               "" if can_annualise else f"under {MIN_DAYS_TO_ANNUALISE} days"),
    ]

    contrib = sorted(chosen, key=lambda p: p.capital_days, reverse=True)
    note = "Contribution to annualised rate:  " + "   ·   ".join(
        f"{p.name}: {p.capital_days / b.capital_days:.0%} of capital-days, "
        f"{p.roce_annualised:+.1%}"
        for p in contrib
    )

    fig = go.Figure()
    for p in contrib:
        s = p.daily_book.reindex(b.daily_book.index).fillna(0.0)
        fig.add_trace(go.Scatter(
            x=s.index, y=s.values, name=p.name, mode="lines",
            line=dict(width=0.5), stackgroup="one",
            hovertemplate="%{y:,.0f}<extra>%{fullData.name}</extra>",
        ))
    fig.add_hline(y=b.avg_capital, line_dash="dash", line_color="#374151",
                  annotation_text=f"average £{b.avg_capital:,.0f}",
                  annotation_position="top left")
    fig.add_hline(y=b.peak_capital, line_dash="dot", line_color="#9ca3af",
                  annotation_text=f"peak £{b.peak_capital:,.0f}",
                  annotation_position="bottom left")
    fig.update_layout(
        template="simple_white", height=440, hovermode="x unified",
        title="Capital employed (book cost)", yaxis_title="£",
        margin=dict(t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=-0.25),
    )

    checks = validate(chosen, b, data["trades"])
    val = html.Ul([
        html.Li([
            html.Span("pass " if ok else "FAIL ",
                      style={"color": "#15803d" if ok else "#b91c1c",
                             "fontWeight": 600}),
            f"{label} — {detail}",
        ])
        for label, ok, detail in checks
    ])

    return cards, note, fig, val