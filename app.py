"""
Indian Market Breadth Tracker — Streamlit app
=============================================

What it does
------------
1. Trend state of Nifty 50, Nifty Midcap 150 and Nifty Smallcap 250
   (S-UT / UT / TZ / DT / S-DT) from 10- and 50-day EMAs and 3-month lows.
2. True breadth: % of Nifty 500 constituents closing above their own 50-day SMA.
3. A combined regime, posture, breadth check and a written remark for every session.
4. Sector rotation page: 17 Nifty sector / thematic / benchmark indices ranked by Rank(1,3,6)
   (0.5 x rank of 1M + 0.3 x rank of 3M + 0.2 x rank of 6M return), with weekly rank history.

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
import textwrap
import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

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
# 3b. SECTOR ROTATION — mirrors the 'Sectors' and 'Analysis' tabs of the Google Sheet
# ════════════════════════════════════════════════════════════════════════════
SECTORS = {  # sheet name: (niftyindices name, NSE long name, Yahoo fallback, group)
    "NIFTY_BANK": ("NIFTY BANK", "Nifty Bank", "^NSEBANK", "Sector"),
    "NIFTY_AUTO": ("NIFTY AUTO", "Nifty Auto", "^CNXAUTO", "Sector"),
    "NIFTY_MNC": ("NIFTY MNC", "Nifty MNC", "^CNXMNC", "Theme"),
    "NIFTY_SERV_SECTOR": ("NIFTY SERV SECTOR", "Nifty Services Sector", "^CNXSERVICE", "Theme"),
    "NIFTY_FMCG": ("NIFTY FMCG", "Nifty FMCG", "^CNXFMCG", "Sector"),
    "NIFTY_IT": ("NIFTY IT", "Nifty IT", "^CNXIT", "Sector"),
    "NIFTY_MEDIA": ("NIFTY MEDIA", "Nifty Media", "^CNXMEDIA", "Sector"),
    "NIFTY_METAL": ("NIFTY METAL", "Nifty Metal", "^CNXMETAL", "Sector"),
    "NIFTY_PHARMA": ("NIFTY PHARMA", "Nifty Pharma", "^CNXPHARMA", "Sector"),
    "NIFTY_ENERGY": ("NIFTY ENERGY", "Nifty Energy", "^CNXENERGY", "Sector"),
    "NIFTY_INFRA": ("NIFTY INFRA", "Nifty Infrastructure", "^CNXINFRA", "Theme"),
    "NIFTY_REALTY": ("NIFTY REALTY", "Nifty Realty", "^CNXREALTY", "Sector"),
    "NIFTY_CONSUMPTION": ("NIFTY CONSUMPTION", "Nifty India Consumption", "^CNXCONSUM", "Theme"),
    "NIFTY_COMMODITIES": ("NIFTY COMMODITIES", "Nifty Commodities", "^CNXCMDT", "Theme"),
    "NIFTY_50": ("NIFTY 50", "Nifty 50", "^NSEI", "Benchmark"),
    "NIFTY_500": ("NIFTY 500", "Nifty 500", "^CRSLDX", "Benchmark"),
    "NIFTY_200": ("NIFTY 200", "Nifty 200", "^CNX200", "Benchmark"),
}
SECTOR_CFG = {
    "w_1m": 0.5, "w_3m": 0.3, "w_6m": 0.2,   # Rank(1,3,6) weights, same as the sheet
    "days": {"1w": 7, "1m": 30, "6m": 180, "1y": 365},  # calendar-day lookbacks, same as the sheet
    "days_3m_true": 90,         # a real 3 months
    "days_3m_sheet": 60,        # what the sheet's '3M Return' formula actually uses (TODAY()-60)
    "top_n": 5,                 # 'leaders' = top 5 positions
    "move_weeks": 4,            # rank change measured over this many weeks
    "history_days": 740,        # data fetched: 52 weeks of history + 1 year lookback
}
SECTOR_COLORS = ["#fde68a", "#bfdbfe", "#fecaca", "#bbf7d0", "#ddd6fe", "#fed7aa", "#a5f3fc", "#fbcfe8",
                 "#d9f99d", "#e9d5ff", "#99f6e4", "#fecdd3", "#c7d2fe", "#fef08a", "#e5e7eb", "#d6d3d1", "#cbd5e1"]
SECTOR_COLOR = dict(zip(SECTORS, SECTOR_COLORS))
BASIS = {"Composite Rank(1,3,6) — as in your sheet": "composite",
         "3M return": "r_3m",
         "Away from 52W low": "away_low"}


def _ni_ohlc(s: requests.Session, api_name: str, long_name: str, start: date, end: date) -> pd.DataFrame:
    rows, cur = [], start
    while cur <= end:
        nxt = min(cur + timedelta(days=360), end)
        last_err = None
        for idx_name in (long_name, api_name):     # the site has used both forms of indexName
            try:
                rows += _ni_index_chunk(s, api_name, idx_name, cur, nxt)
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
        if last_err is not None:
            raise last_err
        cur = nxt + timedelta(days=1)
        time.sleep(0.3)
    if not rows:
        raise ValueError("no rows returned")
    df = pd.DataFrame(rows)
    idx = pd.to_datetime(df["HistoricalDate"], format="%d %b %Y")
    out = pd.DataFrame({c.lower(): pd.to_numeric(df[c], errors="coerce").to_numpy() for c in ("CLOSE", "HIGH", "LOW")},
                       index=idx).sort_index()
    return out[~out.index.duplicated(keep="last")].dropna(subset=["close"])


def _yahoo_ohlc(ticker: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end + timedelta(days=1), progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"Yahoo returned nothing for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = df[["Close", "High", "Low"]].rename(columns=str.lower)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.dropna(subset=["close"])


def _fetch_one_sector(name: str, start: date, end: date) -> tuple[str, pd.DataFrame | None, str]:
    api, long_name, yh, _ = SECTORS[name]
    try:
        return name, _ni_ohlc(_ni_session(), api, long_name, start, end), "niftyindices.com"
    except Exception as e1:  # noqa: BLE001
        try:
            return name, _yahoo_ohlc(yh, start, end), f"Yahoo {yh} (niftyindices failed: {e1})"
        except Exception as e2:  # noqa: BLE001
            return name, None, f"FAILED — niftyindices: {e1}; Yahoo: {e2}"


@st.cache_data(ttl=60 * 60, show_spinner="Fetching 17 sector indices (first load ~20–40 s)…")
def load_sectors(start: date, end: date) -> tuple[dict, dict]:
    if OFFLINE_DIR:
        raw = pd.read_csv(os.path.join(OFFLINE_DIR, "sectors.csv"), parse_dates=["date"])
        data = {n: g.set_index("date")[["close", "high", "low"]].sort_index() for n, g in raw.groupby("name")}
        return data, {n: "offline file" for n in data}
    from concurrent.futures import ThreadPoolExecutor
    data, src = {}, {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        for name, df, note in ex.map(lambda n: _fetch_one_sector(n, start, end), list(SECTORS)):
            src[name] = note
            if df is not None and len(df):
                data[name] = df[df.index <= pd.Timestamp(end)]
    return data, src


def _base_close(df: pd.DataFrame, asof: pd.Timestamp, days: int) -> float:
    """Sheet logic: first close ON or AFTER (as-of date − N calendar days)."""
    target = asof - pd.Timedelta(days=days)
    if df.index[0] > target + pd.Timedelta(days=5):      # not enough history
        return np.nan
    sub = df.loc[target:asof, "close"]
    return float(sub.iloc[0]) if len(sub) else np.nan


def sector_snapshot(data: dict, asof: pd.Timestamp, days_3m: int, cfg: dict = SECTOR_CFG) -> pd.DataFrame:
    rows = []
    for name in SECTORS:
        df = data.get(name)
        if df is None:
            continue
        df = df.loc[:asof]
        if df.empty or (asof - df.index[-1]).days > 7:      # no recent close → skip
            continue
        c = float(df["close"].iloc[-1])
        b = {k: _base_close(df, asof, d) for k, d in cfg["days"].items()}
        b["3m"] = _base_close(df, asof, days_3m)
        yr = df.loc[asof - pd.Timedelta(days=cfg["days"]["1y"]):]
        enough_yr = df.index[0] <= asof - pd.Timedelta(days=cfg["days"]["1y"] - 5)
        hi = pd.concat([yr["high"].where(yr["high"] > 0), yr["close"]]).max() if enough_yr else np.nan
        lo = pd.concat([yr["low"].where(yr["low"] > 0), yr["close"]]).min() if enough_yr else np.nan
        rows.append({
            "name": name, "group": SECTORS[name][3], "date": df.index[-1], "close": c,
            "high_52w": hi, "low_52w": lo,
            "r_1w": c / b["1w"] - 1, "r_1m": c / b["1m"] - 1, "r_3m": c / b["3m"] - 1,
            "r_6m_ex_1m": b["1m"] / b["6m"] - 1,           # price 1M ago vs 6M ago (compounded)
            "r_6m": c / b["6m"] - 1, "r_1y": c / b["1y"] - 1,
            "away_low": c / lo - 1, "off_high": c / hi - 1,
        })
    t = pd.DataFrame(rows).set_index("name")
    if t.empty:
        return t
    # Google Sheets RANK(): 1 = highest value, ties share the lower rank number
    t["rk_1m"] = t["r_1m"].rank(ascending=False, method="min")
    t["rk_3m"] = t["r_3m"].rank(ascending=False, method="min")
    t["rk_6m"] = t["r_6m"].rank(ascending=False, method="min")
    t["score"] = cfg["w_1m"] * t["rk_1m"] + cfg["w_3m"] * t["rk_3m"] + cfg["w_6m"] * t["rk_6m"]
    return t


def order_by(t: pd.DataFrame, basis: str) -> pd.DataFrame:
    """Adds 'pos' (1 = best) using the chosen basis. Composite: lowest score first, ties → better 1M."""
    if basis == "composite":
        o = t.sort_values(["score", "r_1m"], ascending=[True, False], na_position="last")
    else:
        o = t.sort_values([basis, "r_1m"], ascending=[False, False], na_position="last")
    o = o.copy()
    o["pos"] = range(1, len(o) + 1)
    return o


def week_end_dates(data: dict, latest: pd.Timestamp, weeks: int) -> list[pd.Timestamp]:
    """Last trading day of each week (Mon–Fri) for the last `weeks` weeks; the newest is the latest close."""
    ref = data.get("NIFTY_50")
    idx = ref.index if ref is not None else pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    idx = idx[idx <= latest]
    ends = pd.Series(idx, index=idx).groupby(idx.to_period("W-FRI")).max()
    return list(ends.iloc[-weeks:])


def rank_history(data: dict, dates: list, days_3m: int, basis: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Position and composite score of every index at each weekly snapshot (index × date)."""
    pos, score = {}, {}
    for d in dates:
        o = order_by(sector_snapshot(data, d, days_3m), basis)
        pos[d], score[d] = o["pos"], o["score"]
    return pd.DataFrame(pos), pd.DataFrame(score)


