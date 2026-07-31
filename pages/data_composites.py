"""
Data -> Composites.

Two grids. The upper one is the composites themselves - fund_id, name, type
and the account a holding is booked to. The lower one is the components of
whichever composite is selected, with a running weight total, because a blend
whose weights do not sum to 1.0 is priced wrong and nothing else will tell you.

ASSET:HOUSE sits in the same table with no components. That is deliberate: it
is not a blend, it just needs somewhere to carry its account.

Components are added through a searchable dropdown rather than by typing into
a blank grid row. A DataTable dropdown cell renders as an invalid red box
until it has a value, and needs one click to focus and another to open, which
is a poor way to pick from several hundred instruments.
"""

from dash import html, dcc, dash_table, callback, Input, Output, State, no_update

from core import theme, db
from core.repo import composites as repo
from ui.layout import card

TABLE_STYLE = dict(
    style_table={"overflowX": "auto"},
    style_cell={"fontSize": "12.5px", "padding": "7px 10px",
                "fontFamily": "inherit", "textAlign": "left"},
    style_header={"fontWeight": 600, "fontSize": "11.5px",
                  "textTransform": "uppercase", "letterSpacing": "0.06em"},
)

BTN = {"padding": "7px 14px", "border": f"1px solid {theme.LINE}",
       "borderRadius": "3px", "background": theme.SURFACE, "cursor": "pointer",
       "fontSize": "12.5px", "fontWeight": 500, "color": theme.INK,
       "marginRight": "8px"}


def _instruments_frame():
    """Composites cannot be components of other composites - that would need
    a build order the importer does not have."""
    return db.query("""
        SELECT fund_id, name FROM instruments
        WHERE fund_id NOT LIKE 'COMPOSITE:%'
        ORDER BY name
    """)


def _instrument_options():
    df = _instruments_frame()
    return [{"label": f"{r['name']} ({r['fund_id']})", "value": r["fund_id"]}
            for r in df.to_dict("records")]


def _valid_fund_ids() -> set:
    return set(_instruments_frame()["fund_id"])


def render():
    comps = repo.all_composites()
    return html.Div([
        html.Div(id="cmp-msg"),

        card("Composites", html.Div([
            dash_table.DataTable(
                id="cmp-table",
                columns=[
                    {"name": "Fund ID", "id": "fund_id", "editable": False},
                    {"name": "Display name", "id": "display_name"},
                    {"name": "Asset type", "id": "asset_type"},
                    {"name": "Account", "id": "account"},
                ],
                data=comps.to_dict("records"),
                editable=True,
                row_deletable=True,
                row_selectable="single",
                selected_rows=[0] if len(comps) else [],
                **TABLE_STYLE,
            ),
            html.Div([
                html.Button("Save composites", id="cmp-save", n_clicks=0,
                            style=BTN),
            ], style={"marginTop": "12px"}),

            html.Div([
                html.Div("Add a composite", style={
                    "fontSize": "11.5px", "fontWeight": 600,
                    "textTransform": "uppercase", "letterSpacing": "0.06em",
                    "color": theme.SLATE, "marginBottom": "8px"}),
                dcc.Input(id="cmp-new-id", placeholder="COMPOSITE:HSBC_X",
                          style={"marginRight": "8px", "padding": "6px 9px",
                                 "width": "230px"}),
                dcc.Input(id="cmp-new-name", placeholder="Display name",
                          style={"marginRight": "8px", "padding": "6px 9px",
                                 "width": "230px"}),
                dcc.Input(id="cmp-new-account", placeholder="Account",
                          style={"marginRight": "8px", "padding": "6px 9px",
                                 "width": "150px"}),
                html.Button("Add", id="cmp-add", n_clicks=0, style=BTN),
            ], style={"marginTop": "18px", "paddingTop": "16px",
                      "borderTop": f"1px solid {theme.LINE}"}),
        ])),

        html.Div(id="cmp-components"),
    ])


