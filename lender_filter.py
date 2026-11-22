# Copied unchanged from the Abu Abdullah RS app (github.com/Nhanna234/RS-on-Nifty, lender_filter.py, d931e8c).
# Keep the two copies the same if the lending rules change.
"""
Identifies lending businesses so they can be left out of the screener and the backtest
(no Streamlit imports, so it can be tested on its own).

A company counts as a lender when NSE Indices' "Basic Industry" (the most detailed level of
NSE's industry classification, describing the company's core business) is one of:
    Public Sector Bank, Private Sector Bank, Other Bank (small finance, payment, foreign banks),
    Non Banking Financial Company (NBFC), Housing Finance Company, Financial Institution.
Insurers, asset managers, brokers, exchanges, depositories, rating agencies, distributors,
fintech, holding and investment companies are not lenders by this definition.

Where it comes from, per stock:
1. NSE's quote page data (industryInfo.basicIndustry), fetched only for stocks whose broad
   sector is Financial Services, and kept on disk for 30 days.
2. If NSE does not answer (it sometimes blocks cloud servers), rules instead:
   "Bank" in the company name; membership of NSE's Nifty NBFC index (all NBFCs in the
   Nifty Total Market); housing-finance or lending words in the name; a short list of
   government financial institutions.
3. The user's own "always exclude" / "never exclude" symbols override both.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

IST = timezone(timedelta(hours=5, minutes=30))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
NSE = "https://www.nseindia.com"
CACHE_DAYS = 30

LENDING_LABELS = ("Public Sector Bank", "Private Sector Bank", "Other Bank",
                  "Non Banking Financial Company (NBFC)", "Housing Finance Company", "Financial Institution")

# Government financial institutions whose names do not say "bank" or "finance"
KNOWN_FIS = {"RECLTD": "Financial Institution", "IREDA": "Financial Institution",
             "HUDCO": "Financial Institution", "PFC": "Financial Institution",
             "IRFC": "Financial Institution", "IFCI": "Financial Institution"}
HFC_WORDS = re.compile(r"housing finance|home finance|homes? ltd|homes limited|financiers", re.I)
LENDER_WORDS = re.compile(r"\bfinance\b|\bfincorp\b|\bcredit\b|microfin|grameen|\bleasing\b", re.I)
NOT_LENDER_WORDS = re.compile(r"insurance|asset management|\bamc\b|holding|investments? (ltd|limited)|exchange|"
                              r"depositor|registrar|broking|securities", re.I)


def is_lending_label(label: str | None) -> bool:
    if not label:
        return False
    s = label.lower()
    return ("bank" in s and "non bank" not in s) or "non banking financial" in s or \
        "housing finance" in s or "financial institution" in s


def is_financial_sector(sector: str | None) -> bool:
    s = (sector or "").lower()
    return "financ" in s or "bank" in s


class BasicIndustryCache:
    """NSE basic industry per symbol, kept on disk; NSE is asked only for stale or unknown symbols."""

    def __init__(self, path: str, fetch_one=None):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        try:
            with open(path) as fh:
                self.data = json.load(fh)
        except Exception:  # noqa: BLE001
            self.data = {}
        self.fetch_one = fetch_one

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self.data, fh)
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    def get(self, symbols: list[str], max_calls: int = 400) -> tuple[dict, str]:
        """Returns ({symbol: basic industry}, status) where status is 'ok', 'partial' or 'blocked'."""
        with self.lock:
            now = time.time()
            fresh = {s: v["label"] for s, v in self.data.items()
                     if s in symbols and now - v.get("t", 0) < CACHE_DAYS * 86400 and v.get("label")}
            todo = [s for s in symbols if s not in fresh][:max_calls]
            if not todo:
                return fresh, "ok"
            fetch = self.fetch_one or NseQuote().basic_industry
            got, failures = 0, 0
            for s in todo:
                try:
                    label = fetch(s)
                except Exception:  # noqa: BLE001
                    label = None
                if label:
                    fresh[s] = label
                    self.data[s] = {"label": label, "t": now}
                    got += 1
                else:
                    failures += 1
                    if got == 0 and failures >= 3:       # NSE is not answering: stop asking
                        break
            self._save()
            missing = [s for s in symbols if s not in fresh]
            status = "ok" if not missing else ("blocked" if len(missing) == len(symbols) else "partial")
            return fresh, status


class NseQuote:
    """Reads industryInfo.basicIndustry from NSE's quote data for one symbol."""

    def __init__(self, pause: float = 0.35):
        self.s = None
        self.pause = pause

    def _session(self):
        if self.s is None:
            self.s = requests.Session()
            self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                                   "Accept": "application/json, text/plain, */*", "Referer": f"{NSE}/"})
            try:
                self.s.get(NSE, timeout=10)                 # sets the cookies NSE's data pages need
            except requests.RequestException:
                pass
        return self.s

    def basic_industry(self, symbol: str) -> str | None:
        s = self._session()
        time.sleep(self.pause)
        r = s.get(f"{NSE}/api/quote-equity", params={"symbol": symbol}, timeout=10)
        if r.status_code != 200:
            return None
        info = (r.json() or {}).get("industryInfo") or {}
        return info.get("basicIndustry") or None


