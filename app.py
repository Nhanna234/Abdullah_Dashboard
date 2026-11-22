"""
Indian Market Breadth Tracker — Streamlit app
=============================================

What it does
------------
1. Trend state of Nifty 50, Nifty Midcap 150 and Nifty Smallcap 250
   (S-UT / UT / TZ / DT / S-DT) from 10- and 50-day EMAs and 3-month lows.
2. True breadth: % of Nifty 500 constituents closing above their own 50-day SMA.
3. A combined regime, posture, breadth check and a written remark for every session.
4. Sector rotation page: every NSE sectoral and thematic index (plus Nifty 50/200/500) ranked by
   Rank(1,3,6) = 0.5 x rank of 1M + 0.3 x rank of 3M + 0.2 x rank of 6M return, with weekly rank
   history, and a drill-down into the strongest indices' stocks using the Abu Abdullah RS rules.
   The work is done in sectors.py, rs_leaders.py and lender_filter.py.

Data sources (fetched fresh, cached for a few hours)
----------------------------------------------------
* Index closes      : NSE Indices historical data API (niftyindices.com) — official source.
* Nifty 500 members : NSE Indices constituent file (ind_nifty500list.csv) — official source.
* Stock closes      : Yahoo Finance via yfinance, split/bonus ADJUSTED. Exchange bhavcopy is
                      the primary source but is unadjusted, so any split or bonus inside the
                      window would wrongly push that stock below its 50 DMA.

Run locally:  pip install -r requirements.txt  &&  streamlit run app.py
"""

from __future__ import annotations

import io
import json
import os
import re
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components

import lender_filter
import rs_leaders
import sectors

# ════════════════════════════════════════════════════════════════════════════
# 1. RULE SETTINGS — every threshold lives here. Change a number, the app follows.
# ════════════════════════════════════════════════════════════════════════════
CFG = {
    "ema_fast": 10,            # fast EMA for S-UT / S-DT (also needs fast EMA above / below slow EMA)
    "ema_slow": 50,            # slow EMA: above = uptrend
    "sdt_needs_3m_low": True,  # S-DT also requires the DT condition (3-month low). False = EMA test alone
    "lookback": 63,            # sessions in "3-month low" (≈ 3 months of trading)
    "latch": 10,               # 'recent' DT test: fresh 3M low within this many sessions
    "tol_pct": 2.0,            # 'tolerance' DT test: close within this % of the 3M low
    "traj_n": 5,               # sessions for score trajectory (Δ)
    "traj_thresh": 3,          # |Δ score| that counts as improving / deteriorating
    "dma": 50,                 # stock-level moving average for breadth (simple MA)
    "breadth_zones": [         # (lower bound %, label) — checked top-down
        (80, "Overheated"), (60, "Healthy"), (40, "Neutral"), (20, "Weak"), (0, "Washed-out")],
    "breadth_confirm_up": 50,  # up-regime confirmed if ≥ this % above 50 DMA
    "breadth_diverge_up": 40,  # up-regime diverges if < this %
    "breadth_confirm_dn": 40,  # down-regime confirmed if ≤ this %
    "breadth_diverge_dn": 50,  # down-regime diverges if ≥ this % (strength under the surface)
    "breadth_delta_n": 5,      # sessions for breadth change
    "breadth_delta_pts": 10,   # change in %-points that counts as improving / deteriorating
    "thrust_from": 25,         # breadth thrust: from below this % ...
    "thrust_to": 60,           # ... to above this % ...
    "thrust_n": 10,            # ... within this many sessions
}

INDICES = {  # key: (niftyindices API name, display / indexName, yfinance fallback)
    "n50": ("NIFTY 50", "Nifty 50", "^NSEI"),
    "mid": ("NIFTY MIDCAP 150", "Nifty Midcap 150", "NIFTYMIDCAP150.NS"),
    "sml": ("NIFTY SMLCAP 250", "Nifty Smallcap 250", "NIFTYSMLCAP250.NS"),
}
SHORT = {"n50": "Nifty 50", "mid": "Midcap 150", "sml": "Smallcap 250"}
SCORE = {"S-UT": 2, "UT": 1, "TZ": 0, "DT": -1, "S-DT": -2}
STATE_COLOR = {"S-UT": "#166534", "UT": "#4ade80", "TZ": "#facc15", "DT": "#f87171", "S-DT": "#991b1b"}
STATE_TEXT = {"S-UT": "#ffffff", "UT": "#052e16", "TZ": "#422006", "DT": "#450a0a", "S-DT": "#ffffff"}
POSTURE_COLOR = {"Risk-on": "#166534", "Constructive": "#4ade80", "Selective": "#facc15",
                 "Cautious": "#fb923c", "Defensive": "#f87171", "Risk-off": "#991b1b"}
POSTURE_TEXT = {"Risk-on": "#ffffff", "Constructive": "#052e16", "Selective": "#422006",
                "Cautious": "#431407", "Defensive": "#450a0a", "Risk-off": "#ffffff"}
CHECK_COLOR = {"Confirms": "#bbf7d0", "Diverges": "#fecaca", "Neutral": "#e5e7eb", "n/a": "#f3f4f6"}

