import dash
from dash import html, dcc, callback, Input, Output, State, ctx, no_update

from core import theme, config, db
from importers import registry, jobs
from ui.layout import page_header, subtabs, card, data_table
from pages import data_accounts, data_instruments

dash.register_page(__name__, path="/data", name="Data", order=5)

TABS = ["Importers", "Run history", "Database", "Accounts", "Instruments"]

layout = html.Div([
    subtabs("dt-tabs", TABS),
    html.Div([
        page_header("Data",
                    "Every loader in one place: what it feeds, when it last ran, "
                    "and how to run it now."),
        html.Div(id="dt-body"),
    ], style=theme.PAGE),
])


# --- Importers tab -------------------------------------------------------

def _status_pill(status):
    colour = {"ok": theme.POSITIVE, "failed": theme.NEGATIVE,
              "running": theme.NEEDLE}.get(status, theme.NEUTRAL)
    label = status or "never run"
    return html.Span(label, style={
        "color": colour, "fontSize": "11px", "fontWeight": 600,
        "letterSpacing": "0.06em", "textTransform": "uppercase",
    })


def _importer_row(imp, last):
    mode_colour = theme.SLATE if imp.mode == "auto" else theme.NEEDLE
    return html.Div([
        html.Div([
            html.Div([
                html.Span(imp.label, style={"fontWeight": 600, "fontSize": "14px"}),
                html.Span(imp.mode.upper(), style={
                    "marginLeft": "10px", "fontSize": "10px", "fontWeight": 600,
                    "letterSpacing": "0.08em", "color": mode_colour,
                    "border": f"1px solid {mode_colour}", "borderRadius": "3px",
                    "padding": "1px 5px",
                }),
            ]),
            html.Div(imp.description, style={
                "color": theme.SLATE, "fontSize": "12.5px", "marginTop": "3px"}),
            html.Div([
                html.Span(f"Source: {imp.source or '—'}"),
                html.Span(" · "),
                html.Span(f"Writes: {', '.join(imp.target_tables)}"),
                html.Span(f" · {imp.schedule}" if imp.schedule else ""),
            ], style={"color": theme.NEUTRAL, "fontSize": "11.5px",
                      "marginTop": "5px", **theme.NUM}),
        ], style={"flex": "1", "minWidth": "0"}),

        html.Div([
            _status_pill(last.get("status")),
            html.Div(last.get("started_at") or "—", style={
                "fontSize": "11.5px", "color": theme.SLATE,
                "marginTop": "3px", **theme.NUM}),
            html.Div(f"{last.get('rows_affected') or 0:,} rows", style={
                "fontSize": "11.5px", "color": theme.NEUTRAL, **theme.NUM}),
        ], style={"textAlign": "right", "width": "150px"}),

        html.Button("Run now", id={"type": "run-importer", "id": imp.id},
                    n_clicks=0, style={
                        "marginLeft": "16px", "padding": "7px 14px",
                        "border": f"1px solid {theme.LINE}", "borderRadius": "3px",
                        "background": theme.SURFACE, "cursor": "pointer",
                        "fontSize": "12.5px", "fontWeight": 500,
                        "color": theme.INK, "whiteSpace": "nowrap"}),
    ], style={
        "display": "flex", "alignItems": "flex-start", "gap": "12px",
        "padding": "16px 0", "borderBottom": f"1px solid {theme.LINE}",
    })


def _importers_view():
    last = jobs.last_runs().set_index("importer_id").to_dict("index")
    rows = [_importer_row(i, last.get(i.id, {})) for i in registry.REGISTRY]
    return html.Div([
        html.Div(id="dt-run-result"),
        card("Importers", html.Div(rows)),
    ])


# --- Database tab --------------------------------------------------------

def _database_view():
    tables = db.list_tables()
    counts = []
    for name in tables["name"]:
        try:
            counts.append(db.row_count(name))
        except Exception:
            counts.append(None)
    tables["rows"] = counts
    tables = tables[["name", "rows", "index_count"]]
    tables.columns = ["Table", "Rows", "Indexes"]
    tables["Rows"] = tables["Rows"].map(
        lambda v: f"{v:,}" if v is not None else "—")

    return html.Div([
        card("Connection", html.Div([
            html.Div(str(config.DB_PATH), style={"fontSize": "12.5px", **theme.NUM}),
            html.Div("Development copy — the live dashboards use their own file.",
                     style={"color": theme.SLATE, "fontSize": "12px",
                            "marginTop": "4px"}),
        ])),
        card(f"Tables ({len(tables)})",
             data_table(tables, numeric_cols=["Rows", "Indexes"])),
    ])


# --- Routing -------------------------------------------------------------

@callback(Output("dt-body", "children"), Input("dt-tabs", "value"))
def render(tab):
    if tab == "Importers":
        return _importers_view()
    if tab == "Accounts":
        return data_accounts.render()
    if tab == "Instruments":
        return data_instruments.render()
    if tab == "Run history":
        hist = jobs.history()
        if hist.empty:
            return card("Run history", html.Div(
                "No importer has run yet.",
                style={"color": theme.SLATE, "fontSize": "13px"}))
        hist.columns = ["Importer", "Started", "Status", "Rows", "Message"]
        return card("Recent runs",
                    data_table(hist, numeric_cols=["Started", "Rows"]))
    return _database_view()


@callback(
    Output("dt-run-result", "children"),
    Input({"type": "run-importer", "id": dash.ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def run_importer(_clicks):
    triggered = ctx.triggered_id          # NOT prop_id.split('.') — ids contain dots
    if not triggered or not any(_clicks):
        return no_update

    imp = registry.by_id(triggered["id"])
    status, message = jobs.run_importer(imp)
    colour = theme.POSITIVE if status == "ok" else theme.NEGATIVE
    return html.Div(f"{imp.label}: {message}", style={
        "padding": "10px 14px", "marginBottom": "12px", "borderRadius": "3px",
        "fontSize": "13px", "color": colour,
        "border": f"1px solid {colour}33", "background": f"{colour}0D",
    })
