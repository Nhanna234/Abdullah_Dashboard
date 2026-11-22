"""
Sector rotation engine for the dashboard's "Sector rotation" page (no Streamlit imports, testable on its own).

* Which indices: every NSE sectoral and thematic index (niftyindices.com), minus variants that are
  re-weighted copies of another index or are not sector/theme groupings (EXCLUDE_RULES), plus
  Nifty 50 / 200 / 500 as benchmarks. The list below was checked against niftyindices.com on
  22 Nov 2026; the app also reads NSE's index pages at run time, so an index NSE launches later
  is picked up automatically (and shown as "new").
* BSE indices are added only where NSE has no equivalent. As of 22 Nov 2026 the only such case is
  BSE 500 Textiles (launched 13 Nov 2026), which has no free data feed or constituent file that
  works from a cloud server, so it is listed in BSE_NOT_ADDED instead.
* Prices: daily OHLC from the niftyindices.com historical API; the index's API name comes from
  NSE's own IndexMapping.json (the same lookup the website uses), with fallbacks.
* Constituents: the CSV linked from each index's page on niftyindices.com.
* Ranking: your Google Sheet's Rank(1,3,6) = 0.5 x rank(1M) + 0.3 x rank(3M) + 0.2 x rank(6M).
"""

from __future__ import annotations

import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

NI_BASE = "https://www.niftyindices.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
MAPPING_URL = "https://liveindexsa.niftyindices.com/assets/json/IndexMapping.json"
CONSTITUENT_MIRRORS = ["https://www.niftyindices.com/IndexConstituent/{f}",
                       "https://niftyindices.com/IndexConstituent/{f}",
                       "https://nsearchives.nseindia.com/content/indices/{f}"]
CATEGORY_PAGES = {"Sector": "/indices/equity/sectoral-indices", "Theme": "/indices/equity/thematic-indices"}

SECTOR_CFG = {
    "w_1m": 0.5, "w_3m": 0.3, "w_6m": 0.2,   # Rank(1,3,6) weights, same as the Google Sheet
    "days": {"1w": 7, "1m": 30, "6m": 180, "1y": 365},  # calendar-day lookbacks, same as the sheet
    "days_3m_true": 90,         # a real 3 months
    "days_3m_sheet": 60,        # what the sheet's '3M Return' formula actually uses (TODAY()-60)
    "top_n": 5,                 # 'leaders' = top 5 positions
    "move_weeks": 4,            # rank change measured over this many weeks
    "max_weeks": 26,            # longest rank history offered
    "history_days": 560,        # data fetched: 26 weeks of history + 1 year look-back
    "workers": 5,               # parallel downloads from niftyindices.com
}