NI_BASE = "https://www.niftyindices.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
OFFLINE_DIR = os.environ.get("BREADTH_OFFLINE_DIR")  # for testing without internet
IST = timezone(timedelta(hours=5, minutes=30))
SESSION_CUTOFF_IST = (17, 0)  # after 5:00 pm IST the day's session counts as complete


def last_complete_session_date(now: datetime | None = None) -> date:
    """Latest calendar date whose closing prices are final (holidays/weekends simply return no rows).

    Before the cut-off, today's prices are still live/intraday, so we stop at yesterday.
    This also changes the cache key at the cut-off, forcing a fresh download after the close.
    """
    now = (now or datetime.now(IST)).astimezone(IST)
    today = now.date()
    return today if (now.hour, now.minute) >= SESSION_CUTOFF_IST else today - timedelta(days=1)

# ════════════════════════════════════════════════════════════════════════════
# 2. DATA FETCHING
# ════════════════════════════════════════════════════════════════════════════
def _ni_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:  # picks up the site's cookies, as a browser would
        s.get(f"{NI_BASE}/reports/historical-data", timeout=15)
    except requests.RequestException:
        pass
    return s


def _ni_index_chunk(s: requests.Session, api_name: str, index_name: str, start: date, end: date) -> list[dict]:
    cinfo = (f"{{'name':'{api_name}','startDate':'{start:%d-%b-%Y}',"
             f"'endDate':'{end:%d-%b-%Y}','indexName':'{index_name}'}}")
    r = s.post(f"{NI_BASE}/BackPage/getHistoricaldatatabletoString",
               data=json.dumps({"cinfo": cinfo}),
               headers={"Content-Type": "application/json; charset=utf-8",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest",
                        "Origin": NI_BASE, "Referer": f"{NI_BASE}/reports/historical-data"},
               timeout=30)
    r.raise_for_status()
    txt = r.text.strip()
    if not txt.startswith(("[", "{")):
        raise ValueError("niftyindices returned a non-JSON page (blocked or changed)")
    data = json.loads(txt)
    if isinstance(data, dict):  # older ASP.NET shape: {"d": "<json string>"}
        data = data.get("d", [])
        data = json.loads(data) if isinstance(data, str) else data
    return data


def fetch_index_niftyindices(key: str, start: date, end: date) -> pd.Series:
    api_name, index_name, _ = INDICES[key]
    s = _ni_session()
    rows, cur = [], start
    while cur <= end:  # the API is happiest with ~6-month windows
        nxt = min(cur + timedelta(days=180), end)
        rows += _ni_index_chunk(s, api_name, index_name, cur, nxt)
        cur = nxt + timedelta(days=1)
        time.sleep(0.4)
    if not rows:
        raise ValueError(f"No rows for {index_name}")
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["HistoricalDate"], format="%d %b %Y")
    ser = pd.to_numeric(df["CLOSE"], errors="coerce").set_axis(df["date"]).sort_index()
    return ser[~ser.index.duplicated(keep="last")].dropna().rename(key)


def fetch_index_yahoo(key: str, start: date, end: date) -> pd.Series:
    import yfinance as yf
    t = INDICES[key][2]
    df = yf.download(t, start=start, end=end + timedelta(days=1), progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"Yahoo returned nothing for {t}")
    close = df["Close"]
    close = close.iloc[:, 0] if isinstance(close, pd.DataFrame) else close
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close.dropna().rename(key)


@st.cache_data(ttl=60 * 60, show_spinner="Fetching index closes from niftyindices.com…")
def load_indices(start: date, end: date) -> tuple[pd.DataFrame, dict]:
    if OFFLINE_DIR:
        df = pd.read_csv(os.path.join(OFFLINE_DIR, "indices.csv"), parse_dates=["date"]).set_index("date")
        return df[["n50", "mid", "sml"]], {k: "offline file" for k in INDICES}
    out, src = {}, {}
    for k in INDICES:
        try:
            out[k] = fetch_index_niftyindices(k, start, end)
            src[k] = "niftyindices.com (official)"
        except Exception as e1:  # noqa: BLE001
            try:
                out[k] = fetch_index_yahoo(k, start, end)
                src[k] = f"Yahoo fallback ({INDICES[k][2]}) — niftyindices failed: {e1}"
            except Exception as e2:  # noqa: BLE001
                raise RuntimeError(f"{INDICES[k][1]}: niftyindices failed ({e1}); Yahoo failed ({e2})")
    return pd.concat(out, axis=1).sort_index().dropna(how="any"), src


