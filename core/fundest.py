"""
Indicative daily return of a fund from what it holds.

Pure calculation - frames in, frames out. No Dash, no SQL; the data comes
from core/repo/fundest.py.

The estimate
------------
Each holding's move on the date, in GBP:

    gbp = (1 + local) / (1 + fx) - 1

local is the close-to-close change of its Yahoo price (0 when it has no
price on the date - a market that has not opened, or a holiday), fx is the
change in GBP<CCY>=X, so a weaker pound lifts a dollar holding. A holding
with no price at all is left out and its weight treated as uncovered.

The fund's move is the weighted average over the holdings that can be
priced ("scaled": the covered names stand in for the rest), or - when a
residual proxy is set - the covered names at their own weights plus the
proxy for everything else:

    scaled   sum(w * r) / sum(w)
    proxy    (sum(w * r) + (100 - sum(w)) * r_proxy) / 100

Contributions are shown so that they add up to the estimate.

The backtest
------------
The same weights applied to each past day, compared with the fund's own
price moves. Funds priced once a day are often struck the next morning (a
noon valuation sees the previous US close), so each estimate is compared
both with the fund's move on the same date and with its next move. The
alignment with the smaller error is the one to read the estimate against.
Today's weights are used for every past day, which is fine for a few months.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

GBP_CODES = ("GBP", "GBX")
SAME = 1e-9                     # a move smaller than this is no move
BACKTEST_DAYS = 60

STATUS_PRICED = "priced"
STATUS_NO_MOVE = "no price on date"
STATUS_NO_PRICES = "no prices"
STATUS_MISSING = "not in instruments"
STATUS_UNMAPPED = "no Yahoo symbol"


def fx_fund_id(currency) -> str | None:
    """YF:GBP<CCY>=X for a non-sterling currency, None for sterling."""
    code = str(currency or "").strip().upper()
    if not code or code in GBP_CODES:
        return None
    return f"YF:GBP{code}=X"


def return_frames(closes: pd.DataFrame, also=None
                  ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    From a long frame (fund_id, date, close): wide daily returns and a mask
    of which funds had any earlier price.

    ret[D, f]    close on D over the last close before D, minus 1. NaN when
                 f has no row on D.
    known[D, f]  True when f has a price before D, i.e. a move on D can be
                 measured (and a missing row on D means it did not trade).

    ``also`` adds a date to the index even if nothing has a price on it, so
    asking about a day before any market has opened gives "no move yet"
    rather than "no data".
    """
    if closes is None or closes.empty:
        empty = pd.DataFrame()
        return empty, empty
    df = closes.assign(date=pd.to_datetime(closes["date"]),
                       close=pd.to_numeric(closes["close"], errors="coerce"))
    wide = (df.pivot_table(index="date", columns="fund_id", values="close",
                           aggfunc="last")
              .sort_index())
    if also is not None:
        wide = wide.reindex(wide.index.union([pd.Timestamp(also)]))
    prev = wide.ffill().shift(1)
    ret = wide / prev - 1
    return ret, prev.notna()