# ---------------------------------------------------------------- the index list
_S = "/indices/equity/sectoral-indices/"
_T = "/indices/equity/thematic-indices/"
_TC = "/indices/equity/Thematic-indices/"
# (name as NSE writes it, group, page on niftyindices.com)
CATALOG = [
    ("Nifty Auto", "Sector", _S + "nifty-auto"),
    ("Nifty Bank", "Sector", _S + "nifty-bank"),
    ("Nifty Capital Goods", "Sector", _S + "nifty-capital-goods"),
    ("Nifty Cement", "Sector", _S + "nifty-cement"),
    ("Nifty Chemicals", "Sector", _S + "nifty-chemicals"),
    ("Nifty Commercial & Transport Services", "Sector", _S + "nifty-commercial---transport-services"),
    ("Nifty Construction", "Sector", _S + "nifty-construction"),
    ("Nifty Consumer Durables", "Sector", _S + "nifty-consumer-durables-index"),
    ("Nifty Consumer Services", "Sector", _S + "nifty-consumer-services"),
    ("Nifty Financial Services", "Sector", _S + "nifty-financial-services"),
    ("Nifty Financial Services Ex Bank", "Sector", _S + "nifty-financial--services-ex-bank"),
    ("Nifty FMCG", "Sector", _S + "nifty-fmcg"),
    ("Nifty Healthcare", "Sector", _S + "nifty-healthcare-index"),
    ("Nifty Hospitals", "Sector", _S + "nifty-hospitals"),
    ("Nifty Housing Finance", "Sector", _S + "nifty-housing-finance"),
    ("Nifty Insurance", "Sector", _S + "nifty-insurance"),
    ("Nifty IT", "Sector", _S + "nifty-it"),
    ("Nifty Media", "Sector", _S + "nifty-media"),
    ("Nifty Metal", "Sector", _S + "nifty-metal"),
    ("Nifty NBFC", "Sector", _S + "nifty-nbfc"),
    ("Nifty Oil and Gas", "Sector", _S + "nifty-oil-and-gas-index"),
    ("Nifty Pharma", "Sector", _S + "nifty-pharma"),
    ("Nifty Power", "Sector", _S + "nifty-power"),
    ("Nifty Private Bank", "Sector", _S + "nifty-private-bank"),
    ("Nifty PSU Bank", "Sector", _S + "nifty-psu-bank"),
    ("Nifty Realty", "Sector", _S + "nifty-realty"),
    ("Nifty REITs & Realty", "Sector", _S + "nifty-reits---realty"),
    ("Nifty Retail", "Sector", _S + "nifty-retail"),
    ("Nifty Telecommunications", "Sector", _S + "nifty-telecommunications"),
    ("Nifty500 Healthcare", "Sector", _S + "nifty500-healthcare"),
    ("Nifty MidSmall Financial Services", "Sector", _S + "nifty-midsmall--financial-services"),
    ("Nifty MidSmall Healthcare", "Sector", _S + "nifty-midsmallhealthcare"),
    ("Nifty MidSmall IT & Telecom", "Sector", _S + "nifty-midsmall--it-telecom"),
    ("Nifty AI Catalysts", "Theme", _T + "nifty-ai-catalysts"),
    ("Nifty Capital Markets", "Theme", _T + "nifty-capital-markets"),
    ("Nifty Commodities", "Theme", _T + "nifty-commodities"),
    ("Nifty Conglomerate 50", "Theme", _T + "nifty-conglomerate-50"),
    ("Nifty Core Housing", "Theme", _T + "nifty-core--housing"),
    ("Nifty CPSE", "Theme", _T + "nifty-cpse"),
    ("Nifty Energy", "Theme", _T + "nifty-energy"),
    ("Nifty EV & New Age Automotive", "Theme", _TC + "nifty-ev-new--age-automotive"),
    ("Nifty Housing", "Theme", _T + "nifty--housing"),
    ("Nifty India Consumption", "Theme", _T + "nifty-india-consumption"),
    ("Nifty India Defence", "Theme", _T + "nifty-india-defence"),
    ("Nifty India Digital", "Theme", _T + "nifty-india-digital"),
    ("Nifty India Infrastructure & Logistics", "Theme", _T + "nifty-india-infrastructure-logistics"),
    ("Nifty India Internet", "Theme", _T + "nifty-india-internet"),
    ("Nifty India Manufacturing", "Theme", _T + "nifty-india-manufacturing"),
    ("Nifty India New Age Consumption", "Theme", _TC + "nifty-india-new--age-consumption"),
    ("Nifty India Railways PSU", "Theme", _T + "nifty-india-railways-psu"),
    ("Nifty India Tourism", "Theme", _T + "nifty-india-tourism"),
    ("Nifty Infrastructure", "Theme", _T + "nifty-infrastructure"),
    ("Nifty MidSmall India Consumption", "Theme", _T + "nifty-mid-small-india-consumption"),
    ("Nifty MNC", "Theme", _T + "nifty-mnc"),
    ("Nifty Mobility", "Theme", _T + "nifty--mobility"),
    ("Nifty PSE", "Theme", _T + "nifty-pse"),
    ("Nifty Rural", "Theme", _TC + "nifty--rural"),
    ("Nifty Non-Cyclical Consumer", "Theme", _T + "nifty-non--cyclical-consumer"),
    ("Nifty Services Sector", "Theme", _T + "nifty-services-sector"),
    ("Nifty Small Finance Banks & Microfinance Institutions", "Theme",
     _T + "nifty-small-finance-banks---microfinance-institutions"),
    ("Nifty Sugar & Ethanol", "Theme", _T + "nifty-sugar---ethanol"),
    ("Nifty Transportation & Logistics", "Theme", _TC + "nifty--transportation-logistics"),
    ("Nifty Waves", "Theme", _T + "nifty-waves"),
    ("Nifty 50", "Benchmark", None),
    ("Nifty 200", "Benchmark", None),
    ("Nifty 500", "Benchmark", None),
]
GROUP = {n: g for n, g, _ in CATALOG}