@st.cache_data(ttl=24 * 60 * 60, show_spinner="Fetching Nifty 500 constituents…")
def load_nifty500_symbols() -> pd.DataFrame:
    if OFFLINE_DIR:
        return pd.read_csv(os.path.join(OFFLINE_DIR, "nifty500.csv"))
    r = requests.get(f"{NI_BASE}/IndexConstituent/ind_nifty500list.csv",
                     headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = [c.strip() for c in df.columns]
    return df


@st.cache_data(ttl=6 * 60 * 60, show_spinner="Downloading ~500 stock histories (first load takes ~30–90 s)…")
def load_stock_closes(symbols: tuple[str, ...], start: date, end: date) -> pd.DataFrame:
    if OFFLINE_DIR:
        return pd.read_csv(os.path.join(OFFLINE_DIR, "stocks.csv"), parse_dates=["date"]).set_index("date")
    import yfinance as yf
    tickers = [f"{s}.NS" for s in symbols]
    frames = []
    for i in range(0, len(tickers), 100):  # batches keep Yahoo from throttling
        df = yf.download(tickers[i:i + 100], start=start, end=end + timedelta(days=1),
                         auto_adjust=True, progress=False, threads=True, group_by="column")
        if df.empty:
            continue
        close = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
        frames.append(close)
        time.sleep(1)
    if not frames:
        raise RuntimeError("Yahoo returned no stock data")
    closes = pd.concat(frames, axis=1)
    closes.index = pd.to_datetime(closes.index).tz_localize(None).normalize()
    closes.columns = [str(c).removesuffix(".NS") for c in closes.columns]
    return closes.sort_index()


# ════════════════════════════════════════════════════════════════════════════
# 3. CALCULATIONS
# ════════════════════════════════════════════════════════════════════════════
def ema(s: pd.Series, n: int) -> pd.Series:
    """EMA seeded with the SMA of the first n closes (matches common charting tools)."""
    out = pd.Series(np.nan, index=s.index)
    if len(s) < n:
        return out
    a, vals = 2 / (n + 1), s.to_numpy(dtype=float)
    res = np.full(len(vals), np.nan)
    res[n - 1] = vals[:n].mean()
    for i in range(n, len(vals)):
        res[i] = a * vals[i] + (1 - a) * res[i - 1]
    return pd.Series(res, index=s.index)


def index_states(px: pd.DataFrame, dt_mode: str, cfg: dict) -> pd.DataFrame:
    out = pd.DataFrame(index=px.index)
    for k in INDICES:
        c = px[k]
        e_f, e_s = ema(c, cfg["ema_fast"]), ema(c, cfg["ema_slow"])
        low = c.rolling(cfg["lookback"]).min()
        new_low = (c <= low) & low.notna()
        if dt_mode == "strict":
            at_low = new_low
        elif dt_mode == "tol":
            at_low = c <= low * (1 + cfg["tol_pct"] / 100)
        else:  # 'latch' (recommended)
            at_low = new_low.astype(int).rolling(cfg["latch"], min_periods=1).max().astype(bool)
        # S-UT needs all three: close > fast EMA, close > slow EMA, fast EMA > slow EMA.
        # Any other close above the slow EMA is UT (pullback below the fast EMA, or the
        # fast EMA has not yet crossed above the slow EMA).
        strong_up = (c > e_f) & (c > e_s) & (e_f > e_s)
        # S-DT mirrors it: close < fast EMA, close < slow EMA, fast EMA < slow EMA (on top of being DT).
        strong_dn = (c < e_f) & (c < e_s) & (e_f < e_s)
        dt_zone = at_low | (strong_dn & (not cfg["sdt_needs_3m_low"]))
        state = np.where(c > e_s, np.where(strong_up, "S-UT", "UT"),
                         np.where(dt_zone, np.where(strong_dn, "S-DT", "DT"), "TZ"))
        out[k] = c
        out[f"{k}_ema_f"], out[f"{k}_ema_s"], out[f"{k}_low"] = e_f, e_s, low
        out[f"{k}_vs_f"] = (c / e_f - 1) * 100
        out[f"{k}_vs_s"] = (c / e_s - 1) * 100
        out[f"{k}_fs"] = (e_f / e_s - 1) * 100          # fast EMA vs slow EMA
        out[f"{k}_off_low"] = (c / low - 1) * 100
        out[f"{k}_new_low"] = new_low
        out[f"{k}_state"] = state
    valid = out[[f"{k}_ema_s" for k in INDICES] + [f"{k}_low" for k in INDICES]].notna().all(axis=1)
    return out[valid].copy()


def pct_above_dma(closes: pd.DataFrame, n: int) -> pd.DataFrame:
    sma = closes.rolling(n, min_periods=n).mean()
    valid = sma.notna() & closes.notna()
    above = (closes > sma) & valid
    cnt = valid.sum(axis=1)
    pct = (above.sum(axis=1) / cnt.replace(0, np.nan)) * 100
    return pd.DataFrame({"breadth_pct": pct, "breadth_n": cnt})


def regime_of(st_: dict) -> tuple[str, str]:
    up = {k: st_[k] in ("UT", "S-UT") for k in INDICES}
    dn = {k: st_[k] in ("DT", "S-DT") for k in INDICES}
    a, d = sum(up.values()), sum(dn.values())
    if a == 3:
        return ("Broad strong uptrend" if all(st_[k] == "S-UT" for k in INDICES) else "Broad uptrend"), "Risk-on"
    if d == 3:
        return ("Broad strong downtrend" if all(st_[k] == "S-DT" for k in INDICES) else "Broad downtrend"), "Risk-off"
    if a and d:
        return "Divergent", "Cautious"
    if a:
        if up["n50"] and not up["mid"] and not up["sml"]:
            return "Narrow: large-caps only", "Cautious"
        if up["n50"]:
            return "Breadth narrowing", "Selective"
        if up["mid"] and up["sml"]:
            return "Broader market leading", "Constructive"
        return "Mixed", "Selective"
    return ("Correction / no trend", "Defensive") if d == 0 else ("Weakening", "Defensive")


def breadth_zone(p: float, cfg: dict) -> str:
    if pd.isna(p):
        return "n/a"
    for lo, lab in cfg["breadth_zones"]:
        if p >= lo:
            return lab
    return cfg["breadth_zones"][-1][1]


def breadth_check(posture: str, p: float, cfg: dict) -> str:
    if pd.isna(p):
        return "n/a"
    if posture in ("Risk-on", "Constructive"):
        return "Confirms" if p >= cfg["breadth_confirm_up"] else "Diverges" if p < cfg["breadth_diverge_up"] else "Neutral"
    if posture in ("Defensive", "Risk-off"):
        return "Confirms" if p <= cfg["breadth_confirm_dn"] else "Diverges" if p >= cfg["breadth_diverge_dn"] else "Neutral"
    return "Neutral"


def build_table(px: pd.DataFrame, breadth: pd.DataFrame | None, dt_mode: str, cfg: dict) -> pd.DataFrame:
    df = index_states(px, dt_mode, cfg)
    if breadth is not None:
        df = df.join(breadth, how="left")
    else:
        df["breadth_pct"], df["breadth_n"] = np.nan, np.nan
    F, S = cfg["ema_fast"], cfg["ema_slow"]
    meaning = {
        "Broad strong uptrend": f"All three above {F} & {S} EMA with {F} EMA above {S} EMA: broad, healthy participation",
        "Broad uptrend": (f"All three above {S} EMA; at least one either pulling back below its {F} EMA "
                          f"or with its {F} EMA not yet above its {S} EMA (uptrend not fully confirmed)"),
        "Broader market leading": "Mid & small-caps in uptrend while Nifty 50 lags: heavyweights dragging, broad market healthy",
        "Breadth narrowing": f"Nifty 50 up but one of mid/small has slipped below {S} EMA: participation shrinking",
        "Narrow: large-caps only": f"Only Nifty 50 above {S} EMA: index masks weak breadth (classic late-stage warning)",
        "Mixed": f"Only one broader index above {S} EMA, no downtrend: unclear, wait for confirmation",
        "Divergent": "At least one index in uptrend and another in downtrend: split market, high whipsaw risk",
        "Correction / no trend": f"All below {S} EMA but none at 3M lows: correction or base-building",
        "Broad downtrend": f"All three below {S} EMA near 3M lows: broad selling",
        "Broad strong downtrend": (f"All three in downtrend, below {F} & {S} EMA with {F} EMA below {S} EMA: "
                                   "maximum weakness, no bounce yet"),
    }
    recs, prev, streak, idx_days = [], None, 0, {k: 0 for k in INDICES}
    scores, bvals = [], []
    for dt_, r in df.iterrows():
        st_ = {k: r[f"{k}_state"] for k in INDICES}
        score = sum(SCORE[v] for v in st_.values())
        scores.append(score)
        reg, post = regime_of(st_)
        streak = streak + 1 if prev is not None and prev["regime"] == reg else 1
        for k in INDICES:
            idx_days[k] = idx_days[k] + 1 if prev is not None and prev[f"{k}_state"] == st_[k] else 1
        d_score = score - scores[-1 - cfg["traj_n"]] if len(scores) > cfg["traj_n"] else np.nan

        bp = r["breadth_pct"]
        bvals.append(bp)
        b_delta = bp - bvals[-1 - cfg["breadth_delta_n"]] if len(bvals) > cfg["breadth_delta_n"] else np.nan
        window = [v for v in bvals[-cfg["thrust_n"]:] if not pd.isna(v)]
        thrust = bool(window) and not pd.isna(bp) and min(window) < cfg["thrust_from"] and bp > cfg["thrust_to"]
        zone, check = breadth_zone(bp, cfg), breadth_check(post, bp, cfg)

        # ---- remark --------------------------------------------------------
        if reg == "Weakening":
            names = " & ".join(SHORT[k] for k in INDICES if st_[k] in ("DT", "S-DT"))
            m = f"All three below {S} EMA; downtrend (near 3M lows) in {names}"
        else:
            m = meaning[reg]
        vs = sorted(((SHORT[k], r[f"{k}_vs_s"]) for k in INDICES), key=lambda x: -x[1])
        lead = f"{vs[0][0]} strongest ({vs[0][1]:+.2f}% vs {S}EMA), {vs[2][0]} weakest ({vs[2][1]:+.2f}%)"
        any_up = any(r[f"{k}_vs_s"] > 0 for k in INDICES)
        if r["sml_vs_s"] > r["n50_vs_s"] and r["mid_vs_s"] > r["n50_vs_s"]:
            risk = "small/mid outperforming = risk appetite intact" if any_up else "small/mid falling less than large-caps"
        elif r["n50_vs_s"] > r["mid_vs_s"] and r["n50_vs_s"] > r["sml_vs_s"]:
            risk = ("large-caps leading = narrow, defensive market" if any_up
                    else "broader market under more stress than large-caps = defensive rotation")
        else:
            risk = "leadership mixed"
        events = []
        if prev is not None:
            for k in INDICES:
                was_up, now_up = prev[f"{k}_state"] in ("UT", "S-UT"), st_[k] in ("UT", "S-UT")
                if now_up and not was_up:
                    events.append(f"{SHORT[k]} reclaimed {S}EMA")
                if was_up and not now_up:
                    events.append(f"{SHORT[k]} lost {S}EMA")
                if r[f"{k}_new_low"]:
                    events.append(f"{SHORT[k]} at fresh 3M closing low")
        parts = [f"{m}.", f"{lead}; {risk}."]
        if events:
            parts.append("; ".join(events) + ".")
        if not pd.isna(bp):
            b = f"Participation: {bp:.0f}% of Nifty 500 above {cfg['dma']} DMA ({zone})"
            if check == "Confirms":
                b += ", confirming the index picture"
            elif check == "Diverges":
                b += (", NOT confirming the uptrend (rally carried by fewer stocks)" if post in ("Risk-on", "Constructive")
                      else ", stronger than the indices suggest (selling may be exhausting)")
            if not pd.isna(b_delta) and abs(b_delta) >= cfg["breadth_delta_pts"]:
                b += f"; {'improving' if b_delta > 0 else 'deteriorating'} {b_delta:+.0f} pts in {cfg['breadth_delta_n']} sessions"
            parts.append(b + ".")
            if thrust:
                parts.append(f"BREADTH THRUST: from below {cfg['thrust_from']}% to above {cfg['thrust_to']}% "
                             f"within {cfg['thrust_n']} sessions — historically a strong bullish signal.")
            elif zone == "Washed-out":
                parts.append("Washed-out breadth: the zone where bottoms tend to form; wait for breadth to turn up.")
        if not pd.isna(d_score) and abs(d_score) >= cfg["traj_thresh"]:
            parts.append(f"{'Improving' if d_score > 0 else 'Deteriorating'} (score {d_score:+.0f} in {cfg['traj_n']} sessions).")

        rec = {"date": dt_, "score": score, "d_score": d_score, "regime": reg, "posture": post,
               "streak": streak, "breadth_pct": bp, "breadth_n": r["breadth_n"], "breadth_delta": b_delta,
               "breadth_zone": zone, "breadth_check": check, "thrust": thrust, "remark": " ".join(parts)}
        for k in INDICES:
            rec.update({k: r[k], f"{k}_state": st_[k], f"{k}_days": idx_days[k], f"{k}_ema_f": r[f"{k}_ema_f"],
                        f"{k}_ema_s": r[f"{k}_ema_s"], f"{k}_vs_f": r[f"{k}_vs_f"], f"{k}_vs_s": r[f"{k}_vs_s"],
                        f"{k}_fs": r[f"{k}_fs"], f"{k}_off_low": r[f"{k}_off_low"]})
        recs.append(rec)
        prev = rec
    return pd.DataFrame(recs).set_index("date")


# ════════════════════════════════════════════════════════════════════════════
# 3b. SECTOR ROTATION — engine in sectors.py (index list, prices, Rank(1,3,6)) and rs_leaders.py
#     (Abu Abdullah RS screen for the strongest indices' stocks); this file only draws the page.
# ════════════════════════════════════════════════════════════════════════════
# ---- cached data for the Sector rotation page (the work itself is in sectors.py and rs_leaders.py)
@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def cached_discovery() -> dict:
    if OFFLINE_DIR:
        return {"new": [], "excluded": [], "ok": True, "note": "offline test data"}
    return sectors.discover()


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def cached_mapping() -> dict:
    return {} if OFFLINE_DIR else sectors.load_mapping()


@st.cache_data(ttl=60 * 60, show_spinner="Fetching sector and thematic indices from niftyindices.com "
                                          "(first load can take about a minute)…")
def cached_index_prices(names: tuple, start: date, end: date) -> tuple[dict, dict]:
    if OFFLINE_DIR:
        raw = pd.read_csv(os.path.join(OFFLINE_DIR, "sectors.csv"), parse_dates=["date"])
        data = {n: g.set_index("date")[["close", "high", "low"]].sort_index()
                for n, g in raw.groupby("name") if n in names}
        return data, {n: {"source": "offline file", "api": "", "rows": len(d), "last": d.index.max()}
                      for n, d in data.items()}
    return sectors.fetch_all(list(names), start, end, cached_mapping())


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def cached_constituents(name: str, page: str) -> tuple[pd.DataFrame, str]:
    if OFFLINE_DIR:
        return pd.read_csv(os.path.join(OFFLINE_DIR, "constituents", re.sub(r"\W+", "_", name) + ".csv")), "offline"
    fname = sectors.constituent_file(page)
    return sectors.download_list(fname), fname


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def cached_nbfc_index() -> set:
    if OFFLINE_DIR:
        return set()
    try:
        return set(sectors.download_list("ind_niftyNBFC_list.csv")["Symbol"])
    except Exception:  # noqa: BLE001
        return set()


@st.cache_data(ttl=60 * 60, show_spinner="Downloading monthly prices of the constituents from Yahoo Finance…")
def cached_monthly(symbols: tuple) -> pd.DataFrame:
    if OFFLINE_DIR:
        m = pd.read_pickle(os.path.join(OFFLINE_DIR, "stocks_monthly.pkl"))
        keep = [s + rs_leaders.SUFFIX for s in symbols if s + rs_leaders.SUFFIX in m["Close"].columns]
        cols = [c for c in m.columns if c[1] in keep + [rs_leaders.INDEX_TICKER]]
        return m[cols]
    return rs_leaders.fetch_monthly(list(symbols))


@st.cache_resource(max_entries=1)
def lender_cache():
    return lender_filter.BasicIndustryCache(os.path.join(tempfile.gettempdir(), "dash_basic_industry.json"),
                                            fetch_one=(lambda s: None) if OFFLINE_DIR else None)



# ════════════════════════════════════════════════════════════════════════════
# 4. DATA FOR THE WEBSITE — Python computes, the React front end (frontend/, built into
#    frontend/dist) draws. Each page asks for what it needs; results are kept per visitor.
# ════════════════════════════════════════════════════════════════════════════
def _j(v):
    """JSON-safe value: NaN/inf -> None, numpy -> Python, timestamps -> 'YYYY-MM-DD'."""
    if v is None:
        return None
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(v).strftime("%Y-%m-%d")
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if not np.isfinite(v) else round(float(v), 6)
    return v


def _rec(d: dict) -> dict:
    return {k: _j(v) for k, v in d.items()}


DT_MODES = {"latch": f"Fresh 3-month low within the last {CFG['latch']} sessions",
            "strict": "Today's close is the 3-month low",
            "tol": f"Close within {CFG['tol_pct']:g}% of the 3-month low"}


def breadth_payload(window_days: int, dt_mode: str, use_breadth: bool) -> dict:
    data_end = last_complete_session_date()
    idx_start = data_end - timedelta(days=window_days + 420)   # EMA warm-up + 3M low + window
    stk_start = data_end - timedelta(days=window_days + 120)   # 50 DMA needs ~75 calendar days
    try:
        px, src = load_indices(idx_start, data_end)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not load the index closes: {e}"}
    breadth, note = None, ""
    if use_breadth:
        try:
            members = load_nifty500_symbols()
            syms = tuple(sorted(members["Symbol"].astype(str).str.strip().unique()))
            closes = load_stock_closes(syms, stk_start, data_end)
            closes = closes[closes.index <= pd.Timestamp(data_end)]
            got = int(closes.notna().any().sum())
            breadth = pct_above_dma(closes, CFG["dma"])
            note = f"{got} of {len(syms)} Nifty 500 stocks downloaded."
            if got < 0.9 * len(syms):
                note += " Coverage is below 90%, so treat the breadth figure with care."
        except Exception as e:  # noqa: BLE001
            note = f"Breadth is unavailable on this load: {e}"
    full = build_table(px, breadth, dt_mode, CFG)
    view = full[full.index > pd.Timestamp(full.index.max()) - pd.Timedelta(days=window_days)]
    rows = [_rec({"date": d, **r}) for d, r in view.reset_index().set_index("date").to_dict("index").items()]
    prev_close = {k: _j(view[k].iloc[-2]) if len(view) > 1 else None for k in INDICES}
    return {"ok": True, "asOf": _j(view.index[-1]), "rows": rows, "prevClose": prev_close,
            "indices": {k: v[1] for k, v in INDICES.items()},
            "sources": {INDICES[k][1]: v for k, v in src.items()}, "breadthNote": note,
            "settings": {"window": window_days, "dtMode": dt_mode, "useBreadth": use_breadth},
            "cfg": {k: v for k, v in CFG.items() if k != "breadth_zones"},
            "zones": [[lo, lab] for lo, lab in CFG["breadth_zones"]],
            "rendered": datetime.now(IST).strftime("%d %b %Y, %H:%M IST")}


def _catalog() -> tuple[list, dict]:
    discovery = cached_discovery()
    for n, g, _ in discovery["new"]:
        sectors.GROUP.setdefault(n, g)
    return sectors.CATALOG + [tuple(x) for x in discovery["new"]], discovery


def sector_payload(groups: list, weeks: int, basis: str, days_3m: int, scope: str = "major") -> dict:
    catalog, discovery = _catalog()
    if scope == "major":                       # the default: major indices only (sectors.MAJOR)
        catalog = [c for c in catalog if c[0] in sectors.MAJOR]
    names = [n for n, g, _ in catalog if g in groups]
    data_end = last_complete_session_date()
    data, status = cached_index_prices(tuple(n for n, _, _ in catalog),
                                       data_end - timedelta(days=sectors.SECTOR_CFG["history_days"]), data_end)
    # an index that does not load is left out everywhere; it shows only in the data status list
    coverage = {
        "checked": "22 Nov 2026", "discoveryNote": discovery["note"], "scope": scope,
        "newFound": [{"name": n, "group": g} for n, g, _ in discovery["new"]],
        "excluded": [{"name": n, "why": w} for n, w in discovery["excluded"]]
                    or [{"name": p, "why": w} for p, w in sectors.EXCLUDE_RULES],
        "bse": [{"name": n, "why": w} for n, w in sectors.BSE_NOT_ADDED],
        "status": [_rec({"name": n, "group": g, "source": status.get(n, {}).get("source", "not requested"),
                         "api": status.get(n, {}).get("api", ""), "rows": status.get(n, {}).get("rows", 0),
                         "last": status.get(n, {}).get("last"), "note": status.get(n, {}).get("note", "")})
                   for n, g, _ in catalog],
    }
    data = {n: d for n, d in data.items() if n in names}
    if not data:
        return {"ok": False, "error": "No index could be loaded from niftyindices.com. The data status list below "
                                      "shows what each index returned.", "coverage": coverage}
    latest = max(df.index.max() for df in data.values())
    snap = sectors.order_by(sectors.sector_snapshot(data, latest, days_3m), basis)
    dates = sectors.week_end_dates(data, latest, weeks)
    hist = sectors.rank_history(data, dates, days_3m, basis).reindex(snap.index)
    prev = hist.iloc[:, -2] if hist.shape[1] >= 2 else pd.Series(np.nan, index=hist.index)
    pages = {n: p for n, _, p in catalog}
    rows = []
    for name, r in snap.iterrows():
        rows.append(_rec({"name": name, "group": r["group"], "pos": r["pos"], "prevPos": prev.get(name),
                          "close": r["close"], "high52": r["high_52w"], "low52": r["low_52w"],
                          "r1w": r["r_1w"], "r1m": r["r_1m"], "r3m": r["r_3m"], "r6mx1m": r["r_6m_ex_1m"],
                          "r6m": r["r_6m"], "r1y": r["r_1y"], "awayLow": r["away_low"], "offHigh": r["off_high"],
                          "score": r["score"], "drillable": bool(pages.get(name))})
                    | {"trail": [_j(v) for v in hist.loc[name].tolist()]})
    return {"ok": True, "asOf": _j(latest), "rows": rows, "weeks": [_j(d) for d in hist.columns],
            "notes": sectors.rotation_notes(hist), "coverage": coverage,
            "failed": [n for n in names if n not in data], "scope": scope,
            "lenderIndices": sorted(sectors.LENDER_INDICES),
            "settings": {"scope": scope, "groups": groups, "weeks": weeks, "basis": basis, "days3m": days_3m},
            "cfg": {k: v for k, v in sectors.SECTOR_CFG.items() if k != "days"} | {"days": sectors.SECTOR_CFG["days"]},
            "rendered": datetime.now(IST).strftime("%d %b %Y, %H:%M IST")}


def drill_payload(chosen: list, p: dict, no_lenders: bool) -> dict:
    catalog, _ = _catalog()
    pages = {n: pg for n, _, pg in catalog}
    members, info, errors = {}, {}, []
    for n in chosen:
        if not pages.get(n):
            continue
        try:
            df, _src = cached_constituents(n, pages[n])
        except Exception as e:  # noqa: BLE001
            errors.append(f"{n}: {e}")
            continue
        for _, r in df.iterrows():
            members.setdefault(r["Symbol"], []).append(n)
            info.setdefault(r["Symbol"], (str(r.get("Company Name", "")), str(r.get("Industry", ""))))
    base = {"ok": True, "indices": chosen, "errors": errors, "settings": {**p, "noLenders": no_lenders},
            "members": len(members), "excluded": [], "stocks": [], "labels": [], "provisional": False}
    if not members:
        return {**base, "ok": not errors, "error": "No constituent list could be loaded." if errors else None}
    symbols = sorted(members)
    if no_lenders:
        res = lender_filter.classify(symbols, {s: info[s][0] for s in symbols}, {s: info[s][1] for s in symbols},
                                     lender_cache(), cached_nbfc_index())
        drop = {r["symbol"] for r in res["excluded"]}
        base["excluded"] = [{"symbol": r["symbol"], "name": r["name"], "label": r["label"], "source": r["source"]}
                            for r in res["excluded"]]
        symbols = [s for s in symbols if s not in drop]
    if not symbols:
        return base
    try:
        monthly = cached_monthly(tuple(symbols))
        if monthly is None or monthly.empty or rs_leaders.INDEX_TICKER not in monthly["Close"].columns:
            raise RuntimeError("Yahoo Finance returned no prices (it may be busy); try again in a minute")
        res, labels = rs_leaders.screen(monthly, p)
    except Exception as e:  # noqa: BLE001
        return {**base, "ok": False, "error": f"The RS screen could not run: {e}"}
    last_m = monthly["Close"].dropna(how="all").index[-1]
    today = datetime.now(IST).date()
    stocks = [_rec({"symbol": r["symbol"], "company": info.get(r["symbol"], ("", ""))[0],
                    "industry": info.get(r["symbol"], ("", ""))[1], "run": r["run"], "cr": r["cr"], "mr": r["mr"],
                    "emaGap": r["ema_gap"], "price": r["price"]})
              | {"indices": members.get(r["symbol"], []), "cells": [[_j(v), bool(m), bool(h)] for v, m, h in r["cells"]]}
              for _, r in res.iterrows()] if not res.empty else []
    return {**base, "labels": labels, "stocks": stocks, "checked": len(symbols),
            "provisional": (last_m.year, last_m.month) == (today.year, today.month), "lastMonth": _j(last_m)}


# ════════════════════════════════════════════════════════════════════════════
# 5. PAGES — one React component draws both; Streamlit's own chrome is hidden
# ════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Abdullah Dashboard", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
  header[data-testid="stHeader"], [data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
  [data-testid="stToolbar"], footer { display: none !important; }
  .stApp { background: #EEF1EE; }
  .block-container, [data-testid="stMainBlockContainer"] { padding: 0 !important; max-width: 100% !important; }
  iframe { display: block; }
  [data-testid="stAlert"], [data-testid="stProgress"] { margin: 10px 24px 0; }
</style>
""", unsafe_allow_html=True)

FRONTEND_DIR = Path(__file__).parent / "frontend" / "dist"
dashboard = components.declare_component("abdullah_dashboard", path=str(FRONTEND_DIR)) \
    if (FRONTEND_DIR / "index.html").exists() else None

DEFAULTS = {
    "breadth": {"window": 90, "dtMode": "latch", "useBreadth": True},
    "sectors": {"scope": "major", "groups": ["Sector", "Theme", "Benchmark"], "weeks": 12, "basis": "composite",
                "days3m": sectors.SECTOR_CFG["days_3m_true"]},
    "drill": {"topK": 3, "noLenders": True, **{k: v for k, v in rs_leaders.RS_DEFAULTS.items() if k != "lookback"}},
    "dtModes": DT_MODES,
    "bases": {"composite": "Rank(1,3,6), as in your sheet", "r_3m": "3M return", "away_low": "Away from 52W low"},
    "days3m": {"true": sectors.SECTOR_CFG["days_3m_true"], "sheet": sectors.SECTOR_CFG["days_3m_sheet"]},
    "maxWeeks": sectors.SECTOR_CFG["max_weeks"],
}


def _handle(request: dict, page_objects: dict) -> None:
    kind = request.get("kind")
    if kind == "goto":
        st.switch_page(page_objects[request.get("page", "breadth")])
    if kind == "refresh":
        st.cache_data.clear()
        for k in ("breadth", "sectors", "drill"):
            st.session_state.pop(k, None)
        st.rerun()
    note = st.empty()
    note.info({"breadth": "Working out market breadth. The first load of the day downloads about 500 stock "
                          "histories and can take a minute or two.",
               "sectors": "Ranking the sector and thematic indices. The first load can take about a minute.",
               "drill": "Screening the stocks of the chosen indices with the RS rules."}.get(kind, "Working…"))
    try:
        if kind == "breadth":
            out = breadth_payload(int(request.get("window", 90)), request.get("dtMode", "latch"),
                                  bool(request.get("useBreadth", True)))
        elif kind == "sectors":
            groups = [g for g in request.get("groups", []) if g in ("Sector", "Theme", "Benchmark")] or ["Sector"]
            weeks = max(4, min(int(request.get("weeks", 12)), sectors.SECTOR_CFG["max_weeks"]))
            basis = request.get("basis") if request.get("basis") in ("composite", "r_3m", "away_low") else "composite"
            out = sector_payload(groups, weeks, basis, int(request.get("days3m", 90)),
                                 "all" if request.get("scope") == "all" else "major")
        elif kind == "drill":
            p = {"threshold": float(request.get("threshold", 100)), "history": int(request.get("history", 6)),
                 "use_ema": bool(request.get("use_ema", True)), "cr": float(request.get("cr", 75)),
                 "mr": float(request.get("mr", 5))}
            out = drill_payload(list(request.get("indices", []))[:80], p, bool(request.get("noLenders", True)))
        else:
            out = {"ok": False, "error": f"Unknown request '{kind}'."}
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "error": f"Something went wrong: {type(e).__name__}: {e}"}
    note.empty()
    out["nonce"] = request.get("nonce")
    st.session_state[kind] = out
    st.rerun()


def render(page: str, page_objects: dict) -> None:
    if dashboard is None:
        st.error("The website files are missing (frontend/dist). Build them with: cd frontend && npm install && npm run build")
        st.stop()
    request = dashboard(page=page, defaults=DEFAULTS, breadth=st.session_state.get("breadth"),
                        sectors=st.session_state.get("sectors"), drill=st.session_state.get("drill"),
                        key=f"dash_{page}", default=None)
    if request and request.get("nonce") and request["nonce"] != st.session_state.get("last_nonce"):
        st.session_state["last_nonce"] = request["nonce"]
        _handle(request, page_objects)


def page_breadth() -> None:
    render("breadth", PAGE_OBJECTS)


def page_sectors() -> None:
    render("sectors", PAGE_OBJECTS)


_default = os.environ.get("BREADTH_DEFAULT_PAGE", "breadth")   # only used for offline testing
PAGE_OBJECTS = {
    "breadth": st.Page(page_breadth, title="Market breadth", url_path="breadth", default=_default == "breadth"),
    "sectors": st.Page(page_sectors, title="Sector rotation", url_path="sectors", default=_default == "sectors"),
}
st.navigation(list(PAGE_OBJECTS.values()), position="hidden").run()