def rotation_notes(hist: pd.DataFrame, cfg: dict = SECTOR_CFG) -> list[str]:
    """Plain-language summary of what moved, from the weekly positions table (index × date)."""
    n, k = cfg["top_n"], cfg["move_weeks"]
    cur = hist.iloc[:, -1].dropna().sort_values()
    notes = [f"**Top {n} now:** " + ", ".join(f"{s} ({int(p)})" for s, p in cur.head(n).items()) + "."]
    if hist.shape[1] >= 2:
        prev = hist.iloc[:, -2].dropna()
        now_top, prev_top = set(cur[cur <= n].index), set(prev[prev <= n].index)
        if now_top - prev_top:
            notes.append("**New in top {}:** {}.".format(n, ", ".join(sorted(now_top - prev_top))))
        if prev_top - now_top:
            notes.append("**Dropped out of top {}:** {}.".format(n, ", ".join(sorted(prev_top - now_top))))
        if now_top == prev_top:
            notes.append(f"Top {n} unchanged from last week (order may differ).")
    if hist.shape[1] > k:
        chg = (hist.iloc[:, -1 - k] - hist.iloc[:, -1]).dropna()     # + = moved up
        up, dn = chg.sort_values(ascending=False), chg.sort_values()
        ups = [f"{s} +{int(v)}" for s, v in up.head(3).items() if v > 0]
        dns = [f"{s} {int(v)}" for s, v in dn.head(3).items() if v < 0]
        if ups:
            notes.append(f"**Biggest climbers over {k} weeks:** " + ", ".join(ups) + ".")
        if dns:
            notes.append(f"**Biggest fallers over {k} weeks:** " + ", ".join(dns) + ".")
    streak = (hist <= n).sum(axis=1)
    steady = streak[streak >= max(3, int(hist.shape[1] * 0.75))].sort_values(ascending=False)
    if len(steady):
        notes.append(f"**Persistent leaders** (in top {n} for most of the window): "
                     + ", ".join(f"{s} ({int(v)}/{hist.shape[1]} weeks)" for s, v in steady.items()) + ".")
    if "NIFTY_50" in cur.index:
        others = cur.drop([b for b in ("NIFTY_50", "NIFTY_200", "NIFTY_500") if b in cur.index])
        above = int((others < cur["NIFTY_50"]).sum())
        notes.append(f"**{above} of {len(others)}** sectoral/thematic indices rank above Nifty 50"
                     + (" — leadership is broad." if above >= len(others) / 2 else " — leadership is narrow."))
    return notes