# "Major indices" (the default view): NSE's long-established sector indices, the themes your Google Sheet
# tracked plus a few widely followed ones, and the three benchmarks. The full list stays available as "All NSE".
MAJOR = [
    "Nifty Auto", "Nifty Bank", "Nifty Private Bank", "Nifty PSU Bank", "Nifty Financial Services",
    "Nifty FMCG", "Nifty Consumer Durables", "Nifty Healthcare", "Nifty Pharma", "Nifty IT", "Nifty Media",
    "Nifty Metal", "Nifty Oil and Gas", "Nifty Realty",
    "Nifty Energy", "Nifty Infrastructure", "Nifty India Consumption", "Nifty Commodities", "Nifty MNC",
    "Nifty Services Sector", "Nifty PSE", "Nifty India Defence", "Nifty Capital Markets", "Nifty India Manufacturing",
    "Nifty 50", "Nifty 200", "Nifty 500",
]

# NSE indices left out on purpose: (pattern, reason). Checked against every index NSE lists.
EXCLUDE_RULES = [
    (r"25/50|25% cap|equal weight|50:30:20|90:10", "re-weighted copy of another index (same stocks)"),
    (r"\besg\b", "ESG screen of the Nifty 100, not a sector"),
    (r"shariah|ahimsa", "faith-based screen of a broad index, not a sector"),
    (r"liquid 15", "liquidity strategy, not a sector"),
    (r"corporate group|maatr", "business-group basket, not a sector"),
    (r"reits & invits", "REIT / InvIT units, not company shares"),
    (r"sme emerge", "SME platform stocks (thinly traded)"),
    (r"^nifty ipo$", "recently listed companies, not a sector"),
]
BSE_NOT_ADDED = [
    ("BSE 500 Textiles", "the only BSE sector with no NSE equivalent (launched 13 Nov 2026); BSE offers no free "
                         "price feed or constituent file that works from a cloud server"),
]
# Every other BSE sectoral / thematic index duplicates an NSE index already in the list
# (e.g. BSE Auto = Nifty Auto, BSE Bankex = Nifty Bank, BSE Capital Goods = Nifty Capital Goods,
# BSE Power = Nifty Power, BSE Telecommunication = Nifty Telecommunications, BSE Hospitals = Nifty Hospitals,
# BSE India Defence = Nifty India Defence, BSE Internet Economy = Nifty India Internet) or is a
# broad mix of sectors already covered (BSE Industrials, Utilities, Teck, Consumer Discretionary).

# Indices made only of lenders: skipped in the drill-down's default choice when lenders are excluded
LENDER_INDICES = {"Nifty Bank", "Nifty Private Bank", "Nifty PSU Bank", "Nifty NBFC", "Nifty Housing Finance",
                  "Nifty Small Finance Banks & Microfinance Institutions"}

