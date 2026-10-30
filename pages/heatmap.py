"""
Heatmap tab.

Merges the old Markets Heat Map and Holdings Heat Map into one page: the
shared universe selector chooses the instrument set (so "Holdings Heat Map"
is just this page with the My-holdings radio), and a sort dropdown reorders
rows by any period. A Group-by-Type toggle sections the grid by asset_type.

Fast because it renders straight from repo.period_returns (one grouped pass)
rather than the original per-cell recompute that made both pages crawl.

Below the grid, a day-move map: one tile per held position in the universe,
sized by its value in GBP and coloured by its latest daily move (the 1D
column; funds not yet priced today show their last move, dated on the tile).
Group by type nests the tiles under their asset type. The sums are in
core/daymove.py.
"""

import dash
from dash import html, dcc, callback, Input, Output
import pandas as pd
import plotly.graph_objects as go

from core import theme, config
from core import daymove as dm
from core import finance as fin
from core import valuation as val
from core.repo import market as repo
from core.repo import portfolio as pf
from core.repo import settings
from ui import universe

# Columns shown in the grid, in order.
HEAT_COLS = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "Since"]

# Day-move map: full colour at +-TILE_CAP percent; prices loaded this many
# days back to find each holding's latest move.
TILE_CAP = 3.0
MOVE_WINDOW_DAYS = 30


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Heatmap", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Returns across periods for the selected universe. "
                         "Green is up, red is down.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Div([
                    html.Label("Sort by", style={"fontSize": "11px",
                               "color": theme.SLATE, "marginRight": "6px"}),
                    dcc.Dropdown(id="hm-sort", clearable=False,
                                 value="YTD", style={"width": "110px"},
                                 options=[{"label": c, "value": c}
                                          for c in HEAT_COLS]),
                ], style={"display": "flex", "alignItems": "center",
                          "marginRight": "18px"}),
                dcc.Checklist(id="hm-group",
                              options=[{"label": " Group by type", "value": "on"}],
                              value=["on"],
                              inputStyle={"marginRight": "6px"},
                              labelStyle={"fontSize": "12px",
                                          "color": theme.TEXT}),
                html.Div([
                    html.Label("Since", style={"fontSize": "11px",
                               "color": theme.SLATE, "margin": "0 6px 0 18px"}),
                    dcc.DatePickerSingle(id="hm-since",
                                         date=settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01"),
                                         display_format="DD MMM YYYY"),
                ], style={"display": "flex", "alignItems": "center"}),
            ], style={"display": "flex", "alignItems": "center",
                      "flexWrap": "wrap"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "14px"}),

        html.Div(html.Div(id="hm-grid"),
                 style={**theme.CARD, "overflow": "auto",
                        "maxHeight": "calc(100vh - 190px)"}),

        html.Div(id="hm-tree"),
    ])


@callback(
    Output("hm-grid", "children"),
    Input(universe.STORE_ID, "data"),
    Input("hm-sort", "value"),
    Input("hm-group", "value"),
    Input("hm-since", "date"),
)
def _grid(store, sort_col, group, since):
    since = since or settings.get("MARKETS_SINCE_DEFAULT", "2026-03-01")
    sort_col = sort_col if sort_col in HEAT_COLS else "YTD"
    grouped = "on" in (group or [])
    if not universe.is_chosen(store):
        return _empty(universe.PROMPT)
    ids = universe.resolve_ids(store)
    if not ids:
        return _empty("No instruments in this universe.")

    table = repo.period_returns(fund_ids=ids, since_date=since)
    if table.empty:
        return _empty("No returns to show.")

    since_label = pd.Timestamp(since).strftime("%d %b %y")
    header = _header_row(since_label, sort_col)

    if grouped:
        blocks = []
        # Order type groups by their best value in the sort column, so the
        # strongest sector floats to the top too.
        order = (table.groupby("Type")[sort_col].max()
                 .sort_values(ascending=False).index.tolist())
        for atype in order:
            grp = table[table["Type"] == atype]
            grp = grp.sort_values(sort_col, ascending=False, na_position="last")
            blocks.append(html.Div([
                html.Div(str(atype).upper(), style={
                    "fontSize": "9px", "fontWeight": 700,
                    "letterSpacing": "0.06em", "color": theme.SLATE,
                    "padding": "10px 0 4px"}),
                html.Table([header, html.Tbody([_row(r, sort_col)
                            for r in grp.to_dict("records")])],
                    style={"width": "100%", "borderCollapse": "collapse"}),
            ]))
        return html.Div(blocks)

    table = table.sort_values(sort_col, ascending=False, na_position="last")
    return html.Table([header, html.Tbody([_row(r, sort_col)
                       for r in table.to_dict("records")])],
                      style={"width": "100%", "borderCollapse": "collapse"})


