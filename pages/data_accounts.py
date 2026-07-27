"""
Data - Accounts tab.

An editable grid of accounts. Edit cells directly, add rows, then Save. The
account name feeds the transaction form's dropdown, so keeping this tidy is
what stops typos becoming new accounts.

Owner and wrapper were seeded by splitting existing account names on the
first space, which gets most of them right and some of them wrong - "Share
Incentive Plan" became owner "Share", for instance. Fix those here.

Wrapper is what a future by-wrapper report will group on, so it is worth
filling in consistently (ISA / SIPP / Trading / Pension / Other).
"""

from __future__ import annotations

import dash
from dash import html, dcc, dash_table, callback, Input, Output, State, no_update

from core import theme
from core.repo import standing as repo

COLUMNS = [
    {"name": "Name", "id": "name", "editable": True},
    {"name": "Owner", "id": "owner", "editable": True},
    {"name": "Wrapper", "id": "wrapper", "editable": True},
    {"name": "Order", "id": "sort_order", "editable": True,
     "type": "numeric"},
    {"name": "Active", "id": "active", "editable": True, "type": "numeric"},
]


def render():
    return html.Div([
        html.Div([
            html.Div([
                html.Div("Accounts", style={"fontSize": "17px",
                         "fontWeight": 700, "color": theme.INK}),
                html.Div("Edit cells directly, then Save. Active 0 hides an "
                         "account from the transaction form without losing "
                         "its history.",
                         style={"fontSize": "12px", "color": theme.SLATE,
                                "marginTop": "2px"}),
            ]),
            html.Div([
                html.Button("Add account", id="acc-add", n_clicks=0,
                            style=_btn()),
                html.Button("Save", id="acc-save", n_clicks=0,
                            style=_btn(primary=True)),
            ], style={"display": "flex", "gap": "8px"}),
        ], style={"display": "flex", "justifyContent": "space-between",
                  "alignItems": "flex-start", "marginBottom": "12px"}),

        html.Div(id="acc-feedback", style={"marginBottom": "10px"}),

        html.Div(dash_table.DataTable(
            id="acc-table",
            columns=COLUMNS,
            data=[],
            editable=True,
            row_deletable=True,
            sort_action="native",
            style_table={"overflowX": "auto"},
            style_header={"backgroundColor": theme.INK, "color": "white",
                          "fontWeight": "600", "fontSize": "11px",
                          "textAlign": "left", "border": "none"},
            style_cell={"padding": "6px 10px", "fontSize": "12px",
                        "fontFamily": "DM Sans, system-ui, sans-serif",
                        "textAlign": "left", "color": theme.INK,
                        "border": f"1px solid {theme.LINE}"},
            style_data_conditional=[
                {"if": {"filter_query": "{active} = 0"},
                 "backgroundColor": theme.SURFACE, "color": theme.NEUTRAL},
                {"if": {"filter_query": "{wrapper} is blank"},
                 "backgroundColor": "#FFF8E6"},
            ],
        ), style=theme.CARD),

        dcc.Store(id="acc-loaded", data=0),
    ])


def _btn(primary=False):
    base = {"padding": "7px 15px", "borderRadius": "4px", "fontSize": "12px",
            "fontWeight": 600, "cursor": "pointer", "border": "none"}
    if primary:
        return {**base, "backgroundColor": theme.POSITIVE, "color": "#fff"}
    return {**base, "backgroundColor": theme.SURFACE, "color": theme.TEXT,
            "border": f"1px solid {theme.LINE}"}


@callback(Output("acc-table", "data"), Output("acc-loaded", "data"),
          Input("acc-loaded", "data"))
def _load(_n):
    df = repo.accounts()
    return (df.to_dict("records") if not df.empty else []), 1


@callback(
    Output("acc-table", "data", allow_duplicate=True),
    Input("acc-add", "n_clicks"), State("acc-table", "data"),
    prevent_initial_call=True,
)
def _add_row(_n, rows):
    rows = list(rows or [])
    rows.append({"id": None, "name": "", "owner": "", "wrapper": "",
                 "sort_order": 0, "active": 1})
    return rows


@callback(
    Output("acc-feedback", "children"),
    Output("acc-table", "data", allow_duplicate=True),
    Input("acc-save", "n_clicks"), State("acc-table", "data"),
    prevent_initial_call=True,
)
def _save(_n, rows):
    rows = list(rows or [])
    named = [r for r in rows if (r.get("name") or "").strip()]
    if not named:
        return _msg("Nothing to save - every row needs a name.", False), no_update

    # Rows removed from the grid are deleted, but only if nothing references
    # them; an account still on transactions stays, with a warning.
    existing = repo.accounts()
    kept_ids = {r.get("id") for r in named if r.get("id")}
    usage = repo.account_usage()
    blocked, removed = [], 0
    for r in existing.to_dict("records"):
        if r["id"] in kept_ids:
            continue
        if usage.get(r["name"], 0) > 0:
            blocked.append(f"{r['name']} ({usage[r['name']]} transactions)")
        else:
            repo.delete_account(r["id"])
            removed += 1

    try:
        n = repo.save_accounts(named)
    except Exception as exc:                                   # noqa: BLE001
        return _msg(f"Save failed: {exc}", False), no_update

    parts = [f"Saved {n} accounts."]
    if removed:
        parts.append(f"Removed {removed}.")
    if blocked:
        parts.append("Kept (still used by transactions): "
                     + ", ".join(blocked) + ".")

    df = repo.accounts()
    return (_msg(" ".join(parts), not blocked),
            df.to_dict("records") if not df.empty else [])


def _msg(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEEDLE
    return html.Div(text, style={
        "padding": "9px 14px", "borderRadius": "4px", "fontSize": "12.5px",
        "fontWeight": 500, "color": colour,
        "border": f"1px solid {colour}44", "background": f"{colour}0D"})