def rule_label(symbol: str, name: str, nbfc_index: set[str]) -> tuple[str | None, str]:
    """Fallback when NSE's label is not available. Returns (lending label or None, reason)."""
    n = name or ""
    if symbol in KNOWN_FIS:
        return KNOWN_FIS[symbol], "known government financial institution"
    if symbol in nbfc_index:
        return "Non Banking Financial Company (NBFC)", "in NSE's Nifty NBFC index"
    if re.search(r"\bbank\b", n, re.I):
        return "Bank", "'Bank' in the company name"
    if HFC_WORDS.search(n):
        return "Housing Finance Company", "housing-finance words in the name"
    if LENDER_WORDS.search(n) and not NOT_LENDER_WORDS.search(n):
        return "Non Banking Financial Company (NBFC)", "lending words in the name"
    return None, "no lending signal in the name"


def parse_symbols(text) -> set[str]:
    if not text:
        return set()
    if isinstance(text, (list, tuple, set)):
        items = text
    else:
        items = re.split(r"[\s,;]+", str(text))
    out = set()
    for x in items:
        x = str(x).strip().upper()
        if ":" in x:                       # NSE:TCS, NASDAQ:AAPL
            x = x.split(":", 1)[1]
        if x.endswith(".NS"):
            x = x[:-3]
        x = x.replace(".", "-")            # US class shares in Yahoo form: BRK.B -> BRK-B
        if x:
            out.add(x)
    return out


def classify(symbols: list[str], names: dict, sectors: dict, cache: BasicIndustryCache,
             nbfc_index: set[str], always=None, never=None) -> dict:
    """Decides which symbols are lenders. Returns the excluded and kept financial companies with reasons."""
    always, never = parse_symbols(always), parse_symbols(never)
    fin = [s for s in symbols if is_financial_sector(sectors.get(s)) or s in always]
    labels, status = cache.get(fin) if fin else ({}, "ok")

    excluded, kept = [], []
    by_rule = 0
    for s in sorted(set(symbols)):
        if s in never:
            if s in fin:
                kept.append({"symbol": s, "name": names.get(s, ""), "label": labels.get(s, "Financial Services"),
                             "source": "your never-exclude list"})
            continue
        if s in always:
            excluded.append({"symbol": s, "name": names.get(s, ""), "label": labels.get(s, "Your choice"),
                             "source": "your always-exclude list"})
            continue
        if s not in fin:
            continue
        if s in labels:
            row = {"symbol": s, "name": names.get(s, ""), "label": labels[s], "source": "NSE basic industry"}
            (excluded if is_lending_label(labels[s]) else kept).append(row)
        else:
            lab, why = rule_label(s, names.get(s, ""), nbfc_index)
            row = {"symbol": s, "name": names.get(s, ""), "label": lab or "Financial Services",
                   "source": f"rule: {why}"}
            if lab:
                by_rule += 1
                excluded.append(row)
            else:
                kept.append(row)
    return {"excluded": excluded, "kept": kept, "nseStatus": status, "byRule": by_rule,
            "checked": len(fin), "labels": list(LENDING_LABELS)}


# ════════════════════════════════════════════════════════════ US universes
# For US stocks the industry in the stock list decides (ICB subsector from the Nasdaq-100 table, or
# whatever industry column an uploaded list has). The Nasdaq-100 rules already leave out financial
# companies, so for that index this normally removes nothing.
US_LENDING_WORDS = re.compile(r"\bbanks?\b|banking|consumer lending|consumer finance|mortgage|thrift|"
                              r"savings institution|specialty finance|commercial finance|credit services", re.I)
US_LENDING_DEFINITION = ("Lending means the stock list's industry is a bank, a consumer or commercial lender, a "
                         "mortgage lender or mortgage REIT, or a savings institution. Insurers, asset managers, "
                         "exchanges and payment companies stay in. The Nasdaq-100 itself has no financial companies.")


def is_us_lender(industry: str | None) -> bool:
    return bool(industry) and bool(US_LENDING_WORDS.search(str(industry)))


def classify_us(symbols: list[str], names: dict, industries: dict, always=None, never=None) -> dict:
    """Same output as classify(), for US stocks: industries = {symbol: 'Industry / Subsector' text}."""
    always, never = parse_symbols(always), parse_symbols(never)
    excluded, kept = [], []
    for s in sorted(set(symbols)):
        label = industries.get(s, "") or ""
        row = {"symbol": s, "name": names.get(s, ""), "label": label or "Not classified"}
        if s in never:
            if is_us_lender(label):
                kept.append({**row, "source": "your never-exclude list"})
        elif s in always:
            excluded.append({**row, "source": "your always-exclude list"})
        elif is_us_lender(label):
            excluded.append({**row, "source": "industry in the stock list"})
    return {"excluded": excluded, "kept": kept, "nseStatus": "ok", "byRule": 0, "checked": len(set(symbols)),
            "labels": [], "market": "US", "definition": US_LENDING_DEFINITION}
