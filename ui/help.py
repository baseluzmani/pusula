"""
ui.help
=======

The in-app help: a "?" button in the top bar, a drawer that opens on the tab
you are on, (i) icons beside table and panel titles, and the same content as
a full page at /help (pages/help.py). The words live in help/*.md and are
read by core.help; this module only draws them.

Pieces a page uses
------------------
    help_title("Holdings detail", "portfolio.portfolio.holdings-detail")
        a card title with an (i) icon: hover shows the entry's first line,
        click opens it in the drawer
    help_icon(key)
        the icon on its own, for a title built some other way

How "This tab" knows where you are
----------------------------------
Every section uses one row of dcc.Tabs. When "?" is clicked, a small
browser-side callback reads the URL and the label of the selected tab
(class tab--selected) and core.help.topic_for maps them to a help key, so
no page has to declare anything.
"""

from __future__ import annotations

import dash
from dash import (html, dcc, callback, clientside_callback, Input, Output,
                  State, ALL, ctx, no_update)

from core import help as content
from core import theme

VIEWS = [("tab", "This tab"), ("pages", "All pages"),
         ("defs", "Definitions"), ("system", "System")]


# --- small pieces pages use ---------------------------------------------------

def help_icon(key: str):
    """(i) icon: hover = the entry's first line, click = open it in the
    drawer."""
    topic = content.get(key)
    tip = topic.summary if topic else "No help written for this yet."
    return html.Span("i", id={"type": "help-icon", "key": key}, n_clicks=0,
                     className="help-i", title=tip, role="button",
                     **{"aria-label": "Help"})


def help_title(text, key: str, style: dict | None = None):
    """A card title followed by its (i) icon."""
    base = dict(style if style is not None else theme.CARD_TITLE)
    return html.Div([html.Span(text), help_icon(key)],
                    style={**base, "display": "flex", "alignItems": "center",
                           "gap": "6px"})


def topbar_button():
    return html.Button("?", id="help-btn", n_clicks=0, className="help-btn",
                       title="Help for this page")


# --- drawing the content ------------------------------------------------------

def _link(label, key, mode, className="help-link"):
    """A jump to another help entry: a button inside the drawer, a URL on the
    full page."""
    if mode == "page":
        return dcc.Link(label, href=f"/help?topic={key}", className=className)
    return html.Button(label, id={"type": "help-link", "key": key},
                       n_clicks=0, className=className)


def _md(text):
    return dcc.Markdown(text or "", className="help-md", link_target="_self")


def _terms(topic, mode):
    if not topic.terms:
        return None
    chips = []
    for term in topic.terms:
        d = content.get(f"def.{term}")
        if d:
            chips.append(_link(d.title, d.key, mode, "help-chip"))
    return html.Div([html.Div("Figures", className="help-h4"),
                     html.Div(chips, className="help-chips")])


def _crumbs(key, mode):
    trail = content.breadcrumb(key)
    parts = []
    for i, t in enumerate(trail):
        if i:
            parts.append(html.Span(" › ", className="help-sep"))
        parts.append(html.Span(t.title) if i == len(trail) - 1
                     else _link(t.title, t.key, mode, "help-crumb"))
    return html.Div(parts, className="help-crumbs")


def render_topic(key, mode="drawer"):
    """One entry: a tab with its tables and panels below it, or a single
    table / panel with a way back to its tab."""
    topic = content.get(key)
    if topic is None:
        return html.Div([
            html.P("There is no help for this screen yet."),
            _link("See all pages", "__pages__", mode),
        ], className="help-empty")
    if topic.key.startswith(("def.", "system.")):
        return render_definitions(key, mode) if topic.key.startswith("def.") \
            else render_system(mode)

    blocks = [_crumbs(key, mode), html.H3(topic.title, className="help-h3"),
              _md(topic.body), _terms(topic, mode)]
    if topic.level == 1:                         # a whole section
        blocks.append(html.Div("Tabs", className="help-h4"))
        blocks.append(_tab_list(topic.key, mode))
    for child_key in topic.children if topic.level == 2 else []:
        child = content.get(child_key)
        blocks.append(html.Div([
            html.Div(child.title, className="help-h4"),
            _md(child.body), _terms(child, mode),
        ], className="help-sub", id=f"help-{child.key}"))
    if topic.level == 3 and topic.parent:
        parent = content.get(topic.parent)
        blocks.append(html.Div(["About the whole tab: ",
                                _link(parent.title, parent.key, mode)],
                               className="help-back"))
    return html.Div([b for b in blocks if b is not None])


def _tab_list(section, mode):
    rows = []
    for t in content.tabs(section):
        rows.append(html.Div([_link(t.title, t.key, mode),
                              html.Span(t.summary, className="help-oneliner")],
                             className="help-row"))
    return html.Div(rows)


def render_pages(mode="drawer"):
    """Every section and tab with its one-line summary: a map of the app."""
    out = []
    for section in content.SECTIONS:
        sec = content.get(section)
        if sec is None:
            continue
        out.append(html.Div([
            _link(sec.title, sec.key, mode, "help-section-link"),
            _tab_list(section, mode),
        ], className="help-section"))
    return html.Div(out)


def render_definitions(focus=None, mode="drawer"):
    """All definitions, the one asked about first and highlighted."""
    defs = sorted(content.definitions(), key=lambda t: t.title.lower())
    if focus:
        defs.sort(key=lambda t: t.key != focus)
    return html.Div([
        html.Div([html.Div(d.title, className="help-h4"), _md(d.body)],
                 className="help-def" + (" help-focus" if d.key == focus
                                         else ""))
        for d in defs
    ])