# ════════════════════════════════════════════════════════════════════════════
# 4. PAGE
# ════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Indian Market Breadth Tracker", page_icon="📊", layout="wide")

st.markdown("""
<style>
.chip{display:inline-block;padding:1px 10px;border-radius:999px;font-weight:600;font-size:0.85rem;white-space:nowrap}
.remark{padding:12px 14px;border-left:4px solid #94a3b8;background:rgba(148,163,184,.12);border-radius:6px;line-height:1.55}
.small{color:#6b7280;font-size:0.85rem}
</style>""", unsafe_allow_html=True)


def chip(text: str, bg: str, fg: str) -> str:
    return f'<span class="chip" style="background:{bg};color:{fg}">{text}</span>'


def page_breadth() -> None:
    with st.sidebar:
        st.header("Settings")
        window_days = st.slider("Display window (calendar days)", 30, 180, 90, step=10)
        DT_OPTIONS = {
            f"Fresh 3M low within last {CFG['latch']} sessions (recommended)": "latch",
            "Close is the 3M low today (literal)": "strict",
            f"Close within {CFG['tol_pct']:g}% of 3M low": "tol",
        }
        dt_mode = DT_OPTIONS[st.radio("Downtrend test ('at a 3-month low')", list(DT_OPTIONS), index=0)]
        use_breadth = st.toggle("Include Nifty 500 breadth (% above 50 DMA)", value=True,
                                help="Downloads ~500 stock histories; first load takes up to ~90 s, then cached for 6 h.")
        if st.button("Refresh data now", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        st.caption("A trading day's closes are picked up after 5:00 pm IST. Index data re-checked hourly, "
                   "stocks every 6 h (and again after each close), constituent list daily.")

    data_end = last_complete_session_date()                  # IST-based, never includes a live intraday bar
    idx_start = data_end - timedelta(days=window_days + 420)  # EMA warm-up + 3M low + window
    stk_start = data_end - timedelta(days=window_days + 120)  # 50 DMA needs ~75 calendar days

    st.title("Indian market breadth tracker")

    try:
        px, src = load_indices(idx_start, data_end)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not load index data: {e}")
        st.stop()

    breadth, breadth_note = None, ""
    if use_breadth:
        try:
            members = load_nifty500_symbols()
            syms = tuple(sorted(members["Symbol"].astype(str).str.strip().unique()))
            closes = load_stock_closes(syms, stk_start, data_end)
            closes = closes[closes.index <= pd.Timestamp(data_end)]
            got = closes.notna().any().sum()
            breadth = pct_above_dma(closes, CFG["dma"])
            breadth_note = f"{got} of {len(syms)} Nifty 500 stocks downloaded."
            if got < 0.9 * len(syms):
                breadth_note += " Coverage is below 90% — treat the breadth % with care."
        except Exception as e:  # noqa: BLE001
            breadth_note = f"Breadth unavailable this load: {e}"

    full = build_table(px, breadth, dt_mode, CFG)
    cutoff = pd.Timestamp(full.index.max()) - pd.Timedelta(days=window_days)
    view = full[full.index > cutoff]
    last = view.iloc[-1]
    asof = view.index[-1]

    # ---- headline -----------------------------------------------------------------
    st.markdown(
        f"**As of {asof:%d %b %Y}** the market reads "
        f"{chip(last.regime, POSTURE_COLOR[last.posture], POSTURE_TEXT[last.posture])} with posture "
        f"{chip(last.posture, POSTURE_COLOR[last.posture], POSTURE_TEXT[last.posture])} — unchanged for "
        f"**{int(last.streak)}** sessions. Breadth score **{int(last.score)}** (−6 to +6)"
        + (f" · **{last.breadth_pct:.0f}%** of Nifty 500 above {CFG['dma']} DMA "
           f"({last.breadth_zone}, {last.breadth_check.lower()})" if not pd.isna(last.breadth_pct) else "")
        + ".", unsafe_allow_html=True)
    st.markdown(f'<div class="remark">{last.remark}</div>', unsafe_allow_html=True)
    st.write("")

    # ---- cards --------------------------------------------------------------------
    cols = st.columns(4)
    for col, k in zip(cols[:3], INDICES):
        with col, st.container(border=True):
            s_ = last[f"{k}_state"]
            st.markdown(f"**{INDICES[k][1]}** &nbsp; {chip(s_, STATE_COLOR[s_], STATE_TEXT[s_])}", unsafe_allow_html=True)
            prev_close = view[k].iloc[-2] if len(view) > 1 else np.nan
            st.metric("Close", f"{last[k]:,.2f}", f"{(last[k] / prev_close - 1) * 100:+.2f}% day", label_visibility="collapsed")
            st.markdown(
                f"<div class='small'>vs {CFG['ema_fast']} EMA: <b>{last[f'{k}_vs_f']:+.2f}%</b><br>"
                f"vs {CFG['ema_slow']} EMA: <b>{last[f'{k}_vs_s']:+.2f}%</b><br>"
                f"{CFG['ema_fast']} EMA vs {CFG['ema_slow']} EMA: <b>{last[f'{k}_fs']:+.2f}%</b><br>"
                f"Above 3M closing low: <b>{last[f'{k}_off_low']:+.2f}%</b><br>"
                f"Sessions in this state: <b>{int(last[f'{k}_days'])}</b></div>", unsafe_allow_html=True)
    with cols[3], st.container(border=True):
        st.markdown("**Nifty 500 breadth**")
        if pd.isna(last.breadth_pct):
            st.metric("% above 50 DMA", "—", label_visibility="collapsed")
            st.caption(breadth_note or "Breadth switched off.")
        else:
            dlt = last.breadth_delta
            st.metric("% above 50 DMA", f"{last.breadth_pct:.1f}%",
                      None if pd.isna(dlt) else f"{dlt:+.1f} pts / {CFG['breadth_delta_n']}d", label_visibility="collapsed")
            st.markdown(f"<div class='small'>Zone: <b>{last.breadth_zone}</b><br>Check vs regime: <b>{last.breadth_check}</b><br>"
                        f"Stocks counted: <b>{int(last.breadth_n)}</b></div>", unsafe_allow_html=True)

    # ---- heatmap ------------------------------------------------------------------
    st.subheader(f"Trend state by day — last {window_days} days")
    rows = ["Nifty 50", "Midcap 150", "Smallcap 250", "Regime posture"]
    state_codes = {"S-UT": 4, "UT": 3, "TZ": 2, "DT": 1, "S-DT": 0}
    posture_codes = {"Risk-on": 4, "Constructive": 3.5, "Selective": 2, "Cautious": 1.5, "Defensive": 1, "Risk-off": 0}
    z = [[state_codes[v] for v in view[f"{k}_state"]] for k in INDICES] + [[posture_codes[v] for v in view["posture"]]]
    txt = [list(view[f"{k}_state"]) for k in INDICES] + [list(view["regime"] + " · " + view["posture"])]
    scale = [[0.0, "#991b1b"], [0.125, "#991b1b"], [0.125, "#f87171"], [0.3, "#f87171"],
             [0.3, "#fb923c"], [0.42, "#fb923c"], [0.42, "#facc15"], [0.6, "#facc15"],
             [0.6, "#4ade80"], [0.94, "#4ade80"], [0.94, "#166534"], [1.0, "#166534"]]
    xcat = list(view.index.strftime("%d %b %y"))
    heat = go.Figure(go.Heatmap(z=z[::-1], x=xcat, y=rows[::-1], text=txt[::-1], zmin=0, zmax=4,
                                colorscale=scale, showscale=False, xgap=1, ygap=3,
                                hovertemplate="%{x}<br>%{y}: %{text}<extra></extra>"))
    heat.update_layout(height=230, margin=dict(l=10, r=10, t=10, b=10),
                       xaxis=dict(showgrid=False, type="category", nticks=8), yaxis=dict(showgrid=False))
    st.plotly_chart(heat, width="stretch")
    st.markdown(" ".join(chip(s, STATE_COLOR[s], STATE_TEXT[s]) for s in SCORE) + " &nbsp;·&nbsp; Posture: " +
                " ".join(chip(p, POSTURE_COLOR[p], POSTURE_TEXT[p]) for p in POSTURE_COLOR), unsafe_allow_html=True)

    # ---- score + breadth ----------------------------------------------------------
    c1, c2 = st.columns(2)
    with c1:
        st.subheader("Breadth score (index trend states)")
        f = go.Figure(go.Bar(x=view.index, y=view["score"],
                             marker_color=["#16a34a" if v > 0 else "#dc2626" if v < 0 else "#9ca3af" for v in view["score"]],
                             hovertemplate="%{x|%d %b %Y}: %{y}<extra></extra>"))
        f.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(range=[-6.5, 6.5], dtick=2),
                        xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"])]))
        st.plotly_chart(f, width="stretch")
    with c2:
        st.subheader(f"% of Nifty 500 above {CFG['dma']} DMA")
        if view["breadth_pct"].notna().any():
            f = go.Figure()
            bands = [(0, 20, "rgba(153,27,27,.12)"), (20, 40, "rgba(248,113,113,.10)"), (40, 60, "rgba(250,204,21,.10)"),
                     (60, 80, "rgba(74,222,128,.10)"), (80, 100, "rgba(22,101,52,.12)")]
            for lo, hi, colr in bands:
                f.add_hrect(y0=lo, y1=hi, fillcolor=colr, line_width=0)
            f.add_trace(go.Scatter(x=view.index, y=view["breadth_pct"], mode="lines", line=dict(width=2.5, color="#2563eb"),
                                   hovertemplate="%{x|%d %b %Y}: %{y:.1f}%<extra></extra>"))
            th = view[view["thrust"]]
            if len(th):
                f.add_trace(go.Scatter(x=th.index, y=th["breadth_pct"], mode="markers", marker=dict(size=10, color="#16a34a"),
                                       name="Breadth thrust", hovertemplate="Thrust %{x|%d %b}<extra></extra>"))
            f.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(range=[0, 100], ticksuffix="%"),
                            showlegend=False, xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"])]))
            st.plotly_chart(f, width="stretch")
        else:
            st.info(breadth_note or "Breadth switched off in the sidebar.")

    # ---- price vs EMAs ------------------------------------------------------------
    st.subheader("Index vs its EMAs (strip = daily state)")
    pc = st.columns(3)
    for col, k in zip(pc, INDICES):
        with col:
            f = go.Figure()
            f.add_trace(go.Scatter(x=view.index, y=view[k], name="Close", line=dict(width=2, color="#111827")))
            f.add_trace(go.Scatter(x=view.index, y=view[f"{k}_ema_f"], name=f"{CFG['ema_fast']} EMA", line=dict(width=1.5, color="#3b82f6")))
            f.add_trace(go.Scatter(x=view.index, y=view[f"{k}_ema_s"], name=f"{CFG['ema_slow']} EMA",
                                   line=dict(width=1.5, color="#f59e0b", dash="dash")))
            lo_ = view[[k, f"{k}_ema_f", f"{k}_ema_s"]].min().min()
            hi_ = view[[k, f"{k}_ema_f", f"{k}_ema_s"]].max().max()
            pad = (hi_ - lo_) * 0.08
            f.add_trace(go.Bar(x=view.index, y=[pad * 0.6] * len(view), base=lo_ - pad,
                               marker_color=[STATE_COLOR[s] for s in view[f"{k}_state"]], showlegend=False,
                               hovertext=list(view[f"{k}_state"]), hoverinfo="text+x"))
            f.update_layout(title=INDICES[k][1], height=300, margin=dict(l=10, r=10, t=40, b=10), bargap=0,
                            legend=dict(orientation="h", y=-0.15), yaxis=dict(range=[lo_ - pad, hi_ + pad / 2]),
                            hovermode="x unified", xaxis=dict(rangebreaks=[dict(bounds=["sat", "mon"])]))
            st.plotly_chart(f, width="stretch")

    # ---- daily log ----------------------------------------------------------------
    st.subheader("Daily log with remarks (newest first)")
    log = view.sort_index(ascending=False).reset_index()
    show = pd.DataFrame({
        "Date": log["date"].dt.strftime("%d %b %Y"),
        "Nifty 50": log["n50_state"], "Midcap 150": log["mid_state"], "Smallcap 250": log["sml_state"],
        "Score": log["score"], "Δ score": log["d_score"],
        "% > 50 DMA": log["breadth_pct"].round(1), "Breadth check": log["breadth_check"],
        "Regime": log["regime"], "Posture": log["posture"], "Remarks": log["remark"],
    })


    def _style(v):
        if v in STATE_COLOR:
            return f"background-color:{STATE_COLOR[v]};color:{STATE_TEXT[v]};font-weight:600"
        if v in POSTURE_COLOR:
            return f"background-color:{POSTURE_COLOR[v]};color:{POSTURE_TEXT[v]};font-weight:600"
        if v in CHECK_COLOR:
            return f"background-color:{CHECK_COLOR[v]};color:#111827"
        return ""


    styled = (show.style.map(_style, subset=["Nifty 50", "Midcap 150", "Smallcap 250", "Posture", "Breadth check"])
              .format({"Δ score": lambda v: "" if pd.isna(v) else f"{v:+.0f}",
                       "% > 50 DMA": lambda v: "" if pd.isna(v) else f"{v:.1f}%"}))
    st.dataframe(styled, width="stretch", hide_index=True, height=520,
                 column_config={"Remarks": st.column_config.TextColumn(width="large")})

    xbuf = io.BytesIO()
    export = view.reset_index()
    export["date"] = export["date"].dt.date
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
        export.to_excel(xw, sheet_name="Daily", index=False)
        pd.DataFrame(list(CFG.items()), columns=["setting", "value"]).astype(str).to_excel(xw, sheet_name="Rules", index=False)
    d1, d2 = st.columns(2)
    d1.download_button("Download Excel", xbuf.getvalue(), f"market_breadth_{asof:%Y%m%d}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    d2.download_button("Download CSV", export.to_csv(index=False).encode(), f"market_breadth_{asof:%Y%m%d}.csv",
                       "text/csv", width="stretch")

    # ---- rules + sources -----------------------------------------------------------
    with st.expander("Rules used", expanded=False):
        F, S, L = CFG["ema_fast"], CFG["ema_slow"], CFG["lookback"]
        st.markdown(textwrap.dedent(f"""
    **1. Trend state of each index (daily closes)**
    - **S-UT**: close above both the {F} EMA and the {S} EMA, *and* the {F} EMA above the {S} EMA.
    - **UT**: close above the {S} EMA but at or below the {F} EMA (pullback inside an uptrend).
      Also UT: close above both EMAs while the {F} EMA is still at or below the {S} EMA (early, unconfirmed uptrend).
    - **DT**: close at or below the {S} EMA *and* at a 3-month low.
    - **S-DT**: {"DT, and also " if CFG["sdt_needs_3m_low"] else ""}close below both the {F} EMA and the {S} EMA, *and* the {F} EMA below the {S} EMA.
      Otherwise a DT day stays DT (e.g. below both EMAs but the {F} EMA still above the {S} EMA: a fresh breakdown).
    - **TZ**: everything else (below the {S} EMA, not DT).
    - 3-month low = lowest close of the last {L} sessions. Recommended test: a fresh low within the last {CFG['latch']} sessions;
      literal: today's close is the low; tolerance: within {CFG['tol_pct']:g}% of it.

    **2. Breadth score** — S-UT +2, UT +1, TZ 0, DT −1, S-DT −2, summed (−6 to +6), indices weighted equally.

    **3. Regime and posture (first match wins)**
    1. All three uptrend → Broad uptrend (strong if all S-UT) → Risk-on
    2. All three DT → Broad downtrend (strong if all S-DT) → Risk-off
    3. ≥1 uptrend and ≥1 downtrend → Divergent → Cautious
    4. Only Nifty 50 up → Narrow: large-caps only → Cautious
    5. Nifty 50 + one broader index up → Breadth narrowing → Selective
    6. Mid & small up, Nifty 50 not → Broader market leading → Constructive
    7. Only one of mid/small up → Mixed → Selective
    8. None up, none DT → Correction / no trend → Defensive
    9. None up, one or two DT → Weakening → Defensive

    **4. Nifty 500 participation** — % of current Nifty 500 members closing above their own {CFG['dma']}-day simple MA.
    - Zones: ≥80 Overheated · 60–80 Healthy · 40–60 Neutral · 20–40 Weak · <20 Washed-out.
    - Breadth check: in Risk-on / Constructive regimes, ≥{CFG['breadth_confirm_up']}% confirms and <{CFG['breadth_diverge_up']}% diverges;
      in Defensive / Risk-off regimes, ≤{CFG['breadth_confirm_dn']}% confirms and ≥{CFG['breadth_diverge_dn']}% diverges; otherwise neutral.
    - Breadth thrust (simplified): breadth goes from below {CFG['thrust_from']}% to above {CFG['thrust_to']}% within {CFG['thrust_n']} sessions.
    - The breadth check is reported in the remark; it does not change the regime or posture.

    **5. Remark** — regime meaning · strongest / weakest index vs {S} EMA · risk appetite · that day's events
    (EMA crosses, fresh 3M lows) · participation and breadth check · score trajectory (±{CFG['traj_thresh']} in {CFG['traj_n']} sessions).

    *Posture is a rules output, not investment advice.*
    """))

    st.caption("Data — " + " · ".join(f"{INDICES[k][1]}: {v}" for k, v in src.items())
               + (f" · Breadth: Yahoo Finance adjusted closes; constituents from niftyindices.com. {breadth_note}" if use_breadth else "")
               + f" · Rendered {datetime.now(IST):%d %b %Y %H:%M} IST")


def _ret_color(v):
    """Green for gains, red for losses; stronger colour for bigger moves (no matplotlib needed)."""
    if pd.isna(v):
        return ""
    a = min(abs(v) / 0.15, 1.0)                     # full colour at ±15%
    rgb = (22, 163, 74) if v >= 0 else (220, 38, 38)
    return f"background-color: rgba({rgb[0]},{rgb[1]},{rgb[2]},{0.12 + 0.55 * a:.2f})"


def _pos_color(p, n=17):
    if pd.isna(p):
        return ""
    x = (p - 1) / max(n - 1, 1)                     # 0 = best, 1 = worst
    r, g = (int(22 + 233 * min(1, 2 * x)), int(163 + 60 * min(1, 2 * x))) if x <= 0.5 else \
        (int(255 - 35 * (2 * x - 1)), int(223 - 185 * (2 * x - 1)))
    b = 74 if x <= 0.5 else int(80 - 42 * (2 * x - 1))
    txt = "#ffffff" if x < 0.15 or x > 0.85 else "#111827"
    return f"background-color: rgb({r},{g},{b}); color: {txt}"


def page_sectors() -> None:
    with st.sidebar:
        st.header("Sector settings")
        weeks = st.slider("Weeks of rank history", 4, 52, 12)
        basis = BASIS[st.radio("Rank by", list(BASIS), index=0)]
        lb = st.radio("3M return lookback", ["90 days (a true 3 months)", "60 days (what my Google Sheet uses)"],
                      index=0, help="Your sheet's '3M Return' formula uses TODAY()-60, i.e. 2 months.")
        days_3m = SECTOR_CFG["days_3m_true"] if lb.startswith("90") else SECTOR_CFG["days_3m_sheet"]
        if st.button("Refresh data now", width="stretch", key="sec_refresh"):
            st.cache_data.clear()
            st.rerun()
        st.caption("Closes are picked up after 5:00 pm IST; index data re-checked hourly.")

    st.title("Sector rotation")
    data_end = last_complete_session_date()
    data, src = load_sectors(data_end - timedelta(days=SECTOR_CFG["history_days"]), data_end)
    failed = [n for n in SECTORS if n not in data]
    if not data:
        st.error("Could not load any sector index. Details: " + "; ".join(f"{k}: {v}" for k, v in src.items()))
        st.stop()
    if failed:
        st.warning("Not loaded this time (left out of the ranking): " + ", ".join(failed))

    latest = max(df.index.max() for df in data.values())
    snap = order_by(sector_snapshot(data, latest, days_3m), basis)
    dates = week_end_dates(data, latest, weeks)
    hist, _ = rank_history(data, dates, days_3m, basis)
    hist = hist.reindex(snap.index)
    prev_pos = hist.iloc[:, -2] if hist.shape[1] >= 2 else pd.Series(np.nan, index=hist.index)

    basis_label = next(k for k, v in BASIS.items() if v == basis)
    st.markdown(f"**As of {latest:%d %b %Y}** · ranked by **{basis_label}** · 3M = {days_3m} days. "
                f"Leaders: **{', '.join(snap.index[:3])}**.")
    notes_html = "<br>".join(re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", n) for n in rotation_notes(hist))
    st.markdown(f'<div class="remark">{notes_html}</div>', unsafe_allow_html=True)
    st.write("")

    # ---- Sectors table (the 'Sectors' tab) -------------------------------------------
    st.subheader("Sector ranking (your 'Sectors' tab, always sorted)")
    tbl = pd.DataFrame({
        "Rank": snap["pos"], "Index": snap.index, "Type": snap["group"],
        "Δ rank 1W": (prev_pos - snap["pos"]).reindex(snap.index),
        "Close": snap["close"], "52W High": snap["high_52w"], "52W Low": snap["low_52w"],
        "1W": snap["r_1w"], "1M": snap["r_1m"], "3M": snap["r_3m"], "6M ex 1M": snap["r_6m_ex_1m"],
        "6M": snap["r_6m"], "1Y": snap["r_1y"], "Away from 52W Low": snap["away_low"],
        "Off 52W High": snap["off_high"], "Rank(1,3,6)": snap["score"],
    })
    ret_cols = ["1W", "1M", "3M", "6M ex 1M", "6M", "1Y", "Away from 52W Low", "Off 52W High"]
    sty = (tbl.style.map(_ret_color, subset=ret_cols).map(_pos_color, subset=["Rank"])
           .map(lambda v: "color:#6b7280;font-style:italic" if v == "Benchmark" else "", subset=["Type"])
           .format({**{c: "{:+.2%}" for c in ret_cols}, "Close": "{:,.2f}", "52W High": "{:,.2f}",
                    "52W Low": "{:,.2f}", "Rank(1,3,6)": "{:.2f}",
                    "Δ rank 1W": lambda v: "" if pd.isna(v) else ("—" if v == 0 else f"{v:+.0f}")}, na_rep=""))
    st.dataframe(sty, width="stretch", hide_index=True, height=640)
    st.caption("Δ rank 1W: + = moved up the table since last week's close. Benchmarks (Nifty 50/200/500) are ranked "
               "alongside, exactly as in your sheet.")

    # ---- Rank history heatmap (the 'Analysis' tab, transposed) -------------------------
    st.subheader(f"Rank history — last {len(dates)} weeks")
    cols = [d.strftime("%d %b %y") for d in hist.columns]
    z = hist.to_numpy(dtype=float)
    heat = go.Figure(go.Heatmap(
        z=z, x=cols, y=list(hist.index),
        text=[[("" if np.isnan(v) else str(int(v))) for v in row] for row in z], texttemplate="%{text}", zmin=1, zmax=len(hist), showscale=False, xgap=2, ygap=2,
        colorscale=[[0, "#166534"], [0.25, "#4ade80"], [0.5, "#facc15"], [0.75, "#f87171"], [1, "#991b1b"]],
        hovertemplate="%{y}<br>%{x}: rank %{z}<extra></extra>"))
    heat.update_layout(height=40 + 26 * len(hist), margin=dict(l=10, r=10, t=10, b=10),
                       yaxis=dict(autorange="reversed"), xaxis=dict(side="top", type="category"))
    st.plotly_chart(heat, width="stretch")
    st.caption("Rows ordered by today's rank. Each column is the last trading day of that week; the newest column "
               "is the latest close (the current week is provisional until Friday).")

    # ---- Bump chart -----------------------------------------------------------------
    st.subheader("Rank movement")
    pick = st.multiselect("Indices to plot", list(hist.index), default=list(snap.index[:5]))
    if pick:
        f = go.Figure()
        for s in pick:
            f.add_trace(go.Scatter(x=cols, y=hist.loc[s], mode="lines+markers", name=s,
                                   hovertemplate=f"{s}<br>%{{x}}: rank %{{y}}<extra></extra>"))
        f.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10),
                        yaxis=dict(range=[len(hist) + 0.5, 0.5], dtick=1, title="Rank (1 = best)"),
                        xaxis=dict(type="category"), legend=dict(orientation="h", y=-0.2))
        st.plotly_chart(f, width="stretch")

    # ---- Sheet-style Analysis grid --------------------------------------------------
    grid = pd.DataFrame({c: hist[d].dropna().sort_values().index.to_list() for c, d in zip(cols, hist.columns)})
    grid.index = range(1, len(grid) + 1)
    grid.index.name = "Rank"
    with st.expander("Sheet-style view (same layout as your 'Analysis' tab)"):
        st.dataframe(grid.style.map(lambda v: f"background-color:{SECTOR_COLOR.get(v, '')};color:#111827"),
                     width="stretch", height=40 + 35 * len(grid))

    # ---- Downloads ---------------------------------------------------------------------
    xbuf = io.BytesIO()
    with pd.ExcelWriter(xbuf, engine="openpyxl") as xw:
        tbl.to_excel(xw, sheet_name="Sectors", index=False)
        grid.to_excel(xw, sheet_name="Analysis")
        h = hist.copy()
        h.columns = cols
        h.to_excel(xw, sheet_name="Rank history")
    d1, d2 = st.columns(2)
    d1.download_button("Download Excel (Sectors + Analysis)", xbuf.getvalue(), f"sector_rotation_{latest:%Y%m%d}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", width="stretch")
    d2.download_button("Download CSV (Sectors)", tbl.to_csv(index=False).encode(), f"sectors_{latest:%Y%m%d}.csv",
                       "text/csv", width="stretch")

    with st.expander("How the numbers are calculated"):
        st.markdown(textwrap.dedent(f"""
        - **Returns** follow your sheet: today's close ÷ the first close on or after (as-of date − N calendar days) − 1,
          with N = 7 (1W), 30 (1M), **{days_3m} (3M)**, 180 (6M), 365 (1Y). The as-of date is the last close, not the
          calendar date, so a weekend view still compares Friday with Friday.
        - **6M ex 1M** = close 1 month ago ÷ close 6 months ago − 1. Your sheet subtracts the two percentages (6M − 1M),
          which is slightly off; this is the compounded version.
        - **52W High / Low**: highest daily high and lowest daily low of the last 365 days (Google's high52 / low52 work the same way).
        - **Rank(1,3,6)** = 0.5 × rank of 1M + 0.3 × rank of 3M + 0.2 × rank of 6M, each rank 1 = best (Google's RANK,
          ties share a rank). **Lower is better.** Ties are broken by the better 1M return.
        - **Rank history**: the same calculation repeated as of the last trading day of each past week, so the
          'Analysis' tab fills itself in instead of being copied by hand.
        - Data: niftyindices.com daily OHLC (official); Yahoo Finance used only if niftyindices fails for an index.
        """))
    st.caption("Data — " + " · ".join(f"{k}: {v}" for k, v in src.items())
               + f" · Rendered {datetime.now(IST):%d %b %Y %H:%M} IST")


# ════════════════════════════════════════════════════════════════════════════
# 5. NAVIGATION — two pages in one app
# ════════════════════════════════════════════════════════════════════════════
_default = os.environ.get("BREADTH_DEFAULT_PAGE", "breadth")   # only used for offline testing
st.navigation([
    st.Page(page_breadth, title="Market breadth", icon="📊", url_path="breadth", default=_default == "breadth"),
    st.Page(page_sectors, title="Sector rotation", icon="🔄", url_path="sectors", default=_default == "sectors"),
]).run()
