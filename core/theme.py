"""
Design tokens. Every colour and spacing value used anywhere in pages/ comes
from here, so changing the look means editing one file.
"""

# --- Palette -------------------------------------------------------------
# Dark "chrome" (nav bar) with a light working surface, the way a trading or
# reporting terminal separates instrument from content.
INK        = "#111A2B"   # nav bar, headings
INK_SOFT   = "#1E2A42"   # nav hover, borders on dark
SLATE      = "#5A6884"   # secondary text
LINE       = "#DDE3EC"   # hairline borders on light
SURFACE    = "#FFFFFF"   # cards
CANVAS     = "#F5F7FA"   # page background
NEEDLE     = "#C9922E"   # accent: the compass needle. Use sparingly.
POSITIVE   = "#1B7F5A"
NEGATIVE   = "#B03A34"
NEUTRAL    = "#8A93A6"

# --- Type ----------------------------------------------------------------
FONT_UI = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
           "'Helvetica Neue', Arial, sans-serif")
# Numbers get a monospace face with tabular figures so columns of money
# line up. In a risk tool this is functional, not decorative.
FONT_NUM = "'SF Mono', 'JetBrains Mono', 'Cascadia Mono', Consolas, monospace"

# --- Reusable style dicts ------------------------------------------------
CARD = {
    "backgroundColor": SURFACE,
    "border": f"1px solid {LINE}",
    "borderRadius": "4px",
    "padding": "18px 20px",
    "marginBottom": "16px",
}

CARD_TITLE = {
    "fontSize": "11px",
    "fontWeight": 600,
    "letterSpacing": "0.09em",
    "textTransform": "uppercase",
    "color": SLATE,
    "marginBottom": "12px",
}

PAGE = {
    "padding": "24px 28px",
    "maxWidth": "1600px",
    "margin": "0 auto",
}

H1 = {
    "fontSize": "22px",
    "fontWeight": 600,
    "color": INK,
    "margin": "0 0 4px 0",
    "letterSpacing": "-0.01em",
}

SUBTITLE = {
    "fontSize": "13px",
    "color": SLATE,
    "margin": "0 0 22px 0",
}

NUM = {
    "fontFamily": FONT_NUM,
    "fontVariantNumeric": "tabular-nums",
}


def money(value: float, currency: str = "£", dp: int = 0) -> str:
    """Format a number for display. Negative values in brackets, City style."""
    if value is None:
        return "–"
    s = f"{abs(value):,.{dp}f}"
    return f"({currency}{s})" if value < 0 else f"{currency}{s}"


def pct(value: float, dp: int = 1) -> str:
    if value is None:
        return "–"
    return f"{value:,.{dp}f}%"


def colour_for(value: float) -> str:
    if value is None:
        return NEUTRAL
    return POSITIVE if value > 0 else NEGATIVE if value < 0 else NEUTRAL