def _components_card(fund_id, flash=None):
    rows = repo.components(fund_id)
    data = [{"component_fund_id": r["component_fund_id"],
             "weight": r["weight"]} for r in rows.to_dict("records")]
    total = sum(float(r["weight"] or 0) for r in data)
    ok = abs(total - 1.0) < 1e-6
    colour = theme.POSITIVE if ok else theme.NEGATIVE

    valid = _valid_fund_ids()
    orphans = [r["component_fund_id"] for r in data
               if r["component_fund_id"] not in valid]

    body = []
    if flash is not None:
        body.append(flash)

    body += [
        dash_table.DataTable(
            id="cmp-parts",
            columns=[
                {"name": "Component", "id": "component_fund_id",
                 "presentation": "dropdown"},
                {"name": "Weight", "id": "weight", "type": "numeric"},
            ],
            data=data,
            editable=True,
            row_deletable=True,
            dropdown={"component_fund_id": {
                "options": _instrument_options(),
                "clearable": False,
            }},
            **TABLE_STYLE,
        ),
        html.Div([
            html.Span("Weight total: ", style={"color": theme.SLATE,
                                               "fontSize": "12.5px"}),
            html.Span(f"{total:.4f}", style={"color": colour,
                                             "fontWeight": 600, **theme.NUM}),
            html.Span("" if ok else "  — does not sum to 1.0",
                      style={"color": theme.NEGATIVE, "fontSize": "12px"}),
        ], style={"marginTop": "10px"}),
    ]

    if orphans:
        body.append(html.Div(
            "Not in instruments, so unpriceable: " + ", ".join(orphans),
            style={"color": theme.NEGATIVE, "fontSize": "12px",
                   "marginTop": "8px"}))

    body += [
        html.Div([
            html.Button("Save weights", id="cmp-parts-save", n_clicks=0,
                        style=BTN),
        ], style={"marginTop": "12px"}),

        # Adding goes through a real dropdown, not a blank grid row.
        html.Div([
            html.Div("Add a component", style={
                "fontSize": "11.5px", "fontWeight": 600,
                "textTransform": "uppercase", "letterSpacing": "0.06em",
                "color": theme.SLATE, "marginBottom": "8px"}),
            html.Div([
                html.Div(
                    dcc.Dropdown(
                        id="cmp-part-pick",
                        options=_instrument_options(),
                        placeholder="Search instruments...",
                        clearable=True,
                        style={"fontSize": "12.5px"},
                    ),
                    style={"flex": "1", "minWidth": "0",
                           "marginRight": "10px"}),
                dcc.Input(id="cmp-part-weight", type="number", value=None,
                          placeholder="Weight", step=0.001,
                          style={"padding": "6px 9px", "width": "110px",
                                 "marginRight": "10px"}),
                html.Button("Add component", id="cmp-part-add", n_clicks=0,
                            style={**BTN, "marginRight": "0"}),
            ], style={"display": "flex", "alignItems": "center"}),
        ], style={"marginTop": "18px", "paddingTop": "16px",
                  "borderTop": f"1px solid {theme.LINE}"}),

        html.Div("Components are chosen from instruments. Prices are rebuilt "
                 "by the Composites importer on the next run, or from "
                 "Data > Importers > Run now.",
                 style={"color": theme.SLATE, "fontSize": "12px",
                        "marginTop": "12px"}),
    ]

    return card(f"Components — {fund_id}", html.Div(body))


def _flash(text, ok=True):
    colour = theme.POSITIVE if ok else theme.NEGATIVE
    return html.Div(text, style={
        "padding": "10px 14px", "marginBottom": "12px", "borderRadius": "3px",
        "fontSize": "13px", "color": colour,
        "border": f"1px solid {colour}33", "background": f"{colour}0D"})


def _selected_fund_id(selected, table):
    if not selected or not table:
        return None
    return table[selected[0]]["fund_id"]


# --- Callbacks -----------------------------------------------------------

@callback(
    Output("cmp-components", "children"),
    Input("cmp-table", "selected_rows"),
    State("cmp-table", "data"),
)
def show_components(selected, data):
    fund_id = _selected_fund_id(selected, data)
    if not fund_id:
        return html.Div()
    return _components_card(fund_id)


