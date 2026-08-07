"""
Data -> ETF upload.

Drag provider holdings files in rather than copying them to a folder over SSH.
Each file reports its own outcome, because a batch is usually several ETFs and
one unreadable file should not leave you guessing which.

Files land in the configured input folder first and are imported from there,
so the path is identical to the folder run - the upload is a way of getting
files into that folder, not a second import route that could drift from it.
"""

import base64
from pathlib import Path

from dash import html, dcc, callback, Input, Output, State, no_update

from core import config, theme, db
from core.repo import tickers as ticker_repo
from importers import etf_holdings
from ui.layout import card

ALLOWED = {".csv", ".xls", ".xlsx"}

STATUS_COLOUR = {"imported": theme.POSITIVE, "skipped": theme.NEUTRAL,
                 "error": theme.NEGATIVE}


def _known_prefixes():
    """Prefix -> fund_id, so the screen can say what it will accept."""
    return ticker_repo.fund_id_map()


def render():
    prefixes = _known_prefixes()
    providers = ticker_repo.etf_provider_map()

    rows = []
    for prefix in sorted(prefixes):
        rows.append(html.Tr([
            html.Td(prefix, style={"padding": "4px 10px", "fontSize": "12px",
                                   **theme.NUM}),
            html.Td(prefixes[prefix], style={"padding": "4px 10px",
                                             "fontSize": "12px",
                                             "color": theme.SLATE}),
            html.Td(providers.get(prefix, "ishares"),
                    style={"padding": "4px 10px", "fontSize": "12px",
                           "color": theme.SLATE}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))

    return html.Div([
        card("Upload holdings files", html.Div([
            dcc.Upload(
                id="etf-upload",
                multiple=True,
                accept=".csv,.xls,.xlsx",
                children=html.Div([
                    html.Div("Drop files here, or click to choose",
                             style={"fontSize": "13px", "fontWeight": 600,
                                    "color": theme.INK}),
                    html.Div("CSV, XLS or XLSX. The filename prefix picks the "
                             "ETF: AINF_20260610.csv → AINF.",
                             style={"fontSize": "12px", "color": theme.SLATE,
                                    "marginTop": "4px"}),
                ]),
                style={
                    "border": f"1.5px dashed {theme.LINE}",
                    "borderRadius": "6px", "padding": "28px 20px",
                    "textAlign": "center", "cursor": "pointer",
                    "background": theme.SURFACE,
                },
            ),
            dcc.Loading(html.Div(id="etf-upload-result",
                                 style={"marginTop": "14px"}),
                        type="default"),
        ])),

        card("Or import what is already in the folder", html.Div([
            html.Div(str(config.IMPORT_DIR),
                     style={"fontSize": "12px", "color": theme.SLATE,
                            **theme.NUM, "marginBottom": "10px"}),
            html.Button("Import folder", id="etf-import-folder", n_clicks=0,
                        style={"padding": "7px 14px",
                               "border": f"1px solid {theme.LINE}",
                               "borderRadius": "3px",
                               "background": theme.SURFACE,
                               "cursor": "pointer", "fontSize": "12.5px",
                               "fontWeight": 500, "color": theme.INK}),
        ])),

        card(f"Recognised prefixes ({len(prefixes)})", html.Div([
            html.Div("A file whose prefix is not here is rejected rather than "
                     "guessed at. Add an instrument with that source_id and a "
                     "provider under Data → Instruments.",
                     style={"fontSize": "12px", "color": theme.SLATE,
                            "marginBottom": "10px"}),
            html.Table([
                html.Thead(html.Tr([
                    html.Th(h, style={"textAlign": "left",
                                      "padding": "6px 10px",
                                      "fontSize": "11px", "fontWeight": 600,
                                      "textTransform": "uppercase",
                                      "letterSpacing": "0.05em",
                                      "color": theme.SLATE,
                                      "borderBottom": f"1px solid {theme.LINE}"})
                    for h in ("Prefix", "Fund ID", "Provider")])),
                html.Tbody(rows),
            ], style={"width": "100%", "borderCollapse": "collapse"}),
        ])),
    ])


def _result_table(result):
    if not result.get("files"):
        return html.Div(result.get("message", "Nothing to do."),
                        style={"fontSize": "13px", "color": theme.SLATE})

    rows = []
    for r in result["files"]:
        colour = STATUS_COLOUR.get(r["status"], theme.NEUTRAL)
        rows.append(html.Tr([
            html.Td(r["file"], style={"padding": "6px 10px",
                                      "fontSize": "12.5px", **theme.NUM}),
            html.Td(r["status"], style={"padding": "6px 10px",
                                        "fontSize": "11.5px",
                                        "fontWeight": 600,
                                        "textTransform": "uppercase",
                                        "letterSpacing": "0.05em",
                                        "color": colour}),
            html.Td(r.get("fund_id") or "—",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "color": theme.SLATE, **theme.NUM}),
            html.Td(r.get("date") or "—",
                    style={"padding": "6px 10px", "fontSize": "12px",
                           "color": theme.SLATE, **theme.NUM}),
            html.Td(r["message"], style={"padding": "6px 10px",
                                         "fontSize": "12px",
                                         "color": theme.SLATE}),
        ], style={"borderBottom": f"1px solid {theme.LINE}"}))

    header = html.Thead(html.Tr([
        html.Th(h, style={"textAlign": "left", "padding": "6px 10px",
                          "fontSize": "11px", "fontWeight": 600,
                          "textTransform": "uppercase",
                          "letterSpacing": "0.05em", "color": theme.SLATE,
                          "borderBottom": f"1px solid {theme.LINE}"})
        for h in ("File", "Status", "Fund", "As of", "Detail")]))

    parts = [
        html.Div(result["message"], style={
            "padding": "10px 14px", "marginBottom": "12px",
            "borderRadius": "3px", "fontSize": "13px",
            "color": theme.NEGATIVE if result.get("failed")
                     else theme.POSITIVE,
            "border": f"1px solid {theme.LINE}",
            "background": theme.SURFACE}),
        html.Table([header, html.Tbody(rows)],
                   style={"width": "100%", "borderCollapse": "collapse"}),
    ]

    figi_result = result.get("figi") or {}
    if figi_result:
        unresolved = figi_result.get("unresolved", 0)
        sample = figi_result.get("unresolved_sample") or []
        line = (f"Tickers resolved: {figi_result.get('resolved', 0)}"
                f" · unresolved: {unresolved}")
        if figi_result.get("isin_resolved") or figi_result.get(
                "isin_unresolved"):
            line += (f" · by ISIN: {figi_result.get('isin_resolved', 0)}"
                     f" resolved, {figi_result.get('isin_unresolved', 0)}"
                     f" unresolved")
        parts.append(html.Div(line, style={
            "marginTop": "12px", "fontSize": "12.5px", "color": theme.SLATE}))
        if sample:
            parts.append(html.Div(
                "Needs review in Ticker Map: " + ", ".join(str(s)
                                                           for s in sample),
                style={"marginTop": "4px", "fontSize": "12px",
                       "color": theme.NEEDLE}))

    return html.Div(parts)


@callback(
    Output("etf-upload-result", "children"),
    Input("etf-upload", "contents"),
    Input("etf-import-folder", "n_clicks"),
    State("etf-upload", "filename"),
    prevent_initial_call=True,
)
def handle(contents, _clicks, filenames):
    from dash import ctx

    if ctx.triggered_id == "etf-import-folder":
        return _result_table(etf_holdings.run())

    if not contents or not filenames:
        return no_update

    import_dir = Path(config.IMPORT_DIR)
    import_dir.mkdir(parents=True, exist_ok=True)

    saved_paths, rejected = [], []
    for content, name in zip(contents, filenames):
        # Filenames come from the browser: strip any path before using one.
        safe = Path(name).name
        if Path(safe).suffix.lower() not in ALLOWED:
            rejected.append({"file": safe, "status": "error", "rows": 0,
                             "message": "Not a CSV, XLS or XLSX"})
            continue
        try:
            _header, b64 = content.split(",", 1)
            target = import_dir / safe
            target.write_bytes(base64.b64decode(b64))
            saved_paths.append(target)
        except Exception as exc:                               # noqa: BLE001
            rejected.append({"file": safe, "status": "error", "rows": 0,
                             "message": f"Could not save upload: {exc}"})

    if not saved_paths:
        return _result_table({"saved": 0, "files": rejected,
                              "failed": len(rejected),
                              "message": "Nothing imported"})

    result = etf_holdings.import_paths(saved_paths)
    result["files"] = rejected + result["files"]
    result["failed"] = result.get("failed", 0) + len(rejected)
    return _result_table(result)