# --- day-move map -------------------------------------------------------------

@callback(
    Output("hm-tree", "children"),
    Input(universe.STORE_ID, "data"),
    Input("hm-group", "value"),
)
def _tree(store, group):
    if not universe.is_chosen(store):
        return None
    ids = set(universe.resolve_ids(store))
    held = repo.open_positions()
    if not held.empty:
        held = held[held["fund_id"].isin(ids)
                    & ~held["asset_type"].isin(repo.EXCLUDED_TYPES)]
    if held.empty:
        return _tree_card(_empty("No held positions in this universe - the "
                                 "map shows holdings only."))

    instruments = pf.instruments()
    latest = pf.latest_prices()
    rates = fin.fx_rates(latest)
    valued = val.value_holdings(held[["fund_id", "units"]], instruments,
                                fin.latest_price_map(latest), rates["USD"],
                                rates)
    tiles = dm.leaves(valued, None)
    if not tiles.empty:
        since = (pd.Timestamp.now().normalize()
                 - pd.Timedelta(days=MOVE_WINDOW_DAYS)).strftime("%Y-%m-%d")
        px = repo.prices(list(tiles["fund_id"]), min_date=since)
        tiles = dm.leaves(valued, dm.latest_moves(px))
    if tiles.empty:
        return _tree_card(_empty("No priced holdings to map."))

    total = dm.summary(tiles)
    head = html.Div([
        html.Div("Latest day", style={**theme.CARD_TITLE,
                                      "marginBottom": "0"}),
        html.Div([
            html.Span(f"{total['move']:+.2f}%", style={
                **theme.NUM, "fontWeight": 700,
                "color": theme.colour_for(round(total["move"], 2))}),
            html.Span(f"  {_signed_money(total['change'])}", style={
                **theme.NUM, "color": theme.SLATE}),
            html.Span(f"  \u00b7  {len(tiles)} holdings, "
                      f"{theme.money(total['value'])}",
                      style={"color": theme.SLATE}),
        ], style={"fontSize": "12px"}),
    ], style={"display": "flex", "justifyContent": "space-between",
              "alignItems": "baseline", "marginBottom": "8px"})
    note = html.Div(
        "Tile size = holding value in \u00a3 \u00b7 colour = latest daily "
        f"move, full colour at \u00b1{TILE_CAP:g}% \u00b7 funds not yet "
        "priced today show their last move, dated on the tile \u00b7 the "
        "total is the value-weighted move of those latest moves",
        style={"fontSize": "10px", "color": theme.NEUTRAL, "marginTop": "6px"})
    graph = dcc.Graph(figure=_tree_figure(tiles, "on" in (group or [])),
                      config={"displayModeBar": False},
                      style={"height": "460px"})
    return _tree_card(html.Div([head, graph, note]))


def _tree_card(child):
    return html.Div(child, style=theme.CARD)


