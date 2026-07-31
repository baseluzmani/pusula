"""
Spending -> Allowances.

Ported from the 8050 dashboard. The arithmetic lives in core/allowances.py,
the figures in user_settings['allowances'], the year-indexed limits in
allowance_limits.

One structural change from the original. That version declared 78 individual
Input() objects and unpacked them by counting list positions, so adding a
field or a tax year shifted every index after it and silently wrote values
into the wrong column. Inputs here carry pattern-matching ids -
{"type": "alw", "person": ..., "field": ..., "year": ...} - so a new year is
a new row and nothing else moves.

Input tables are built once on render rather than on every store change; a
rebuild mid-edit steals focus from the box you are typing in.
"""

from dash import (html, dcc, dash_table, callback, Input, Output, State,
                  ALL, ctx, no_update)

from core import theme, allowances as calc
from core.repo import allowances as repo
from ui.layout import card

MONO = {"fontFamily": "monospace"}

PEOPLE = {
    "ahmet": {"label": "Ahmet", "car": True},
    "burcu": {"label": "Burcu", "car": False},
}

FIELDS = ["salary", "bonus", "car_sacrifice", "employer_pension",
          "employee_pension", "other_deductions", "sipp_done", "sipp_future",
          "isa"]

HEADINGS = {
    "salary": "Salary",
    "bonus": "Bonus",
    "car_sacrifice": "Car sacrifice",
    "employer_pension": "Employer pension",
    "employee_pension": "Employee pension",
    "other_deductions": "Other pre-tax deductions",
    "sipp_done": "SIPP done (net)",
    "sipp_future": "SIPP future (net)",
    "isa": "ISA done",
}

BTN = {"padding": "7px 14px", "border": f"1px solid {theme.LINE}",
       "borderRadius": "3px", "background": theme.SURFACE, "cursor": "pointer",
       "fontSize": "12.5px", "fontWeight": 500, "color": theme.INK}


# --- Small table primitives ----------------------------------------------

def _th(text, align="right"):
    return html.Th(text, style={
        "padding": "7px 10px", "fontSize": "11px", "fontWeight": 600,
        "letterSpacing": "0.05em", "textTransform": "uppercase",
        "color": theme.SLATE, "textAlign": align, "whiteSpace": "nowrap",
        "borderBottom": f"1px solid {theme.LINE}"})


def _money(val, colour=None, bold=False):
    text = "—" if val is None else f"£{val:,.0f}"
    style = {"padding": "5px 10px", "fontSize": "12px", "textAlign": "right",
             **MONO, "fontWeight": 700 if bold else 400}
    if colour:
        style["color"] = colour
    return html.Td(text, style=style)


def _year_cell(yr, current):
    return html.Td(yr, style={
        "padding": "5px 10px", "fontSize": "12.5px", "whiteSpace": "nowrap",
        "fontWeight": 700 if yr == current else 400,
        "color": theme.INK if yr == current else theme.SLATE})


def _row_bg(yr, current, i):
    if yr == current:
        return f"{theme.NEEDLE}14"
    return theme.SURFACE if i % 2 == 0 else "transparent"


def _table(header_cells, rows):
    return html.Div(
        html.Table([html.Thead(html.Tr(header_cells)), html.Tbody(rows)],
                   style={"width": "100%", "borderCollapse": "collapse"}),
        style={"overflowX": "auto"})


def _num_input(person, field, year, value):
    return dcc.Input(
        id={"type": "alw", "person": person, "field": field, "year": year},
        type="number", value=value or 0, debounce=True,
        style={"width": "104px", "fontSize": "12px", "textAlign": "right",
               "border": f"1px solid {theme.LINE}", "borderRadius": "3px",
               "padding": "4px 7px", **MONO})


# --- Builders ------------------------------------------------------------

def _input_table(person, data, years, current, include_car):
    fields = [f for f in FIELDS if include_car or f != "car_sacrifice"]
    header = [_th("Year", "left")] + [_th(HEADINGS[f]) for f in fields]

    rows = []
    for i, yr in enumerate(years):
        d = data.get(person, {}).get(yr, {})
        cells = [_year_cell(yr, current)]
        for f in fields:
            cells.append(html.Td(_num_input(person, f, yr, d.get(f, 0)),
                                 style={"padding": "4px 6px"}))
        rows.append(html.Tr(cells, style={
            "background": _row_bg(yr, current, i),
            "borderBottom": f"1px solid {theme.LINE}"}))
    return _table(header, rows)


