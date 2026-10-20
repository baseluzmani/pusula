"""
Fear & Greed zones, and a line split by them.

Pure calculation - no Dash, no SQL - so the page draws it and the tests check
it without either.

The bands are CNN's: below 25 extreme fear, 25 to 45 fear, 45 to 55 neutral,
55 to 75 greed, 75 and up extreme greed. CNN states them in whole numbers
(0-24, 25-44, 45-55, 56-75, 76-100); here they are half-open on the
continuous score, so the shading, the line colour and the word on the strip
always agree with each other. Within half a point of a boundary the word can
differ from CNN's, which is not a distinction worth acting on.
"""

from __future__ import annotations

import pandas as pd

ZONES = [
    (0.0, 25.0, "Extreme fear"),
    (25.0, 45.0, "Fear"),
    (45.0, 55.0, "Neutral"),
    (55.0, 75.0, "Greed"),
    (75.0, 100.0, "Extreme greed"),
]
LABELS = [z[2] for z in ZONES]
CUTS = [z[1] for z in ZONES[:-1]]          # 25, 45, 55, 75


def zone(score: float) -> str:
    """The band a score falls in."""
    for _lo, hi, label in ZONES[:-1]:
        if score < hi:
            return label
    return ZONES[-1][2]


def split_by_zone(series: pd.Series) -> dict:
    """{label: (xs, ys)} - one polyline per zone, ready for a trace each.

    Plotly colours a whole trace, not a segment, so a line that changes colour
    as it crosses 25 or 45 has to be drawn as several traces. Each step from
    one observation to the next is cut where it crosses a boundary, at the
    point found by straight-line interpolation in time, and each piece goes to
    the zone it lies in. The cut point belongs to the pieces on both sides, so
    they meet rather than leaving a gap at every crossing.

    Within a zone, pieces that follow on from each other are joined into one
    run; a run that resumes later starts after a None, which plotly draws as
    a break. That is what stops a zone's line jumping across the stretches of
    the chart it is not in.

    Only zones the line actually visits are returned.
    """
    out = {label: ([], []) for label in LABELS}
    s = series.dropna().astype(float)

    if len(s) == 1:
        xs, ys = out[zone(s.iloc[0])]
        xs.append(s.index[0])
        ys.append(s.iloc[0])

    pts = list(zip(s.index, s.values))
    for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
        crossed = [c for c in CUTS if min(v0, v1) < c < max(v0, v1)]
        if v1 < v0:
            crossed.reverse()              # met in the order the line falls
        stops = [(t0, v0)]
        stops += [(t0 + (t1 - t0) * ((c - v0) / (v1 - v0)), c)
                  for c in crossed]
        stops.append((t1, v1))

        for (a, va), (b, vb) in zip(stops, stops[1:]):
            xs, ys = out[zone((va + vb) / 2)]
            if xs and xs[-1] == a:         # carries on from the last piece
                xs.append(b)
                ys.append(vb)
                continue
            if xs:
                xs.append(None)
                ys.append(None)
            xs += [a, b]
            ys += [va, vb]

    return {label: xy for label, xy in out.items() if xy[0]}