def _tree_figure(tiles, grouped):
    """Plotly treemap: leaves are holdings; with ``grouped`` they sit under
    one parent per asset type, coloured by the type's weighted move."""
    ids, labels, parents, values = [], [], [], []
    colours, text, hover, sizes = [], [], [], []
    share_max = float(tiles["weight"].max()) or 1.0

    if grouped:
        for g in dm.groups(tiles).to_dict("records"):
            # Plotly draws a parent's label (not its text) in the header
            # strip, so the type's move goes into the label.
            ids.append(f"type:{g['asset_type']}")
            labels.append(f"{g['asset_type']}  {g['move']:+.2f}%")
            parents.append("")
            values.append(g["value"])
            colours.append(theme.tile_rgb(g["move"], TILE_CAP))
            text.append(f"<b>{g['asset_type']}</b>  {g['move']:+.2f}%")
            hover.append(f"<b>{g['asset_type']}</b><br>{g['move']:+.2f}% "
                         f"\u00b7 {_signed_money(g['change'])}<br>"
                         f"{theme.money(g['value'])}")
            sizes.append(11)

    for t in tiles.to_dict("records"):
        ids.append(t["fund_id"])
        labels.append(t["label"])
        parents.append(f"type:{t['asset_type']}" if grouped else "")
        values.append(t["value"])
        colours.append(theme.tile_rgb(t["move"], TILE_CAP))
        when = (pd.Timestamp(t["move_date"]).strftime("%d %b")
                if t["move_date"] is not None and pd.notna(t["move_date"])
                else None)
        # Bigger tiles get bigger type, like a market map; Plotly still
        # shrinks anything that does not fit.
        size = round(10 + 12 * (t["weight"] / share_max) ** 0.5)
        line = f"<b>{t['label']}</b><br>{t['move']:+.2f}%"
        if t["stale"]:
            small = max(9, round(size * 0.7))
            line += (f"<br><span style='font-size:{small}px'>"
                     f"<i>{when or 'no move'}</i></span>")
        text.append(line)
        day = (f"{t['move']:+.2f}% on {when}"
               + (" (latest available)" if t["stale"] else "")
               if when else "no move in the last "
                            f"{dm.STALE_DAYS} days")
        hover.append(f"<b>{t['name']}</b><br>{day}<br>"
                     f"{theme.money(t['value'])} \u00b7 {t['weight']:.1f}% of "
                     f"the map<br>Day change {_signed_money(t['change'])}")
        sizes.append(size)

    fig = go.Figure(go.Treemap(
        ids=ids, labels=labels, parents=parents, values=values,
        branchvalues="total", sort=True,
        marker=dict(colors=colours, line=dict(color=theme.SURFACE, width=2),
                    pad=dict(t=20 if grouped else 0, l=2, r=2, b=2)),
        text=text, texttemplate="%{text}", textposition="middle center",
        textfont=dict(color="#FFFFFF", size=sizes, family=theme.FONT_UI),
        customdata=hover,
        hovertemplate="%{customdata}<extra></extra>",
        pathbar=dict(visible=grouped, thickness=18),
        tiling=dict(packing="squarify"),
    ))
    fig.update_layout(height=460, margin=dict(l=0, r=0, t=0, b=0),
                      paper_bgcolor=theme.SURFACE,
                      font=dict(family=theme.FONT_UI),
                      hoverlabel=dict(font_size=12,
                                      font_family=theme.FONT_UI))
    return fig


def _signed_money(v):
    """+£1,234 / -£567 for a day change."""
    return f"{'+' if v >= 0 else '-'}\u00a3{abs(v):,.0f}"


def _header_row(since_label, sort_col):
    cells = [html.Th("Instrument", style=_th("left"))]
    for c in HEAT_COLS:
        label = since_label if c == "Since" else c
        active = c == sort_col
        cells.append(html.Th(
            (label + (" \u25BC" if active else "")),
            style={**_th("right"),
                   "background": theme.NEEDLE if active else theme.INK}))
    return html.Thead(html.Tr(cells))


def _th(align):
    return {"background": theme.INK, "color": "#fff", "padding": "6px 8px",
            "fontSize": "10px", "fontWeight": 600, "textAlign": align,
            "whiteSpace": "nowrap", "position": "sticky", "top": 0,
            "zIndex": 1}


def _row(r, sort_col):
    cells = [html.Td(html.Span(_short(r["Fund"]), title=str(r["Fund"])),
                     style={"padding": "4px 8px", "fontSize": "11px",
                            "fontWeight": 500, "color": theme.INK,
                            "whiteSpace": "nowrap", "maxWidth": "230px",
                            "overflow": "hidden", "textOverflow": "ellipsis",
                            "position": "sticky", "left": 0,
                            "background": "white"})]
    for c in HEAT_COLS:
        val = r.get(c)
        has = pd.notna(val)
        emphasise = c == sort_col
        cells.append(html.Td(
            f"{val:+.1f}%" if has else "\u2014",
            style={"padding": "4px 8px", "fontSize": "10.5px",
                   "textAlign": "right", "fontWeight": 700 if emphasise else 600,
                   **theme.NUM, "color": theme.INK,
                   "background": theme.heat_rgb(val if has else None),
                   "outline": (f"2px solid {theme.NEEDLE}" if emphasise and has
                               else "none"),
                   "outlineOffset": "-2px"}))
    return html.Tr(cells, style={"borderBottom": f"1px solid {theme.LINE}"})


def _short(name, n=32):
    s = str(name)
    return s if len(s) <= n else s[:n - 1] + "\u2026"


def _empty(msg):
    return html.Div(msg, style={"color": theme.NEUTRAL, "fontSize": "12px",
                                "padding": "18px"})