def _results_table(results, years, current):
    header = [_th("Year", "left")] + [_th(h) for h in [
        "Allowance", "Carry fwd", "Available", "Employer", "Employee",
        "SIPP gross", "Total used", "Remaining", "Car sacrifice", "Car BIK",
        "Adjusted income", "Gap to £100k", "Extra SIPP needed"]]

    rows = []
    for i, yr in enumerate(years):
        r = results.get(yr)
        if not r:
            continue
        rem = r.get("remaining", 0)
        salary = r.get("salary", 0)

        if salary > 0:
            adj, gap, extra = calc.adjusted_income(
                salary, r.get("bonus", 0), r.get("car_sacrifice", 0),
                r.get("car_bik", 0), r.get("employee_pension", 0),
                r.get("other_deductions", 0), r.get("sipp_gross_done", 0),
                r.get("sipp_gross_future", 0))
        else:
            adj = gap = extra = None

        sipp_gross = (r.get("sipp_gross_done", 0)
                      + r.get("sipp_gross_future", 0))

        rows.append(html.Tr([
            _year_cell(yr, current),
            _money(r.get("allowance")),
            _money(r.get("carry_fwd")),
            _money(r.get("available"), bold=True),
            _money(r.get("employer_pension")),
            _money(r.get("employee_pension")),
            _money(sipp_gross),
            _money(r.get("total_used"), bold=True),
            _money(rem, colour=theme.POSITIVE if rem > 0 else theme.NEGATIVE,
                   bold=True),
            _money(r.get("car_sacrifice") or None),
            _money(r.get("car_bik") or None),
            _money(adj, colour=None if adj is None else (
                theme.NEGATIVE if adj > calc.TAPER_THRESHOLD
                else theme.POSITIVE)),
            _money(gap, colour=None if gap is None else (
                theme.NEGATIVE if gap > 0 else theme.POSITIVE)),
            _money(extra if (extra and gap) else None, colour=theme.NEGATIVE),
        ], style={"background": _row_bg(yr, current, i),
                  "borderBottom": f"1px solid {theme.LINE}"}))
    return _table(header, rows)


def _isa_table(results, years, current, isa_limit):
    header = [_th("Year", "left"), _th("Limit"), _th("Done"), _th("Remaining")]
    rows = []
    for i, yr in enumerate(years):
        r = results.get(yr)
        if not r:
            continue
        done = r.get("isa", 0)
        # A closed year has no remaining allowance - it is gone, not unused.
        rem = 0 if yr < current else isa_limit - done
        rows.append(html.Tr([
            _year_cell(yr, current),
            _money(isa_limit),
            _money(done),
            _money(rem, colour=theme.POSITIVE if rem >= 0 else theme.NEGATIVE,
                   bold=True),
        ], style={"background": _row_bg(yr, current, i),
                  "borderBottom": f"1px solid {theme.LINE}"}))
    return _table(header, rows)


def _jisa_table(atlas, years, current, jisa_limit):
    header = [_th("Year", "left"), _th("Limit"), _th("Done"), _th("Future"),
              _th("Remaining")]
    rows = []
    for i, yr in enumerate(years):
        d = atlas.get(yr, {})
        done = d.get("jisa", 0) or 0
        future = d.get("jisa_future", 0) or 0
        rem = 0 if yr < current else jisa_limit - done - future
        rows.append(html.Tr([
            _year_cell(yr, current),
            _money(jisa_limit),
            html.Td(_num_input("atlas", "jisa", yr, done),
                    style={"padding": "4px 6px"}),
            html.Td(_num_input("atlas", "jisa_future", yr, future),
                    style={"padding": "4px 6px"}),
            _money(rem, colour=theme.POSITIVE if rem >= 0 else theme.NEGATIVE,
                   bold=True),
        ], style={"background": _row_bg(yr, current, i),
                  "borderBottom": f"1px solid {theme.LINE}"}))
    return _table(header, rows)


