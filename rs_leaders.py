"""
RS-leadership screen for the stocks of the strongest indices (no Streamlit imports, testable on its own).

This is the screener of the "Abu Abdullah" RS Leaderboard app (github.com/Nhanna234/RS-on-Nifty,
app.py RSHeatmapScreener.generate_matrix and describe_stocks), with the same defaults
(RS threshold 100%, 6 months shown, 12-month look-back, close at or above the 12-month EMA,
closing range >= 75%, monthly return >= 5%). The rules, from that app's RULES.md:

* RS = a stock's month-end close / the Nifty 50 price index's month-end close.
* RS ratio = this month's RS / the highest month-end RS of the last 12 months (this month included).
  100% = a new 12-month RS high.
* A stock is listed if, on the latest month in the data (usually still running, so provisional):
  close >= its 12-month EMA (span 12 on month-end closes), closing range >= the minimum and monthly
  return >= the minimum; AND its RS ratio is at or above the threshold in at least one of the months shown.
* Prices: Yahoo Finance monthly prices for 3 years, adjusted for splits and dividends; a month that
  Yahoo returns twice is merged (highest high, lowest low, latest close).

One deliberate difference: a stock with no price in the latest month is skipped (the original app
could let such a stock through the filters because comparisons with a missing value are false).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RS_DEFAULTS = {"threshold": 100, "history": 6, "lookback": 12, "use_ema": True, "cr": 75, "mr": 5}
EMA_SPAN = 12
INDEX_TICKER = "^NSEI"          # Nifty 50 price index on Yahoo: the denominator of relative strength
SUFFIX = ".NS"
PRICE_PERIOD = "3y"
TRAIL_MONTHS = 24               # months of RS history sent for each listed stock's chart (as in the RS app)


def one_row_per_month(data: pd.DataFrame) -> pd.DataFrame:
    """Same as the RS app: merge a month that Yahoo returns as two rows."""
    if data is None or data.empty:
        return data
    idx = pd.DatetimeIndex(data.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    month = idx.to_period("M")
    how = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    fields = [f for f in how if f in data.columns.get_level_values(0)]
    merged = pd.concat({f: data[f].groupby(month).agg(how[f]) for f in fields}, axis=1)
    merged.index = merged.index.to_timestamp()
    return merged


def fetch_monthly(symbols: list[str]) -> pd.DataFrame:
    import yfinance as yf
    tickers = [s + SUFFIX for s in symbols] + [INDEX_TICKER]
    data = yf.download(tickers, period=PRICE_PERIOD, interval="1mo", auto_adjust=True, progress=False,
                       threads=True, group_by="column")
    return one_row_per_month(data)


def screen(monthly: pd.DataFrame, p: dict | None = None) -> tuple[pd.DataFrame, list[str]]:
    """Returns (one row per listed stock, month labels). Columns: symbol, cells (list of (ratio %, marked,
    new-high)), run, hits, latest_ratio, marked_latest, price, mr, cr, ema_gap."""
    p = {**RS_DEFAULTS, **(p or {})}
    thr, n, hist = p["threshold"] / 100.0, p["lookback"], p["history"]
    price_df = monthly["Close"].dropna(how="all")
    index_prices = price_df[INDEX_TICKER]
    stock_prices = price_df.drop(columns=[INDEX_TICKER])
    high_df, low_df = monthly["High"], monthly["Low"]
    target = sorted(stock_prices.index[-hist:])
    labels = [d.strftime("%b-%y") for d in target]

    rows = []
    for ticker in stock_prices.columns:
        px = stock_prices[ticker]
        curr, prev = px.iloc[-1], px.iloc[-2] if len(px) > 1 else np.nan
        hi, lo = high_df[ticker].iloc[-1], low_df[ticker].iloc[-1]
        if pd.isna(curr) or pd.isna(prev) or pd.isna(hi) or pd.isna(lo):
            continue
        ema12 = px.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
        if p["use_ema"] and curr < ema12:
            continue
        cr = (curr - lo) / (hi - lo) if hi != lo else 0
        if cr * 100 < p["cr"]:
            continue
        mr = curr / prev - 1
        if mr * 100 < p["mr"]:
            continue
        rs = (px / index_prices).dropna()
        cells, any_mark = [], False
        for d in target:
            ratio, marked, high = None, False, False
            if d in rs.index:
                i = rs.index.get_loc(d)
                if i >= n:
                    ratio = rs.iloc[i] / rs.iloc[i - n + 1: i + 1].max()
                    high = ratio >= 1.0
                    marked = high or ratio >= thr
            any_mark |= marked
            cells.append((None if ratio is None else round(ratio * 100, 1), marked, high))
        if not any_mark:
            continue
        run = 0
        for c in reversed(cells):
            if not c[1]:
                break
            run += 1
        trail = rs.iloc[-TRAIL_MONTHS:]
        trail_high = rs.rolling(n, min_periods=1).max().iloc[-TRAIL_MONTHS:]   # highest of the past 12 months
        rows.append({"symbol": ticker[:-len(SUFFIX)] if ticker.endswith(SUFFIX) else ticker,
                     "trail": {"labels": [d.strftime("%b-%y") for d in trail.index],
                               "rs": [float(v) for v in trail], "high": [float(v) for v in trail_high]},
                     "cells": cells, "run": run, "hits": sum(c[1] for c in cells),
                     "latest_ratio": cells[-1][0], "marked_latest": cells[-1][1], "price": float(curr),
                     "mr": float(mr), "cr": float(cr), "ema_gap": float(curr / ema12 - 1)})
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(["marked_latest", "run", "latest_ratio", "cr"],
                              ascending=[False, False, False, False]).reset_index(drop=True)
    return out, labels
