"""
Design tokens. Every colour and spacing value used anywhere in pages/ comes
from here, so changing the look means editing one file.
"""

# --- Palette -------------------------------------------------------------
# Dark chrome (top bar only) over a light, quiet working surface.
INK        = "#0F1826"   # top bar
INK_SOFT   = "#1C2839"   # top bar hover
TEXT       = "#1F2937"   # body text (softer than the bar)
SLATE      = "#6B7891"   # secondary text
LINE       = "#E8ECF2"   # hairline borders
LINE_SOFT  = "#F0F3F7"   # table row separators
SURFACE    = "#FFFFFF"   # cards
CANVAS     = "#FAFBFC"   # page background
NEEDLE     = "#B8860F"   # accent: the compass needle. Use sparingly.
POSITIVE   = "#127A54"
NEGATIVE   = "#A63A34"
NEUTRAL    = "#98A1B3"

# --- Charts --------------------------------------------------------------
CHART_HELD  = "#2A78D6"   # marks for positions you hold
CHART_OTHER = NEUTRAL     # marks for everything else in the universe
CHART_ZERO  = "#CBD2DA"   # zero / reference lines
TINT_NEAR   = "#FFF4D6"   # Monitor: a level the price is close to
TINT_HIT    = "#FFDFA8"   # Monitor: a level the price has reached

# Indicators: CNN Fear & Greed zones. The ends reuse POSITIVE and NEGATIVE so
# "extreme" reads as the strongest version of colours used everywhere else.
ZONE_EXTREME_FEAR  = NEGATIVE
ZONE_FEAR          = "#C9822B"
ZONE_NEUTRAL       = NEUTRAL
ZONE_GREED         = "#4E9F7A"
ZONE_EXTREME_GREED = POSITIVE

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
    "borderRadius": "6px",
    "padding": "20px 22px",
    "marginBottom": "18px",
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
    "padding": "26px 30px",
    "maxWidth": "1600px",
    "margin": "0 auto",
}

PAGE_TIGHT = {
    "padding": "10px",
    "maxWidth": "1600px",
    "margin": "0 auto",
}

H1 = {
    "fontSize": "20px",
    "fontWeight": 600,
    "color": TEXT,
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

def heat_rgb(val, cap=3.0):
    """Diverging cell background: red (neg) -> white (0) -> green (pos)."""
    if val is None or (isinstance(val, float) and val != val):
        return "rgb(244,246,249)"
    v = max(-1.0, min(1.0, val / cap))
    if v >= 0:
        r = int(255 - v * 170); g = 255; b = int(255 - v * 150)
    else:
        r = 255; g = int(255 + v * 150); b = int(255 + v * 170)
    return f"rgb({r},{g},{b})"


# Day-move map tiles carry white text, so the scale runs between the two
# signal colours through a dark neutral rather than through white. Every step
# keeps white text above 5:1 contrast.
TILE_MID = "#5B6472"


def tile_rgb(val, cap=3.0):
    """Diverging tile fill: NEGATIVE (at -cap) -> TILE_MID (0) -> POSITIVE
    (at +cap), linear in between."""
    if val is None or (isinstance(val, float) and val != val):
        return TILE_MID
    v = max(-1.0, min(1.0, val / cap))
    end = POSITIVE if v >= 0 else NEGATIVE
    a = [int(TILE_MID[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(end[i:i + 2], 16) for i in (1, 3, 5)]
    t = abs(v)
    return "#" + "".join(f"{round(x + (y - x) * t):02X}" for x, y in zip(a, b))
