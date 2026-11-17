"""
core.help
=========

Reads the in-app help: plain Markdown files in ``help/`` at the repo root.
Nothing here knows about Dash; ui/help.py draws the drawer and the full page.

Files
-----
    help/<section>.md   one per top-bar page (portfolio.md, markets.md, ...)
    help/definitions.md the figures, in plain English
    help/system.md      how data gets in: jobs, schedule, where things live

Layout of a section file
------------------------
    # Portfolio                      section title (the top-bar name)
    One paragraph about the section.

    ## Transactions                  one heading per tab, exactly as the tab
                                     is labelled (that is how "This tab" finds
                                     it)
    First paragraph = the one-line summary used in "All pages".
    Further paragraphs = the rest of the entry.
    Terms: pnl, cost-gbp             optional line: definitions to link

    ### Transactions table {#table}  a table or panel on the tab. The {#id}
                                     is what a page's (i) icon points at:
                                     key "portfolio.transactions.table".

definitions.md and system.md use ``## Heading {#id}`` per entry; their keys
are "def.<id>" and "system.<id>".

Keys
----
    portfolio                        a section
    portfolio.transactions           a tab (slug of the heading)
    portfolio.transactions.table     a table or panel on that tab
    def.pnl                          a definition
    system.schedule                  a system entry

Files are re-read when they change on disk, so editing a help file needs no
restart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

HELP_DIR = Path(__file__).resolve().parents[1] / "help"

# Top-bar sections in their nav order, with the file each lives in.
SECTIONS = ["portfolio", "markets", "indicators", "spending", "etf", "data"]
SPECIAL = {"definitions": "def", "system": "system"}

_HEADING = re.compile(r"^(#{1,3})\s+(.*?)\s*(?:\{#([\w-]+)\})?\s*$")
_TERMS = re.compile(r"^\s*Terms:\s*(.+)$", re.I)


@dataclass
class Topic:
    key: str
    title: str
    level: int                    # 1 section, 2 tab / entry, 3 table / panel
    body: str = ""                # Markdown, without the Terms line
    terms: list = field(default_factory=list)
    parent: str | None = None     # key of the tab / section above it
    children: list = field(default_factory=list)

    @property
    def summary(self) -> str:
        """First paragraph, as plain text: the one-liner and the hover text
        of an (i) icon."""
        para = self.body.strip().split("\n\n", 1)[0] if self.body else ""
        text = re.sub(r"[*_`]", "", " ".join(para.split()))
        return re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)

    @property
    def section(self) -> str:
        return self.key.split(".", 1)[0]


def slug(text: str) -> str:
    """'P&L ROCI' -> 'pnl-roci', 'Run history' -> 'run-history'."""
    text = str(text or "").lower().replace("&", "n")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


# --- parsing --------------------------------------------------------------

def parse(text: str, prefix: str) -> dict[str, Topic]:
    """Topics of one file, keyed, in file order. ``prefix`` is the section
    name for a section file, or 'def' / 'system'."""
    topics: dict[str, Topic] = {}
    special = prefix in SPECIAL.values()
    current: Topic | None = None
    tab: Topic | None = None
    lines: list[str] = []

    def close():
        if current is None:
            return
        body, terms = [], []
        for line in lines:
            m = _TERMS.match(line)
            if m:
                terms += [t.strip() for t in m.group(1).split(",") if t.strip()]
            else:
                body.append(line)
        current.body = "\n".join(body).strip()
        current.terms = terms

    for line in text.splitlines():
        m = _HEADING.match(line)
        if not m:
            lines.append(line)
            continue
        close()
        lines = []
        level, title, anchor = len(m.group(1)), m.group(2).strip(), m.group(3)
        if level == 1:
            key, parent = prefix, None
            tab = None
        elif level == 2:
            key = f"{prefix}.{anchor or slug(title)}"
            parent = None if special else prefix
            tab = None
        else:
            owner = tab.key if tab else prefix
            key, parent = f"{owner}.{anchor or slug(title)}", owner
        current = Topic(key=key, title=title, level=level, parent=parent)
        if key in topics:
            raise ValueError(f"help: duplicate key {key}")
        topics[key] = current
        if level == 2 and not special:
            tab = current
        if parent and parent in topics:
            topics[parent].children.append(key)
    close()
    return topics


_CACHE: dict = {"stamp": None, "topics": {}}


def _files() -> list[tuple[Path, str]]:
    out = [(HELP_DIR / f"{s}.md", s) for s in SECTIONS]
    out += [(HELP_DIR / f"{name}.md", prefix)
            for name, prefix in SPECIAL.items()]
    return out


def topics() -> dict[str, Topic]:
    """Every topic, keyed. Re-read when any help file changes."""
    files = [(p, pre) for p, pre in _files() if p.exists()]
    stamp = tuple((str(p), p.stat().st_mtime_ns) for p, _ in files)
    if stamp != _CACHE["stamp"]:
        merged: dict[str, Topic] = {}
        for path, prefix in files:
            merged.update(parse(path.read_text(encoding="utf-8"), prefix))
        _CACHE.update(stamp=stamp, topics=merged)
    return _CACHE["topics"]


def get(key: str | None) -> Topic | None:
    return topics().get(key or "")


def tabs(section: str) -> list[Topic]:
    sec = get(section)
    return [get(k) for k in (sec.children if sec else [])]


def definitions() -> list[Topic]:
    return [t for k, t in topics().items() if k.startswith("def.")]


def system() -> list[Topic]:
    return [t for k, t in topics().items() if k.startswith("system.")]


def section_for_path(path: str | None) -> str | None:
    """Top-bar section of a URL path: '/' -> 'portfolio', '/etf' -> 'etf'."""
    p = str(path or "/").split("?", 1)[0].strip("/").split("/", 1)[0]
    p = p or "portfolio"
    return p if p in SECTIONS else None


def topic_for(path: str | None, tab_label: str | None) -> str | None:
    """Key of the open tab, from the URL and the selected tab's label; the
    section if the tab has no entry; None off the six sections."""
    section = section_for_path(path)
    if section is None:
        return None
    label = " ".join(str(tab_label or "").split()).lower()
    for t in tabs(section):
        if t.title.lower() == label:
            return t.key
    return section


def breadcrumb(key: str | None) -> list[Topic]:
    """Section > tab > table for a key, outermost first."""
    out = []
    t = get(key)
    while t is not None:
        out.insert(0, t)
        t = get(t.parent) if t.parent else None
    return out


def search(query: str, limit: int = 30) -> list[Topic]:
    """Topics whose title or text contains every word of the query; title
    hits first."""
    words = [w for w in str(query or "").lower().split() if w]
    if not words:
        return []
    title_hits, body_hits = [], []
    for t in topics().values():
        title = t.title.lower()
        text = f"{title} {t.body.lower()} {' '.join(t.terms)}"
        if all(w in text for w in words):
            (title_hits if all(w in title for w in words)
             else body_hits).append(t)
    return (title_hits + body_hits)[:limit]


def problems(section_tabs: dict[str, list[str]] | None = None) -> list[str]:
    """What is missing or broken: a section tab with no entry, a Terms link
    to no definition. ``section_tabs`` maps section -> tab labels as the app
    shows them. The test suite calls this so a new tab cannot ship without
    help."""
    out = []
    all_topics = topics()
    for section, labels in (section_tabs or {}).items():
        have = {t.title.lower() for t in tabs(section)}
        for label in labels:
            if label.lower() not in have:
                out.append(f"{section}: no help for tab '{label}'")
    for t in all_topics.values():
        for term in t.terms:
            if f"def.{term}" not in all_topics:
                out.append(f"{t.key}: Terms '{term}' has no definition")
        if t.level >= 2 and not t.summary:
            out.append(f"{t.key}: empty entry")
    return out