# Names already proven on the niftyindices API, and Yahoo tickers usable as a fallback
KNOWN_API = {"Nifty 50": "NIFTY 50", "Nifty 200": "NIFTY 200", "Nifty 500": "NIFTY 500",
             "Nifty Bank": "NIFTY BANK", "Nifty IT": "NIFTY IT", "Nifty Auto": "NIFTY AUTO",
             "Nifty FMCG": "NIFTY FMCG", "Nifty Pharma": "NIFTY PHARMA", "Nifty Metal": "NIFTY METAL",
             "Nifty Media": "NIFTY MEDIA", "Nifty Realty": "NIFTY REALTY", "Nifty Energy": "NIFTY ENERGY",
             "Nifty MNC": "NIFTY MNC", "Nifty Infrastructure": "NIFTY INFRA",
             "Nifty India Consumption": "NIFTY CONSUMPTION", "Nifty Services Sector": "NIFTY SERV SECTOR",
             "Nifty Commodities": "NIFTY COMMODITIES", "Nifty PSU Bank": "NIFTY PSU BANK",
             "Nifty Private Bank": "NIFTY PVT BANK", "Nifty Financial Services": "NIFTY FIN SERVICE"}
YAHOO = {"Nifty 50": "^NSEI", "Nifty 200": "^CNX200", "Nifty 500": "^CRSLDX", "Nifty Bank": "^NSEBANK",
         "Nifty IT": "^CNXIT", "Nifty Auto": "^CNXAUTO", "Nifty FMCG": "^CNXFMCG", "Nifty Pharma": "^CNXPHARMA",
         "Nifty Metal": "^CNXMETAL", "Nifty Media": "^CNXMEDIA", "Nifty Realty": "^CNXREALTY",
         "Nifty Energy": "^CNXENERGY", "Nifty MNC": "^CNXMNC", "Nifty Infrastructure": "^CNXINFRA",
         "Nifty India Consumption": "^CNXCONSUM", "Nifty Services Sector": "^CNXSERVICE",
         "Nifty Commodities": "^CNXCMDT", "Nifty PSU Bank": "^CNXPSUBANK"}


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("&", "and"))


def excluded_reason(name: str) -> str | None:
    for pat, why in EXCLUDE_RULES:
        if re.search(pat, name, re.I):
            return why
    return None


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        s.get(f"{NI_BASE}/reports/historical-data", timeout=15)
    except requests.RequestException:
        pass
    return s


# ---------------------------------------------------------------- discovery of new NSE indices
_LINK = re.compile(r'<a[^>]+href="([^"]*/indices/equity/(?:sectoral|thematic)-indices/[^"#?]+)"[^>]*>(.*?)</a>',
                   re.I | re.S)


def discover(session_get=None) -> dict:
    """Reads NSE's sectoral and thematic index pages. Returns {'new': [(name, group, page)], 'excluded':
    [(name, reason)], 'ok': bool, 'note': str}. Indices already in CATALOG are not repeated."""
    get = session_get or (lambda url: requests.get(url, headers={"User-Agent": UA}, timeout=25))
    known = {_norm(n) for n, _, _ in CATALOG}
    new, excl, seen, errors = [], [], set(), []
    for group, path in CATEGORY_PAGES.items():
        try:
            html = get(NI_BASE + path).text
        except Exception as e:  # noqa: BLE001
            errors.append(f"{group}: {e}")
            continue
        for href, text in _LINK.findall(html):
            name = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
            if not name or len(name) > 90 or not name.lower().startswith("nifty"):
                continue
            key = _norm(name)
            if key in seen:
                continue
            seen.add(key)
            why = excluded_reason(name)
            if why:
                excl.append((name, why))
            elif key not in known:
                page = href if href.startswith("/") else re.sub(r"^https?://[^/]+", "", href)
                new.append((name, group, page))
    return {"new": new, "excluded": excl, "ok": not errors,
            "note": "; ".join(errors) if errors else f"read {len(seen)} index links from niftyindices.com"}


