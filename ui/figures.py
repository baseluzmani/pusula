"""
Shared Plotly styling. Every chart in the app calls base_layout() so figures
inherit the same fonts, grid colours and margins instead of Plotly defaults.
"""
import plotly.graph_objects as go

from core import theme

SEQUENCE = [theme.INK, "#2E75B6", theme.NEEDLE, theme.POSITIVE, "#7E6BA8",
            "#C0785A", "#4A8FA8", "#8A9A5B", "#B0567F", "#5C7A99"]


def base_layout(**overrides) -> dict:
    layout = dict(
        paper_bgcolor=theme.SURFACE,
        plot_bgcolor=theme.SURFACE,
        font=dict(family=theme.FONT_UI, size=11, color=theme.TEXT),
        margin=dict(l=40, r=20, t=16, b=48),
        hoverlabel=dict(font_size=12, font_family=theme.FONT_UI),
        xaxis=dict(gridcolor=theme.LINE_SOFT, zerolinecolor=theme.LINE),
        yaxis=dict(gridcolor=theme.LINE_SOFT, zerolinecolor=theme.LINE),
    )
    layout.update(overrides)
    return layout


def empty(message: str = "No data", height: int = 280) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**base_layout(
        height=height,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        annotations=[dict(text=message, showarrow=False,
                          font=dict(size=13, color=theme.NEUTRAL))],
    ))
    return fig


HEATMAP_SCALE = [[0, "#F4F8FC"], [0.45, "#8FB8DC"], [1.0, theme.INK]]
