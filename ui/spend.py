"""
Building blocks shared by the Spending sub-tabs: table styling, buttons, the
filter-bar field, and the "assign" bar (category, sub-category, remember)
used by Import, Manual Queue and Transactions.
"""

from __future__ import annotations

from dash import html, dcc, callback, Input, Output, State

from core import theme
from core.repo import spending as repo

LABEL = {"fontSize": "10px", "fontWeight": 600, "letterSpacing": "0.06em",
         "textTransform": "uppercase", "color": theme.SLATE,
         "marginBottom": "4px"}

INPUT = {"padding": "6px 9px", "fontSize": "12.5px", "borderRadius": "5px",
         "border": f"1px solid {theme.LINE}"}

NOTE = {"fontSize": "11.5px", "color": theme.SLATE}

# DataTable styling: dark header like the other Pusula grids, numbers in the
# tabular mono face, right-aligned.
TABLE = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": theme.INK, "color": theme.SURFACE,
                  "fontWeight": 600, "fontSize": "10.5px",
                  "letterSpacing": "0.05em", "textTransform": "uppercase",
                  "border": "none", "textAlign": "left"},
    style_cell={"padding": "5px 9px", "fontSize": "12px",
                "fontFamily": theme.FONT_UI, "textAlign": "left",
                "color": theme.TEXT, "border": "none",
                "borderBottom": f"1px solid {theme.LINE_SOFT}",
                "whiteSpace": "nowrap", "overflow": "hidden",
                "textOverflow": "ellipsis", "maxWidth": "360px"},
    css=[{"selector": ".dash-spreadsheet tr:hover td",
          "rule": f"background-color: {theme.CANVAS} !important;"}],
)


def money_cells(*cols):
    """style_cell_conditional entries for right-aligned money columns."""
    return [{"if": {"column_id": c}, "textAlign": "right",
             "fontFamily": theme.FONT_NUM,
             "fontVariantNumeric": "tabular-nums"} for c in cols]


def btn(kind: str = "plain") -> dict:
    base = {"padding": "7px 14px", "fontSize": "12px", "fontWeight": 600,
            "borderRadius": "5px", "cursor": "pointer",
            "whiteSpace": "nowrap"}
    if kind == "primary":
        return {**base, "border": f"1px solid {theme.INK}",
                "background": theme.INK, "color": theme.SURFACE}
    if kind == "go":
        return {**base, "border": f"1px solid {theme.POSITIVE}",
                "background": theme.POSITIVE, "color": theme.SURFACE}
    return {**base, "border": f"1px solid {theme.LINE}",
            "background": theme.SURFACE, "color": theme.TEXT}


def field(label: str, component, width: str | None = None):
    return html.Div([html.Div(label, style=LABEL), component],
                    style={"width": width} if width else {})


def bar(*children, **style):
    """A row of filter fields / buttons, aligned on their bottom edge."""
    return html.Div(list(children), style={
        "display": "flex", "gap": "12px", "flexWrap": "wrap",
        "alignItems": "flex-end", **style})


def header(title: str, subtitle: str):
    return html.Div([
        html.Div(title, style={"fontSize": "17px", "fontWeight": 700,
                               "color": theme.INK}),
        html.Div(subtitle, style={"fontSize": "12px", "color": theme.SLATE,
                                  "marginTop": "2px"}),
    ], style={"marginBottom": "14px"})


def message(text: str, ok: bool = True):
    if not text:
        return ""
    return html.Span(text, style={"fontSize": "12px",
                                  "color": theme.POSITIVE if ok
                                  else theme.NEGATIVE})


def select_rows(data, ids):
    """(selected_rows, selected_row_ids) for a DataTable. A DataTable draws
    its tick boxes from selected_rows - indices into data - so setting only
    the ids leaves the boxes as they were."""
    ids = list(ids or [])
    wanted = set(ids)
    return [i for i, r in enumerate(data or []) if r["id"] in wanted], ids


def category_options():
    return [{"label": c, "value": c} for c in repo.categories()]


def gbp(value) -> str:
    """Signed money for tables: -12.5 -> '(12.50)', 3439 -> '3,439.00'."""
    if value is None or value != value:
        return ""
    return theme.money(float(value), currency="", dp=2)


# --- Assign bar ---------------------------------------------------------------

def assign_bar(prefix: str, button_label: str = "Assign to selected",
               remember: bool = True, extra=None):
    """Category / sub-category pickers, an optional Remember tick with its
    keyword, and an apply button. Ids: {prefix}-cat, -sub, -remember, -kw,
    -apply. Call register_subcategories(prefix) once at import."""
    remember_part = []
    if remember:
        remember_part = [
            html.Div([
                dcc.Checklist(id=f"{prefix}-remember",
                              options=[{"label": " Remember merchant as a rule",
                                        "value": "y"}],
                              value=[], inputStyle={"marginRight": "6px"},
                              labelStyle={"fontSize": "12px",
                                          "color": theme.TEXT}),
                dcc.Input(id=f"{prefix}-kw", type="text", debounce=False,
                          placeholder="keyword", style={**INPUT,
                          "width": "220px", "marginTop": "4px",
                          "fontFamily": theme.FONT_NUM}),
            ]),
        ]
    return bar(
        field("Category", dcc.Dropdown(id=f"{prefix}-cat",
                                       options=category_options(),
                                       clearable=False,
                                       style={"fontSize": "12.5px"}),
              width="190px"),
        field("Sub-category", dcc.Dropdown(id=f"{prefix}-sub", options=[],
                                           clearable=False,
                                           style={"fontSize": "12.5px"}),
              width="190px"),
        *remember_part,
        html.Button(button_label, id=f"{prefix}-apply", n_clicks=0,
                    style=btn("primary")),
        *(extra or []),
    )


def register_subcategories(prefix: str):
    """Sub-category options follow the category. The current sub-category is
    kept if it belongs to the new category (a form filled in one go sets
    both), otherwise the first one is picked."""
    @callback(Output(f"{prefix}-sub", "options"),
              Output(f"{prefix}-sub", "value"),
              Input(f"{prefix}-cat", "value"),
              State(f"{prefix}-sub", "value"))
    def _subs(cat, current):
        subs = repo.categories().get(cat, []) if cat else []
        value = current if current in subs else (subs[0] if subs else None)
        return [{"label": s, "value": s} for s in subs], value
    return _subs
