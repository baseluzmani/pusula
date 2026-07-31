"""
Data -> Config.

Settings you might retune. Paths, ports and the database location stay in
core/config.py, because editing those from a form served by the app is a good
way to lose the app.

Values are stored as text and cast on read, so an edit that will not cast
falls back to the default rather than breaking the page that reads it.
"""

from dash import html, dash_table, callback, Input, Output, State

from core import theme, config
from core.repo import settings as repo
from ui.layout import card

BTN = {"padding": "7px 14px", "border": f"1px solid {theme.LINE}",
       "borderRadius": "3px", "background": theme.SURFACE, "cursor": "pointer",
       "fontSize": "12.5px", "fontWeight": 500, "color": theme.INK}


def render():
    df = repo.all_settings()
    return html.Div([
        html.Div(id="cfg-msg"),

        card("Settings", html.Div([
            dash_table.DataTable(
                id="cfg-table",
                columns=[
                    {"name": "Key", "id": "key", "editable": False},
                    {"name": "Value", "id": "value"},
                    {"name": "Type", "id": "value_type", "editable": False},
                    {"name": "Description", "id": "description",
                     "editable": False},
                ],
                data=df.to_dict("records"),
                editable=True,
                style_table={"overflowX": "auto"},
                style_cell={"fontSize": "12.5px", "padding": "7px 10px",
                            "fontFamily": "inherit", "textAlign": "left"},
                style_cell_conditional=[
                    {"if": {"column_id": "description"},
                     "color": theme.SLATE, "fontSize": "12px"},
                ],
                style_header={"fontWeight": 600, "fontSize": "11.5px",
                              "textTransform": "uppercase",
                              "letterSpacing": "0.06em"},
            ),
            html.Div([
                html.Button("Save settings", id="cfg-save", n_clicks=0,
                            style=BTN),
            ], style={"marginTop": "12px"}),
        ])),

        card("Fixed in code", html.Div([
            html.Div("These describe where the machine keeps things. They are "
                     "read from core/config.py and are not editable here.",
                     style={"color": theme.SLATE, "fontSize": "12.5px",
                            "marginBottom": "10px"}),
            html.Div([
                html.Div(f"DB_PATH — {config.DB_PATH}"),
                html.Div(f"INBOX_DIR — {config.INBOX_DIR}"),
                html.Div(f"SCRIPTS_DIR — {config.SCRIPTS_DIR}"),
                html.Div(f"PORT — {config.PORT}"),
            ], style={"fontSize": "12.5px", "lineHeight": "1.8", **theme.NUM}),
        ])),
    ])


@callback(
    Output("cfg-msg", "children"),
    Output("cfg-table", "data"),
    Input("cfg-save", "n_clicks"),
    State("cfg-table", "data"),
    prevent_initial_call=True,
)
def save(_n, data):
    bad = []
    for r in data or []:
        key, value, vtype = r.get("key"), r.get("value"), r.get("value_type")
        if vtype == "int":
            try:
                int(value)
            except (TypeError, ValueError):
                bad.append(key)
                continue
        elif vtype == "float":
            try:
                float(value)
            except (TypeError, ValueError):
                bad.append(key)
                continue
        repo.set_value(key, value)

    df = repo.all_settings()
    if bad:
        colour = theme.NEGATIVE
        text = ("Saved, but these would not cast and were skipped: "
                + ", ".join(bad))
    else:
        colour = theme.POSITIVE
        text = f"Saved {len(data or [])} settings."

    return html.Div(text, style={
        "padding": "10px 14px", "marginBottom": "12px", "borderRadius": "3px",
        "fontSize": "13px", "color": colour,
        "border": f"1px solid {colour}33", "background": f"{colour}0D",
    }), df.to_dict("records")
