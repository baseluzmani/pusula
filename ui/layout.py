"""
Shared UI building blocks. Dash lives here and in pages/ only, never in core/.
"""
from dash import html, dcc

from core import theme


def page_header(title: str, subtitle: str = ""):
    return html.Div(
        [
            html.H1(title, style=theme.H1),
            html.P(subtitle, style=theme.SUBTITLE) if subtitle else None,
        ]
    )


def card(title: str, *children):
    return html.Div(
        [html.Div(title, style=theme.CARD_TITLE), *children],
        style=theme.CARD,
    )


def subtabs(tab_id: str, labels: list[str], value: str | None = None):
    """Second-level navigation inside a section."""
    return html.Div(
        dcc.Tabs(
            id=tab_id,
            value=value or labels[0],
            children=[dcc.Tab(label=l, value=l) for l in labels],
        ),
        className="subtabs",
    )


def placeholder(text: str):
    return html.Div(
        text,
        style={
            "padding": "42px 20px",
            "textAlign": "center",
            "color": theme.SLATE,
            "border": f"1px dashed {theme.LINE}",
            "borderRadius": "4px",
            "backgroundColor": theme.SURFACE,
        },
    )


def data_table(df, numeric_cols: list[str] | None = None, max_rows: int = 200):
    """Plain HTML table. Fast, styled, no DataTable dependency."""
    numeric_cols = numeric_cols or []
    head = html.Thead(html.Tr([html.Th(c) for c in df.columns]))
    body = html.Tbody(
        [
            html.Tr(
                [
                    html.Td(
                        row[c],
                        className="num" if c in numeric_cols else "",
                    )
                    for c in df.columns
                ]
            )
            for _, row in df.head(max_rows).iterrows()
        ]
    )
    return html.Table([head, body], className="pz")