# ---------------------------------------------------------------- API names (NSE's IndexMapping.json)
def parse_mapping(records) -> dict:
    """{normalised long name: [candidate API names]} from NSE's IndexMapping.json, whatever its field names."""
    out = {}
    for rec in records if isinstance(records, list) else []:
        if not isinstance(rec, dict):
            continue
        long_key = next((k for k in rec if "long" in k.lower()), None)
        if not long_key or not rec.get(long_key):
            continue
        prefer = sorted((k for k in rec if k != long_key and isinstance(rec[k], str) and rec[k].strip()),
                        key=lambda k: (0 if "trad" in k.lower() else 1 if "name" in k.lower() else 2))
        out.setdefault(_norm(rec[long_key]), [])
        for k in prefer:
            v = rec[k].strip().upper()
            if v.startswith("NIFTY") and v not in out[_norm(rec[long_key])]:
                out[_norm(rec[long_key])].append(v)
    return out


def load_mapping() -> dict:
    try:
        r = requests.get(MAPPING_URL, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        return parse_mapping(r.json())
    except Exception:  # noqa: BLE001
        return {}


def api_candidates(name: str, mapping: dict) -> list[str]:
    c = list(mapping.get(_norm(name), []))
    for extra in (KNOWN_API.get(name), name.upper(), name.upper().replace(" AND ", " & ")):
        if extra and extra not in c:
            c.append(extra)
    return c


# ---------------------------------------------------------------- prices
def _ni_chunk(s, api_name: str, index_name: str, start: date, end: date) -> list[dict]:
    cinfo = (f"{{'name':'{api_name}','startDate':'{start:%d-%b-%Y}',"
             f"'endDate':'{end:%d-%b-%Y}','indexName':'{index_name}'}}")
    r = s.post(f"{NI_BASE}/BackPage/getHistoricaldatatabletoString", data=json.dumps({"cinfo": cinfo}),
               headers={"Content-Type": "application/json; charset=utf-8",
                        "Accept": "application/json, text/javascript, */*; q=0.01",
                        "X-Requested-With": "XMLHttpRequest", "Origin": NI_BASE,
                        "Referer": f"{NI_BASE}/reports/historical-data"}, timeout=30)
    r.raise_for_status()
    txt = r.text.strip()
    if not txt.startswith(("[", "{")):
        raise ValueError("non-JSON answer (blocked or changed)")
    data = json.loads(txt)
    if isinstance(data, dict):
        data = data.get("d", [])
        data = json.loads(data) if isinstance(data, str) else data
    return data or []


def _rows_to_ohlc(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    idx = pd.to_datetime(df["HistoricalDate"], format="%d %b %Y")
    out = pd.DataFrame({c.lower(): pd.to_numeric(df[c], errors="coerce").to_numpy() for c in ("CLOSE", "HIGH", "LOW")},
                       index=idx).sort_index()
    return out[~out.index.duplicated(keep="last")].dropna(subset=["close"])


def fetch_ni(name: str, mapping: dict, start: date, end: date) -> tuple[pd.DataFrame, str]:
    """Daily OHLC from niftyindices.com. Tries each candidate API name on the latest year first."""
    s = _session()
    first_start = max(start, end - timedelta(days=360))
    used, rows, errs = None, [], []
    for cand in api_candidates(name, mapping):
        for idx_name in (name, cand):
            try:
                got = _ni_chunk(s, cand, idx_name, first_start, end)
            except Exception as e:  # noqa: BLE001
                errs.append(f"{cand}: {e}")
                continue
            if got:
                used, rows = cand, got
                break
        if used:
            break
    if not used:
        raise ValueError("no data under any name tried (" + ", ".join(api_candidates(name, mapping)) + ")"
                         + (f"; {errs[-1]}" if errs else ""))
    cur_end = first_start - timedelta(days=1)
    while cur_end >= start:
        cur_start = max(start, cur_end - timedelta(days=360))
        rows += _ni_chunk(s, used, name, cur_start, cur_end)
        cur_end = cur_start - timedelta(days=1)
        time.sleep(0.3)
    return _rows_to_ohlc(rows), used


def fetch_yahoo(ticker: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(ticker, start=start, end=end + timedelta(days=1), progress=False, auto_adjust=False)
    if df is None or df.empty:
        raise ValueError(f"Yahoo returned nothing for {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    out = df[["Close", "High", "Low"]].rename(columns=str.lower)
    out.index = pd.to_datetime(out.index).tz_localize(None).normalize()
    return out.dropna(subset=["close"])


def fetch_one(name: str, mapping: dict, start: date, end: date) -> tuple[str, pd.DataFrame | None, dict]:
    try:
        df, api = fetch_ni(name, mapping, start, end)
        return name, df, {"source": "niftyindices.com", "api": api, "rows": len(df), "last": df.index.max()}
    except Exception as e1:  # noqa: BLE001
        if name in YAHOO:
            try:
                df = fetch_yahoo(YAHOO[name], start, end)
                return name, df, {"source": f"Yahoo {YAHOO[name]}", "api": "", "rows": len(df),
                                  "last": df.index.max(), "note": f"niftyindices: {e1}"}
            except Exception as e2:  # noqa: BLE001
                return name, None, {"source": "FAILED", "note": f"niftyindices: {e1}; Yahoo: {e2}"}
        return name, None, {"source": "FAILED", "note": f"niftyindices: {e1}"}


def fetch_all(names: list[str], start: date, end: date, mapping: dict) -> tuple[dict, dict]:
    data, status = {}, {}
    with ThreadPoolExecutor(max_workers=SECTOR_CFG["workers"]) as ex:
        for name, df, st_ in ex.map(lambda n: fetch_one(n, mapping, start, end), names):
            status[name] = st_
            if df is not None and len(df):
                data[name] = df[df.index <= pd.Timestamp(end)]
    return data, status


# ---------------------------------------------------------------- constituents
_CSV = re.compile(r"IndexConstituent/([A-Za-z0-9_\-.%]+\.csv)", re.I)


def constituent_file(page: str, get=None) -> str:
    """Finds the constituent CSV linked from the index's own page on niftyindices.com."""
    get = get or (lambda url: requests.get(url, headers={"User-Agent": UA}, timeout=25))
    html = get(NI_BASE + page).text
    m = _CSV.search(html)
    if not m:
        raise ValueError("no constituent list linked on the index page")
    return m.group(1)


def download_list(fname: str, get=None) -> pd.DataFrame:
    get = get or (lambda url: requests.get(url, headers={"User-Agent": UA, "Accept": "text/csv,*/*"}, timeout=25))
    errors = []
    for m in CONSTITUENT_MIRRORS:
        url = m.format(f=fname)
        try:
            r = get(url)
            r.raise_for_status()
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [str(c).strip() for c in df.columns]
            if "Symbol" not in df.columns or df.empty:
                raise ValueError("no 'Symbol' column")
            if "Series" in df.columns:
                df = df[df["Series"].astype(str).str.strip().isin(["EQ", "BE", "BZ"])]
            for c in ("Company Name", "Industry"):
                if c not in df.columns:
                    df[c] = ""
            df["Symbol"] = df["Symbol"].astype(str).str.strip().str.upper()
            return df.drop_duplicates("Symbol")[["Symbol", "Company Name", "Industry"]].reset_index(drop=True)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{url}: {e}")
    raise RuntimeError("; ".join(errors))


# ---------------------------------------------------------------- calculations (the Google Sheet logic)
def _base_close(df: pd.DataFrame, asof: pd.Timestamp, days: int) -> float:
    """Sheet logic: first close ON or AFTER (as-of date - N calendar days)."""
    target = asof - pd.Timedelta(days=days)
    if df.index[0] > target + pd.Timedelta(days=5):      # not enough history
        return np.nan
    sub = df.loc[target:asof, "close"]
    return float(sub.iloc[0]) if len(sub) else np.nan


def sector_snapshot(data: dict, asof: pd.Timestamp, days_3m: int, cfg: dict = SECTOR_CFG) -> pd.DataFrame:
    rows = []
    for name, df in data.items():
        df = df.loc[:asof]
        if df.empty or (asof - df.index[-1]).days > 7:      # no recent close -> skip
            continue
        c = float(df["close"].iloc[-1])
        b = {k: _base_close(df, asof, d) for k, d in cfg["days"].items()}
        b["3m"] = _base_close(df, asof, days_3m)
        yr = df.loc[asof - pd.Timedelta(days=cfg["days"]["1y"]):]
        enough_yr = df.index[0] <= asof - pd.Timedelta(days=cfg["days"]["1y"] - 5)
        hi = pd.concat([yr["high"].where(yr["high"] > 0), yr["close"]]).max() if enough_yr else np.nan
        lo = pd.concat([yr["low"].where(yr["low"] > 0), yr["close"]]).min() if enough_yr else np.nan
        rows.append({
            "name": name, "group": GROUP.get(name, "Theme"), "date": df.index[-1], "close": c,
            "high_52w": hi, "low_52w": lo,
            "r_1w": c / b["1w"] - 1, "r_1m": c / b["1m"] - 1, "r_3m": c / b["3m"] - 1,
            "r_6m_ex_1m": b["1m"] / b["6m"] - 1,           # price 1M ago vs 6M ago (compounded)
            "r_6m": c / b["6m"] - 1, "r_1y": c / b["1y"] - 1,
            "away_low": c / lo - 1, "off_high": c / hi - 1,
        })
    t = pd.DataFrame(rows)
    if t.empty:
        return t
    t = t.set_index("name")
    # Google Sheets RANK(): 1 = highest value, ties share the lower rank number
    t["rk_1m"] = t["r_1m"].rank(ascending=False, method="min")
    t["rk_3m"] = t["r_3m"].rank(ascending=False, method="min")
    t["rk_6m"] = t["r_6m"].rank(ascending=False, method="min")
    t["score"] = cfg["w_1m"] * t["rk_1m"] + cfg["w_3m"] * t["rk_3m"] + cfg["w_6m"] * t["rk_6m"]
    return t


def order_by(t: pd.DataFrame, basis: str) -> pd.DataFrame:
    """Adds 'pos' (1 = best) using the chosen basis. Composite: lowest score first, ties -> better 1M."""
    if basis == "composite":
        o = t.sort_values(["score", "r_1m"], ascending=[True, False], na_position="last")
    else:
        o = t.sort_values([basis, "r_1m"], ascending=[False, False], na_position="last")
    o = o.copy()
    o["pos"] = range(1, len(o) + 1)
    return o


def week_end_dates(data: dict, latest: pd.Timestamp, weeks: int) -> list[pd.Timestamp]:
    """Last trading day of each week (Mon-Fri) for the last `weeks` weeks; the newest is the latest close."""
    ref = data.get("Nifty 50")
    idx = ref.index if ref is not None else pd.DatetimeIndex(sorted(set().union(*[d.index for d in data.values()])))
    idx = idx[idx <= latest]
    ends = pd.Series(idx, index=idx).groupby(idx.to_period("W-FRI")).max()
    return list(ends.iloc[-weeks:])


def rank_history(data: dict, dates: list, days_3m: int, basis: str) -> pd.DataFrame:
    """Position of every index at each weekly snapshot (index x date)."""
    pos = {}
    for d in dates:
        snap = sector_snapshot(data, d, days_3m)
        pos[d] = order_by(snap, basis)["pos"] if not snap.empty else pd.Series(dtype=float)
    return pd.DataFrame(pos)


def rotation_notes(hist: pd.DataFrame, cfg: dict = SECTOR_CFG) -> list[str]:
    """Plain-language summary of what moved, from the weekly positions table (index x date)."""
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
    if "Nifty 50" in cur.index:
        others = cur.drop([b for b in cur.index if GROUP.get(b) == "Benchmark"])
        above = int((others < cur["Nifty 50"]).sum())
        notes.append(f"**{above} of {len(others)}** sectoral/thematic indices rank above Nifty 50"
                     + (" — leadership is broad." if above >= len(others) / 2 else " — leadership is narrow."))
    return notes