def _limits_card():
    df = repo.limits()
    return card("Limits by tax year", html.Div([
        dash_table.DataTable(
            id="alw-limits",
            columns=[
                {"name": "Tax year", "id": "tax_year"},
                {"name": "Pension allowance", "id": "pension_limit",
                 "type": "numeric"},
                {"name": "Car BIK rate", "id": "car_bik_rate",
                 "type": "numeric"},
                {"name": "JISA year (1/0)", "id": "jisa_year",
                 "type": "numeric"},
                {"name": "Show in tables (1/0)", "id": "show_in_tables",
                 "type": "numeric"},
            ],
            data=df.to_dict("records"),
            editable=True,
            row_deletable=False,
            style_table={"overflowX": "auto"},
            style_cell={"fontSize": "12.5px", "padding": "7px 10px",
                        "fontFamily": "inherit", "textAlign": "left"},
            style_header={"fontWeight": 600, "fontSize": "11.5px",
                          "textTransform": "uppercase",
                          "letterSpacing": "0.06em"},
        ),
        html.Div([
            html.Button("Save limits", id="alw-limits-save", n_clicks=0,
                        style=BTN),
        ], style={"marginTop": "12px"}),
        html.Div("Show in tables controls which years appear above. The ISA "
                 "and JISA limits, the P11D value and the current tax year "
                 "are in Data → Config.",
                 style={"color": theme.SLATE, "fontSize": "12px",
                        "marginTop": "10px"}),
    ]))


# --- Page ----------------------------------------------------------------

def render():
    data = repo.load()
    years = repo.tax_years()
    jyears = repo.jisa_years()
    current = repo.current_year()

    blocks = [dcc.Store(id="alw-store", data=data),
              html.Div(id="alw-msg")]

    for person, meta in PEOPLE.items():
        blocks.append(html.Div([
            card(f"{meta['label']} — inputs",
                 _input_table(person, data, years, current, meta["car"])),
            html.Div(id=f"alw-results-{person}"),
            html.Div(id=f"alw-isa-{person}"),
        ]))

    blocks.append(html.Div(id="alw-jisa"))
    blocks.append(_limits_card())
    return html.Div(blocks)


# --- Callbacks -----------------------------------------------------------

@callback(
    Output("alw-store", "data"),
    Input({"type": "alw", "person": ALL, "field": ALL, "year": ALL}, "value"),
    State({"type": "alw", "person": ALL, "field": ALL, "year": ALL}, "id"),
    State("alw-store", "data"),
    prevent_initial_call=True,
)
def collect(values, ids, data):
    """Write every input back into the blob, then persist.

    Values and ids come back in the same order, so each value is placed by its
    own id rather than by position in a hand-maintained list.
    """
    if not ids:
        return no_update
    data = data or {}
    for value, ident in zip(values, ids):
        person = data.setdefault(ident["person"], {})
        year = person.setdefault(ident["year"], {})
        year[ident["field"]] = value or 0
    repo.save(data)
    return data


@callback(
    Output("alw-results-ahmet", "children"),
    Output("alw-isa-ahmet", "children"),
    Output("alw-results-burcu", "children"),
    Output("alw-isa-burcu", "children"),
    Output("alw-jisa", "children"),
    Input("alw-store", "data"),
)
def recalc(data):
    data = data or {}
    years = repo.tax_years()
    jyears = repo.jisa_years()
    current = repo.current_year()
    limits = repo.pension_limits()
    rates = repo.car_bik_rates()
    p11d = repo.car_p11d()
    isa_limit = repo.isa_limit()
    jisa_limit = repo.jisa_limit()

    out = []
    for person, meta in PEOPLE.items():
        res = calc.carry_forward(data, person, years, limits, rates, p11d)
        out.append(card(f"{meta['label']} — pension allowance & carry forward",
                        _results_table(res, years, current)))
        out.append(card(f"{meta['label']} — ISA",
                        _isa_table(res, years, current, isa_limit)))

    jisa = card("Atlas — JISA",
                _jisa_table(data.get("atlas", {}), jyears, current,
                            jisa_limit))
    return out[0], out[1], out[2], out[3], jisa


@callback(
    Output("alw-msg", "children"),
    Input("alw-limits-save", "n_clicks"),
    State("alw-limits", "data"),
    prevent_initial_call=True,
)
def save_limits(_n, rows):
    n = repo.save_limits(rows or [])
    return html.Div(f"Saved {n} tax years. Reload the tab to pick up any "
                    f"change to which years are shown.", style={
        "padding": "10px 14px", "marginBottom": "12px", "borderRadius": "3px",
        "fontSize": "13px", "color": theme.POSITIVE,
        "border": f"1px solid {theme.POSITIVE}33",
        "background": f"{theme.POSITIVE}0D"})