def render_system(mode="drawer"):
    """system.md, then the import jobs straight from importers/registry.py so
    the schedule shown is always the one the code has."""
    blocks = []
    for t in content.system():
        blocks.append(html.Div([html.Div(t.title, className="help-h4"),
                                _md(t.body)], className="help-def"))
    try:
        from importers.registry import REGISTRY
        rows = [html.Tr([html.Td(i.label), html.Td(i.schedule or "By hand"),
                         html.Td(i.description)]) for i in REGISTRY]
        blocks.append(html.Div([
            html.Div("Jobs (from the importer list)", className="help-h4"),
            html.Table([html.Thead(html.Tr([html.Th("Job"), html.Th("When"),
                                            html.Th("What it does")])),
                        html.Tbody(rows)], className="help-table"),
        ], className="help-def"))
    except Exception:                                     # noqa: BLE001
        pass
    return html.Div(blocks)


def render_search(query, mode="drawer"):
    hits = content.search(query)
    if not hits:
        return html.P("Nothing found.", className="help-empty")
    rows = []
    for t in hits:
        where = " › ".join(x.title for x in content.breadcrumb(t.key)[:-1])
        if t.key.startswith("def."):
            where = "Definitions"
        elif t.key.startswith("system."):
            where = "System"
        rows.append(html.Div([
            _link(t.title, t.key, mode),
            html.Span(where, className="help-where"),
            html.Div(t.summary, className="help-oneliner"),
        ], className="help-row"))
    return html.Div(rows)


def render_view(view, key, mode="drawer"):
    if view == "pages" or (view == "tab" and key == "__pages__"):
        return render_pages(mode)
    if view == "defs":
        return render_definitions(key if str(key).startswith("def.") else None,
                                  mode)
    if view == "system":
        return render_system(mode)
    return render_topic(key, mode)


def view_for(key):
    key = str(key or "")
    if key == "__pages__":
        return "pages"
    if key.startswith("def."):
        return "defs"
    if key.startswith("system"):
        return "system"
    return "tab"


# --- the drawer ---------------------------------------------------------------

def drawer():
    """Lives in app.layout, so it is there on every page."""
    return html.Div([
        dcc.Store(id="help-ctx"),
        dcc.Store(id="help-key"),
        html.Div([
            html.Div([
                html.Span("Help", className="help-title"),
                dcc.Link("Open full page", id="help-full", href="/help",
                         className="help-full"),
                html.Button("×", id="help-close", n_clicks=0,
                            className="help-x", title="Close"),
            ], className="help-head"),
            dcc.Input(id="help-search", type="search", debounce=False,
                      placeholder="Search help and definitions",
                      className="help-search"),
            dcc.RadioItems(id="help-view", value="tab",
                           options=[{"label": l, "value": v}
                                    for v, l in VIEWS],
                           className="help-views", inline=True),
            html.Div(id="help-body", className="help-body"),
        ], id="help-drawer", className="help-drawer"),
    ])


# Browser side: where is the user? The URL and the selected tab's label.
clientside_callback(
    """
    function(n) {
        if (!n) { return window.dash_clientside.no_update; }
        var page = document.getElementById('_pages_content') || document;
        var sel = page.querySelector('.tab--selected');
        return {path: window.location.pathname,
                tab: sel ? sel.innerText : null, n: n};
    }
    """,
    Output("help-ctx", "data"),
    Input("help-btn", "n_clicks"),
)


@callback(
    Output("help-drawer", "className"),
    Output("help-key", "data"),
    Output("help-view", "value"),
    Output("help-search", "value"),
    Input("help-ctx", "data"),
    Input("help-close", "n_clicks"),
    Input({"type": "help-icon", "key": ALL}, "n_clicks"),
    Input({"type": "help-link", "key": ALL}, "n_clicks"),
    Input("url", "pathname"),
    State("help-drawer", "className"),
    prevent_initial_call=True,
)
def _route(where, _close, _icons, _links, _path, cls):
    """Open, close and move the drawer. "?" toggles; an (i) icon or a link
    opens the entry it names; changing page closes it."""
    trig = ctx.triggered_id
    is_open = "open" in str(cls or "")
    shut = ("help-drawer", no_update, no_update, no_update)
    if trig in ("help-close", "url"):
        return shut
    if trig == "help-ctx":
        if is_open:
            return shut
        key = content.topic_for((where or {}).get("path"),
                                (where or {}).get("tab"))
        return "help-drawer open", key, "tab", ""
    if isinstance(trig, dict):
        # A newly drawn icon or link fires with n_clicks 0: not a click.
        if not (ctx.triggered and ctx.triggered[0].get("value")):
            return no_update, no_update, no_update, no_update
        key = trig["key"]
        return "help-drawer open", key, view_for(key), ""
    return no_update, no_update, no_update, no_update


@callback(
    Output("help-body", "children"),
    Output("help-full", "href"),
    Input("help-view", "value"),
    Input("help-key", "data"),
    Input("help-search", "value"),
)
def _body(view, key, query):
    if query and query.strip():
        return render_search(query), f"/help?q={query.strip()}"
    href = f"/help?topic={key}" if key and key != "__pages__" else "/help"
    return render_view(view, key), href
