"""
Help - the full page.

The same content as the drawer (ui/help.py), with room to read: the map of
every page down the left, the chosen entry on the right. Not in the top bar
(nav=False); reached from the drawer's "Open full page" link, or /help.

    /help                    the map of every page
    /help?topic=<key>        one entry, e.g. portfolio.transactions
    /help?topic=def.pnl      the definitions, P&L first
    /help?topic=system       how data gets in
    /help?q=<words>          search
"""
import dash
from dash import html, dcc

from core import help as content
from core import theme
from ui import help as ui

dash.register_page(__name__, path="/help", name="Help", order=99, nav=False)


def _side():
    items = [dcc.Link("All pages", href="/help", className="help-side-link")]
    for section in content.SECTIONS:
        sec = content.get(section)
        if sec is None:
            continue
        items.append(dcc.Link(sec.title, href=f"/help?topic={section}",
                              className="help-side-link help-side-section"))
        for t in content.tabs(section):
            items.append(dcc.Link(t.title, href=f"/help?topic={t.key}",
                                  className="help-side-link help-side-tab"))
    items += [dcc.Link("Definitions", href="/help?topic=def",
                       className="help-side-link help-side-section"),
              dcc.Link("System", href="/help?topic=system",
                       className="help-side-link help-side-section")]
    return html.Div(items, className="help-side")


def layout(topic=None, q=None, **_):
    if q:
        title, body = f"Search: {q}", ui.render_search(q, "page")
    elif not topic or topic == "__pages__":
        title, body = "All pages", ui.render_pages("page")
    elif topic == "def" or topic.startswith("def."):
        title = "Definitions"
        body = ui.render_definitions(topic if "." in topic else None, "page")
    elif topic.startswith("system"):
        title, body = "System", ui.render_system("page")
    else:
        title, body = None, ui.render_topic(topic, "page")

    main = [html.H2(title, className="help-page-title")] if title else []
    main.append(body)
    return html.Div([
        _side(),
        html.Div([
            html.Form([
                dcc.Input(name="q", type="search", value=q or "",
                          placeholder="Search help and definitions",
                          className="help-search"),
            ], action="/help", method="get"),
            *main,
        ], className="help-main"),
    ], className="help-page", style=theme.PAGE)