@callback(
    Output("cmp-msg", "children"),
    Output("cmp-table", "data"),
    Input("cmp-save", "n_clicks"),
    Input("cmp-add", "n_clicks"),
    State("cmp-table", "data"),
    State("cmp-new-id", "value"),
    State("cmp-new-name", "value"),
    State("cmp-new-account", "value"),
    prevent_initial_call=True,
)
def save_composites(save_clicks, add_clicks, data, new_id, new_name,
                    new_account):
    from dash import ctx
    trigger = ctx.triggered_id

    if trigger == "cmp-add":
        if not new_id or not new_name:
            return _flash("Fund ID and display name are both required.",
                          ok=False), no_update
        repo.upsert_composite(new_id.strip(), new_name.strip(), "Fund",
                              (new_account or "").strip() or None)
        return (_flash(f"Added {new_id.strip()}."),
                repo.all_composites().to_dict("records"))

    existing = set(repo.all_composites()["fund_id"])
    kept = set()
    for r in data:
        fid = (r.get("fund_id") or "").strip()
        if not fid:
            continue
        kept.add(fid)
        repo.upsert_composite(fid, (r.get("display_name") or fid).strip(),
                              (r.get("asset_type") or "Fund").strip(),
                              (r.get("account") or "").strip() or None)

    removed = existing - kept
    for fid in removed:
        repo.delete_composite(fid)

    note = f"Saved {len(kept)} composites"
    if removed:
        note += f", removed {len(removed)}"
    return _flash(note + "."), repo.all_composites().to_dict("records")


@callback(
    Output("cmp-msg", "children", allow_duplicate=True),
    Output("cmp-components", "children", allow_duplicate=True),
    Input("cmp-part-add", "n_clicks"),
    State("cmp-part-pick", "value"),
    State("cmp-part-weight", "value"),
    State("cmp-table", "selected_rows"),
    State("cmp-table", "data"),
    prevent_initial_call=True,
)
def add_component(_n, pick, weight, selected, table):
    fund_id = _selected_fund_id(selected, table)
    if not fund_id:
        return no_update, no_update

    if not pick:
        return no_update, _components_card(
            fund_id, flash=_flash("Pick an instrument first.", ok=False))

    try:
        w = float(weight)
    except (TypeError, ValueError):
        return no_update, _components_card(
            fund_id, flash=_flash("Weight must be a number.", ok=False))

    # Existing rows are preserved; a repeat pick updates that component's
    # weight rather than adding a duplicate, which the PK would reject anyway.
    current = repo.components(fund_id).to_dict("records")
    rows = [{"fund_id": r["component_fund_id"], "weight": r["weight"]}
            for r in current if r["component_fund_id"] != pick]
    rows.append({"fund_id": pick, "weight": w})
    repo.replace_components(fund_id, rows)

    total = sum(float(r["weight"] or 0) for r in rows)
    text = f"Added {pick}."
    if abs(total - 1.0) > 1e-6:
        text += f" Weights now sum to {total:.4f}, not 1.0."
    return None, _components_card(
        fund_id, flash=_flash(text, ok=abs(total - 1.0) <= 1e-6))


@callback(
    Output("cmp-msg", "children", allow_duplicate=True),
    Output("cmp-components", "children", allow_duplicate=True),
    Input("cmp-parts-save", "n_clicks"),
    State("cmp-parts", "data"),
    State("cmp-table", "selected_rows"),
    State("cmp-table", "data"),
    prevent_initial_call=True,
)
def save_parts(_n, parts, selected, table):
    fund_id = _selected_fund_id(selected, table)
    if not fund_id:
        return no_update, no_update

    valid = _valid_fund_ids()
    rows, unknown = [], []
    for p in (parts or []):
        cid = (p.get("component_fund_id") or "").strip()
        if not cid:
            continue
        if cid not in valid:
            unknown.append(cid)
            continue
        rows.append({"fund_id": cid, "weight": p.get("weight")})

    # Nothing is written if anything is wrong. A partial save would leave the
    # composite priced on a subset of its components, which reads as a real
    # price move rather than a mistake.
    if unknown:
        return no_update, _components_card(fund_id, flash=_flash(
            "Not saved. These are not in instruments: " + ", ".join(unknown)
            + ".", ok=False))

    if not rows:
        return no_update, _components_card(fund_id, flash=_flash(
            "Not saved — a composite needs at least one component.",
            ok=False))

    repo.replace_components(fund_id, rows)

    total = sum(float(r["weight"] or 0) for r in rows)
    text = f"Saved {len(rows)} components for {fund_id}."
    if abs(total - 1.0) > 1e-6:
        text += (f" Weights sum to {total:.4f}, not 1.0 — the blend will be "
                 f"priced on that basis.")
    return None, _components_card(
        fund_id, flash=_flash(text, ok=abs(total - 1.0) <= 1e-6))