def gbp_returns(ret: pd.DataFrame, known: pd.DataFrame,
                currency_of: dict, fund_ids: list[str]) -> tuple[
                    pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Local, FX and GBP returns for ``fund_ids`` on every date of ``ret``.

    local is 0 where the fund did not trade but had traded before, NaN
    where it has no history yet. fx is 0 on a date the cross has no row.
    """
    idx = ret.index
    local = pd.DataFrame(index=idx, columns=fund_ids, dtype=float)
    fx = pd.DataFrame(0.0, index=idx, columns=fund_ids)
    for f in fund_ids:
        if f in ret.columns:
            r = ret[f]
            local[f] = r.where(r.notna(), np.where(known[f], 0.0, np.nan))
        cross = fx_fund_id(currency_of.get(f))
        if cross and cross in ret.columns:
            fx[f] = ret[cross].fillna(0.0)
    gbp = (1 + local) / (1 + fx) - 1
    return local, fx, gbp


def _row(frame: pd.DataFrame, when) -> pd.Series:
    if frame is None or frame.empty or when not in frame.index:
        return pd.Series(dtype=float)
    return frame.loc[when]


def estimate(holdings: pd.DataFrame, closes: pd.DataFrame, currency_of: dict,
             known_ids: set, as_of, residual_id: str | None = None,
             hedged: bool = False) -> tuple[dict, pd.DataFrame]:
    """
    One fund's indicative move on ``as_of``.

    holdings: name, fund_id (None if unmapped), weight_pct
    closes:   long prices for the holdings, the FX crosses and the proxy
    currency_of: fund_id -> currency
    known_ids: fund_ids present in instruments

    Returns (summary, detail). Summary values are fractions (0.0076 = 0.76%):
    local, fx, gbp, headline (local if hedged else gbp), listed_weight,
    priced_weight, moved_weight, method, priced, total. Detail has one row
    per holding plus, in proxy mode, a "Rest of fund" row; its fx is the
    currency effect on the holding, so (1 + local) * (1 + fx) = 1 + gbp.
    """
    as_of = pd.Timestamp(as_of)
    ids = [f for f in holdings["fund_id"].dropna().unique()]
    ret, known = return_frames(closes, also=as_of)
    extra = [residual_id] if residual_id else []
    local_f, fx_f, gbp_f = (gbp_returns(ret, known, currency_of, ids + extra)
                            if not ret.empty else (None, None, None))
    local, fx, gbp = (_row(local_f, as_of), _row(fx_f, as_of),
                      _row(gbp_f, as_of))
    traded = _row(ret, as_of)

    rows = []
    for h in holdings.itertuples():
        # pandas turns a missing id into NaN, which is truthy.
        f = h.fund_id if isinstance(h.fund_id, str) and h.fund_id else None
        w = float(h.weight_pct or 0)
        if not f:
            status = STATUS_UNMAPPED
        elif f not in known_ids:
            status = STATUS_MISSING
        elif f not in local.index or pd.isna(local.get(f)):
            status = STATUS_NO_PRICES
        elif pd.isna(traded.get(f)):
            status = STATUS_NO_MOVE
        else:
            status = STATUS_PRICED
        ok = status in (STATUS_PRICED, STATUS_NO_MOVE)
        rows.append({
            "name": h.name, "fund_id": f, "weight_pct": w,
            "currency": currency_of.get(f) if f else None,
            "local": float(local[f]) if ok else np.nan,
            # The currency's effect on the holding in GBP, not the move of
            # the cross: a weaker pound is a positive effect.
            "fx": float(1 / (1 + fx[f]) - 1) if ok else np.nan,
            "gbp": float(gbp[f]) if ok else np.nan,
            "status": status, "_ok": ok,
            "_moved": status == STATUS_PRICED,
        })
    detail = pd.DataFrame(rows)

    listed = float(detail["weight_pct"].sum()) if not detail.empty else 0.0
    covered = detail[detail["_ok"]] if not detail.empty else detail
    priced_w = float(covered["weight_pct"].sum()) if not covered.empty else 0.0
    moved_w = (float(detail.loc[detail["_moved"], "weight_pct"].sum())
               if not detail.empty else 0.0)

    proxy_ok = bool(residual_id) and residual_id in local.index \
        and pd.notna(local.get(residual_id))
    summary = {"listed_weight": listed, "priced_weight": priced_w,
               "moved_weight": moved_w,
               "priced": int(len(covered)), "total": int(len(detail)),
               "local": np.nan, "fx": np.nan, "gbp": np.nan,
               "headline": np.nan, "method": "-"}
    if priced_w <= 0:
        detail["contribution"] = np.nan
        return summary, _tidy(detail)

    if proxy_ok:
        rest = max(0.0, 100.0 - priced_w)
        denom = 100.0
        summary["method"] = f"proxy {residual_id.replace('YF:', '')}"
    else:
        rest = 0.0
        denom = priced_w
        summary["method"] = "scaled" if priced_w < 99.5 else "full"

    detail["contribution"] = np.where(
        detail["_ok"], detail["weight_pct"] * detail["gbp"] / denom, np.nan)
    for key in ("local", "gbp"):
        part = (covered["weight_pct"] * covered[key]).sum()
        if proxy_ok:
            part += rest * float((local if key == "local" else gbp)[residual_id])
        summary[key] = part / denom
    summary["fx"] = summary["gbp"] - summary["local"]
    summary["headline"] = summary["local"] if hedged else summary["gbp"]

    if proxy_ok and rest > 0:
        p_local = float(local[residual_id])
        p_gbp = float(gbp[residual_id])
        detail = pd.concat([detail, pd.DataFrame([{
            "name": f"Rest of fund ({residual_id.replace('YF:', '')})",
            "fund_id": residual_id, "weight_pct": rest,
            "currency": currency_of.get(residual_id),
            "local": p_local, "fx": (1 + p_gbp) / (1 + p_local) - 1,
            "gbp": p_gbp, "status": "proxy", "_ok": True, "_moved": True,
            "contribution": rest * p_gbp / denom}])], ignore_index=True)
    return summary, _tidy(detail)


def _tidy(detail: pd.DataFrame) -> pd.DataFrame:
    cols = ["name", "fund_id", "currency", "weight_pct", "local", "fx", "gbp",
            "contribution", "status"]
    if detail.empty:
        return pd.DataFrame(columns=cols)
    return detail[cols].reset_index(drop=True)


# --- Backtest -------------------------------------------------------------

def actual_returns(closes: pd.DataFrame, fund_id: str,
                   currency: str | None, price_unit: str | None,
                   hedged: bool = False) -> pd.Series:
    """
    The fund's own moves, in GBP unless hedged, on the dates it has a price.
    A move smaller than SAME is dropped: an FT fund that repeats yesterday's
    price has not moved, it has not been priced yet.
    """
    if closes is None or closes.empty or not fund_id:
        return pd.Series(dtype=float)
    ret, known = return_frames(closes)
    if fund_id not in ret.columns:
        return pd.Series(dtype=float)
    r = ret[fund_id].dropna()
    cross = fx_fund_id(currency)
    if cross and not hedged and cross in ret.columns:
        fx = ret[cross].reindex(r.index).fillna(0.0)
        r = (1 + r) / (1 + fx) - 1
    return r[r.abs() > SAME]


def backtest(holdings: pd.DataFrame, closes: pd.DataFrame, currency_of: dict,
             known_ids: set, actual: pd.Series,
             residual_id: str | None = None, hedged: bool = False,
             days: int = BACKTEST_DAYS) -> pd.DataFrame:
    """
    Daily estimates over the window, beside the fund's actual moves.

    Columns: date, est, same (the fund's move on that date), next (its next
    move after that date). Fractions. Only dates on which at least one
    holding traded are estimated.
    """
    cols = ["date", "est", "same", "next"]
    ret, known = return_frames(closes)
    if ret.empty:
        return pd.DataFrame(columns=cols)
    h = holdings[holdings["fund_id"].isin(known_ids)
                 & holdings["fund_id"].isin(ret.columns)]
    if h.empty:
        return pd.DataFrame(columns=cols)
    w = h.groupby("fund_id")["weight_pct"].sum()
    ids = list(w.index)
    extra = [residual_id] if residual_id and residual_id in ret.columns else []
    local, _, gbp = gbp_returns(ret, known, currency_of, ids + extra)
    move = local if hedged else gbp

    traded = ret[ids].notna().any(axis=1)
    dates = move.index[traded]
    m = move.loc[dates, ids]
    present = m.notna()
    wsum = present.mul(w, axis=1).sum(axis=1)
    covered = m.fillna(0).mul(w, axis=1).sum(axis=1)
    if extra:
        rest = (100 - wsum).clip(lower=0)
        est = (covered + rest * move.loc[dates, residual_id].fillna(0)) / 100
    else:
        est = covered / wsum.replace(0, np.nan)
    est = est.dropna()

    act = actual.sort_index() if actual is not None else pd.Series(dtype=float)
    same = act.reindex(est.index)
    if act.empty:
        nxt = pd.Series(np.nan, index=est.index)
    else:
        pos = act.index.searchsorted(est.index, side="right")
        nxt = pd.Series([act.iloc[p] if p < len(act) else np.nan
                         for p in pos], index=est.index)
    out = pd.DataFrame({"date": est.index, "est": est.values,
                        "same": same.values, "next": nxt.values})
    return out.tail(days).reset_index(drop=True)


def fit(bt: pd.DataFrame) -> dict:
    """Mean absolute error and correlation of the estimate against each
    alignment, and which alignment fits better. Errors are fractions."""
    out = {"best": None}
    for key in ("same", "next"):
        pair = bt[["est", key]].dropna() if not bt.empty else bt
        n = int(len(pair))
        mae = float((pair["est"] - pair[key]).abs().mean()) if n else np.nan
        corr = (float(pair["est"].corr(pair[key]))
                if n > 2 and pair[key].std() > 0 and pair["est"].std() > 0
                else np.nan)
        out[key] = {"mae": mae, "corr": corr, "n": n}
    candidates = [k for k in ("same", "next")
                  if out[k]["n"] >= 5 and pd.notna(out[k]["mae"])]
    if candidates:
        out["best"] = min(candidates, key=lambda k: out[k]["mae"])
    return out
