"""
In-app help: every tab has an entry, every (i) icon points at one, every
Terms link has a definition, and every entry draws.

A new tab or icon without help fails here - that is the point.

    python -m pytest tests/test_help.py -q
"""
import ast
import re
from pathlib import Path

import pytest

from core import help as content

ROOT = Path(__file__).resolve().parents[1]
SHELLS = {"portfolio": "portfolio", "markets": "markets",
          "indicators": "indicators", "spending": "spending", "etf": "etf",
          "data": "data"}


def _tab_labels(module: str) -> list[str]:
    """TABS from pages/<module>.py, read from the source (importing a page
    registers it with Dash, which needs an app)."""
    tree = ast.parse((ROOT / "pages" / f"{module}.py").read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "TABS" for t in node.targets)):
            v = node.value
            items = v.keys if isinstance(v, ast.Dict) else v.elts
            return [i.value for i in items if isinstance(i, ast.Constant)]
    raise AssertionError(f"no TABS in pages/{module}.py")


def _icon_keys() -> set[str]:
    """Every help key named in pages/ and ui/: help_title / help_icon
    arguments, help_key=..., and lookup tables such as Indicators'
    PANEL_HELP. A key is any string literal shaped like one."""
    shape = re.compile(r"(%s)(\.[a-z0-9-]+){1,2}" % "|".join(content.SECTIONS))
    keys = set()
    for path in list((ROOT / "pages").glob("*.py")) + \
            list((ROOT / "ui").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and shape.fullmatch(node.value)):
                keys.add(node.value)
    return keys


def test_every_indicators_card_has_an_icon():
    src = (ROOT / "pages" / "indicators.py").read_text()
    titles = set(re.findall(r'_card\("([^"]+)"', src))
    tree = ast.parse(src)
    mapped = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", None) == "PANEL_HELP"):
            mapped = {k.value for k in node.value.keys}
    assert titles and titles <= mapped, titles - mapped


def test_every_tab_has_help():
    tabs = {section: _tab_labels(module)
            for section, module in SHELLS.items()}
    assert all(tabs.values())
    assert content.problems(tabs) == []


def test_every_icon_points_at_an_entry():
    keys = _icon_keys()
    assert len(keys) > 80, "help icons not found"
    missing = sorted(k for k in keys if content.get(k) is None)
    assert missing == []


def test_portfolio_tables_have_entries():
    pnl_roci = content.get("portfolio.pnl-roci")
    assert "portfolio.pnl-roci.table" in pnl_roci.children
    assert content.get("portfolio.portfolio.holdings-detail").summary


def test_topic_for():
    assert content.topic_for("/", "P&L ROCI") == "portfolio.pnl-roci"
    assert content.topic_for("/", " Transactions ") == \
        "portfolio.transactions"
    assert content.topic_for("/markets", "Transactions") == \
        "markets.transactions"
    assert content.topic_for("/data", "ETF targets") == "data.etf-targets"
    assert content.topic_for("/spending", "Nope") == "spending"
    assert content.topic_for("/help", "x") is None


def test_parse_keys_terms_and_duplicates():
    text = ("# Demo\nIntro.\n\n## First tab\nOne line *bold*.\n\nMore.\n"
            "Terms: pnl\n\n### A table {#tbl}\nTable text.\n")
    t = content.parse(text, "demo")
    assert list(t) == ["demo", "demo.first-tab", "demo.first-tab.tbl"]
    tab = t["demo.first-tab"]
    assert tab.summary == "One line bold."
    assert tab.terms == ["pnl"] and "Terms" not in tab.body
    assert tab.children == ["demo.first-tab.tbl"]
    with pytest.raises(ValueError):
        content.parse("# D\n## Same\nx\n## Same\ny\n", "d")


def test_search():
    keys = [t.key for t in content.search("drawdown")]
    assert "def.max-drawdown" in keys
    assert content.search("") == []
    assert content.search("zzzz-nothing") == []


def test_every_entry_draws():
    from ui import help as ui
    for key in content.topics():
        for mode in ("drawer", "page"):
            assert ui.render_topic(key, mode) is not None
    assert ui.render_pages() is not None
    assert ui.render_definitions("def.pnl") is not None
    assert ui.render_system() is not None
    assert ui.render_search("pnl", "page") is not None
    assert ui.render_view("tab", "__pages__") is not None
    assert ui.render_topic("no.such.key") is not None
