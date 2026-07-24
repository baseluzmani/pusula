"""
Pusula - single entry point.

Run:  source .venv/bin/activate && python app.py
Then: http://localhost:8060

This file builds the persistent top bar and hands the rest of the screen to
whichever page matches the URL. No business logic, no SQL.
"""
import dash
from dash import Dash, html, dcc

from core import config, theme

app = Dash(
    __name__,
    use_pages=True,              # auto-discovers everything in pages/
    suppress_callback_exceptions=True,
    title=config.APP_NAME,
)
server = app.server               # for gunicorn / systemd later


def compass_mark():
    """Compass needle in the brand lockup. Settles north on page load."""
    return html.Div(
        html.Div(
            className="needle",
            style={
                "width": 0, "height": 0,
                "borderLeft": "5px solid transparent",
                "borderRight": "5px solid transparent",
                "borderBottom": f"13px solid {theme.NEEDLE}",
            },
        ),
        style={"display": "flex", "alignItems": "center"},
    )


def topbar():
    ordered = sorted(dash.page_registry.values(),
                     key=lambda p: p.get("order", 99))
    return html.Nav(
        [
            html.Div([compass_mark(), html.Span(config.APP_NAME)],
                     className="brand"),
            *[
                dcc.Link(p["name"], href=p["relative_path"], className="navlink")
                for p in ordered
            ],
            html.Div(f"dev · {config.PORT}", className="env-tag"),
        ],
        className="topbar",
    )


def serve_layout():
    # A function, so the nav rebuilds if pages change during hot reload
    return html.Div([dcc.Location(id="url"), topbar(), dash.page_container])


app.layout = serve_layout


if __name__ == "__main__":
    problems = config.check()
    if problems:
        print("\n  Startup warnings:")
        for p in problems:
            print(f"   - {p}")
    print(f"\n  {config.APP_NAME} -> http://localhost:{config.PORT}\n")
    app.run(debug=config.DEBUG, host="0.0.0.0", port=config.PORT)
