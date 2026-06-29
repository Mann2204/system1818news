"""
System 1818 News — Standalone Market Intelligence App
100% independent. No other System 1818 files needed.
Deploy the folder containing this file directly to Streamlit Cloud.

Folder structure required:
  system1818news/
  ├── app.py           ← this file
  ├── requirements.txt
  └── .streamlit/
      └── secrets.toml  (local only — never commit)
"""

import os, json, time, hashlib, logging
import requests, feedparser, anthropic
import pandas as pd
import streamlit as st
from datetime import datetime, date
from typing import Optional
from bs4 import BeautifulSoup

# ── PAGE CONFIG — must be the very first Streamlit call ──────────
st.set_page_config(
    page_title="System 1818 News",
    page_icon="📰",
    layout="wide",
    initial_sidebar_state="expanded",
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────
# SECRETS
# ─────────────────────────────────────────────────────────────────

def get_api_key() -> str:
    try:
        return st.secrets["ANTHROPIC_API_KEY"]
    except Exception:
        return os.getenv("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────────────────────────
# CACHE  (uses /tmp — works on Streamlit Cloud)
# ─────────────────────────────────────────────────────────────────

CACHE_DIR = "/tmp/s1818news"
os.makedirs(CACHE_DIR, exist_ok=True)

def _ck(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()

def _rcache(key: str, ttl_min: int = 30) -> Optional[dict]:
    path = f"{CACHE_DIR}/{key}.json"
    if not os.path.exists(path):
        return None
    if time.time() - os.path.getmtime(path) > ttl_min * 60:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def _wcache(key: str, data):
    try:
        with open(f"{CACHE_DIR}/{key}.json", "w") as f:
            json.dump(data, f, default=str)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────
# CLAUDE API HELPER
# ─────────────────────────────────────────────────────────────────

def _claude(system: str, user: str, max_tokens: int = 800, ttl: int = 60) -> str:
    ck = _ck(system[:80] + user[:200])
    cached = _rcache(ck, ttl_min=ttl)
    if cached:
        return cached.get("t", "")
    key = get_api_key()
    if not key:
        return '{"error":"No API key configured"}'
    try:
        client = anthropic.Anthropic(api_key=key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = msg.content[0].text
        _wcache(ck, {"t": text})
        return text
    except Exception as e:
        return f'{{"error":"{str(e)}"}}'

def _json(raw: str) -> dict:
    try:
        clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except Exception:
        return {}

# ─────────────────────────────────────────────────────────────────
# F&O STOCK UNIVERSE
# symbol → (bse_code, company_name, nifty_weight%, banknifty_weight%)
# ─────────────────────────────────────────────────────────────────

FNO = {
    "HDFCBANK":   ("532275", "HDFC Bank",               11.2, 28.5),
    "RELIANCE":   ("500325", "Reliance Industries",       9.8,  0.0),
    "ICICIBANK":  ("532174", "ICICI Bank",                7.1, 22.3),
    "TCS":        ("532540", "Tata Consultancy Services", 4.8,  0.0),
    "INFY":       ("500209", "Infosys",                   4.2,  0.0),
    "KOTAKBANK":  ("500247", "Kotak Mahindra Bank",       3.9, 13.2),
    "BHARTIARTL": ("532454", "Bharti Airtel",             3.5,  0.0),
    "LT":         ("500510", "Larsen & Toubro",           3.6,  0.0),
    "AXISBANK":   ("532215", "Axis Bank",                 3.3, 12.5),
    "ITC":        ("500875", "ITC",                       3.1,  0.0),
    "SBIN":       ("500112", "State Bank of India",       3.1, 10.8),
    "HCLTECH":    ("532281", "HCL Technologies",          2.8,  0.0),
    "BAJFINANCE": ("500034", "Bajaj Finance",             2.9,  6.4),
    "HINDUNILVR": ("500696", "Hindustan Unilever",        2.2,  0.0),
    "MARUTI":     ("532500", "Maruti Suzuki",             2.1,  0.0),
    "TATAMOTORS": ("500570", "Tata Motors",               1.4,  0.0),
    "WIPRO":      ("507685", "Wipro",                     1.8,  0.0),
    "ADANIENT":   ("512599", "Adani Enterprises",         1.8,  0.0),
    "SUNPHARMA":  ("524715", "Sun Pharmaceuticals",       1.6,  0.0),
    "ONGC":       ("500312", "ONGC",                      1.5,  0.0),
    "NTPC":       ("532555", "NTPC",                      1.4,  0.0),
    "TATASTEEL":  ("500470", "Tata Steel",                1.2,  0.0),
    "POWERGRID":  ("532898", "Power Grid",                1.0,  0.0),
    "M&M":        ("500520", "Mahindra & Mahindra",       1.9,  0.0),
}

# ─────────────────────────────────────────────────────────────────
# NEWS FEEDS
# ─────────────────────────────────────────────────────────────────

NEWS_FEEDS = [
    ("ET Markets",        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("ET Economy",        "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms"),
    ("Moneycontrol",      "https://www.moneycontrol.com/rss/marketsindia.xml"),
    ("LiveMint Markets",  "https://www.livemint.com/rss/markets"),
    ("Reuters India",     "https://feeds.reuters.com/reuters/INbusinessNews"),
    ("NDTV Profit",       "https://feeds.feedburner.com/ndtvprofit-latest"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
]

# ─────────────────────────────────────────────────────────────────
# 20-YEAR NIFTY PLAYBOOK
# ─────────────────────────────────────────────────────────────────

PLAYBOOK = {
    "election_surprise": {
        "label": "Election surprise", "icon": "🗳️",
        "direction": "CRASH", "magnitude": "HIGH",
        "duration_days": 5, "bn_mult": 1.2,
        "range": "-8% to -12%",
        "examples": "May 2004 UPA shock (−12.2%), Jun 2024 BJP below majority (−5.9%)",
        "keys": ["exit poll", "election result", "hung parliament", "lok sabha",
                 "coalition", "unexpected win", "majority"],
    },
    "election_mandate": {
        "label": "Election mandate (clear majority)", "icon": "🏆",
        "direction": "RALLY", "magnitude": "HIGH",
        "duration_days": 14, "bn_mult": 1.5,
        "range": "+8% to +18%",
        "examples": "May 2009 UPA mandate (+17.7%), May 2014 NDA landslide (+8%)",
        "keys": ["clear majority", "stable government", "policy continuity",
                 "NDA win", "BJP majority", "single-party majority"],
    },
    "global_credit_crisis": {
        "label": "Global credit / banking crisis", "icon": "🏦",
        "direction": "CRASH", "magnitude": "EXTREME",
        "duration_days": 180, "bn_mult": 2.5,
        "range": "-50% to -65% total drawdown",
        "examples": "Lehman 2008 (−65% peak-to-trough), SVB 2023 (minor contagion)",
        "keys": ["bank collapse", "Lehman", "SVB", "credit freeze", "systemic risk",
                 "bank run", "financial crisis", "sub-prime"],
    },
    "pandemic_shock": {
        "label": "Pandemic / health emergency", "icon": "🦠",
        "direction": "CRASH", "magnitude": "EXTREME",
        "duration_days": 30, "bn_mult": 2.0,
        "range": "-10% to -13% single day",
        "examples": "COVID Mar 2020 (−12.98% single day, −38% in 30 days)",
        "keys": ["WHO pandemic", "lockdown", "quarantine", "new virus",
                 "epidemic", "health emergency", "travel ban", "outbreak"],
    },
    "domestic_policy_shock": {
        "label": "Domestic policy shock", "icon": "⚡",
        "direction": "CRASH", "magnitude": "HIGH",
        "duration_days": 60, "bn_mult": 1.0,
        "range": "-5% to -7%",
        "examples": "Demonetisation Nov 2016 (−6.3%), LTCG tax Feb 2018 (−5%)",
        "keys": ["demonetisation", "LTCG", "STT hike", "windfall tax",
                 "sudden policy", "export ban", "FII cap", "GST rate hike"],
    },
    "china_contagion": {
        "label": "China / EM contagion", "icon": "🇨🇳",
        "direction": "CRASH", "magnitude": "MEDIUM",
        "duration_days": 14, "bn_mult": 1.1,
        "range": "-4% to -7%",
        "examples": "China Black Monday Aug 2015 (−5.9%), Jan 2016 circuit breaker",
        "keys": ["yuan devaluation", "PBOC", "China GDP miss", "Shanghai crash",
                 "EM selloff", "China slowdown", "circuit breaker China"],
    },
    "fed_hawkish": {
        "label": "US Fed hawkishness", "icon": "🦅",
        "direction": "CRASH", "magnitude": "MEDIUM",
        "duration_days": 30, "bn_mult": 1.2,
        "range": "-3% to -7%",
        "examples": "2013 taper tantrum (rupee to 68), 2022 rate hike cycle (Nifty −18%)",
        "keys": ["Fed rate hike", "taper tantrum", "hawkish FOMC", "US 10Y yield",
                 "DXY spike", "Powell hawkish", "higher for longer"],
    },
    "oil_shock": {
        "label": "Oil price shock", "icon": "🛢️",
        "direction": "CRASH", "magnitude": "MEDIUM",
        "duration_days": 21, "bn_mult": 1.0,
        "range": "-2% to -5%",
        "examples": "2022 Russia-Ukraine (crude to $130), 2025 Hormuz closure",
        "keys": ["crude above 100", "OPEC cut", "Strait of Hormuz", "oil supply",
                 "brent spike", "rupee depreciation oil", "CAD widening"],
    },
    "domestic_banking_crisis": {
        "label": "Domestic banking / NBFC crisis", "icon": "🚨",
        "direction": "CRASH", "magnitude": "HIGH",
        "duration_days": 30, "bn_mult": 3.0,
        "range": "-5% to -15% (BankNifty worst hit)",
        "examples": "IL&FS 2018 (NBFC freeze), Yes Bank 2020 (RBI bailout)",
        "keys": ["NBFC default", "IL&FS", "Yes Bank", "DHFL", "NPA surge",
                 "bank fraud", "RBI intervention", "promoter pledge", "short report"],
    },
    "rbi_rate_cut": {
        "label": "RBI rate cut / dovish pivot", "icon": "✂️",
        "direction": "RALLY", "magnitude": "MEDIUM",
        "duration_days": 14, "bn_mult": 1.8,
        "range": "+2% to +5%",
        "examples": "COVID emergency cuts Apr 2020, Rajan cuts 2015–16, 2024 cut",
        "keys": ["repo rate cut", "RBI cut", "CRR cut", "dovish MPC",
                 "RBI accommodative", "rate cycle reversal", "OMO purchase"],
    },
    "fed_dovish": {
        "label": "US Fed rate cut / dovish pivot", "icon": "🕊️",
        "direction": "RALLY", "magnitude": "MEDIUM",
        "duration_days": 14, "bn_mult": 1.5,
        "range": "+2% to +5%",
        "examples": "Fed cut Sep 2024 (Nifty hit 59 ATHs in 2024), QE 2020",
        "keys": ["Fed rate cut", "dovish Fed", "QE announced", "Fed pause",
                 "DXY fall", "FII buying", "EM inflows"],
    },
    "growth_budget": {
        "label": "Growth-oriented Union Budget", "icon": "📋",
        "direction": "RALLY", "magnitude": "LOW",
        "duration_days": 3, "bn_mult": 1.0,
        "range": "+1% to +4%",
        "examples": "Capex budget 2021, Budget 2014 post-election, Budget 2024",
        "keys": ["union budget", "capex allocation", "infra spending",
                 "tax relief", "PLI scheme", "fiscal consolidation", "disinvestment"],
    },
    "geopolitical": {
        "label": "Geopolitical shock", "icon": "⚔️",
        "direction": "MIXED", "magnitude": "LOW",
        "duration_days": 3, "bn_mult": 1.0,
        "range": "-1% to -3% (recovers in 1-3 days)",
        "examples": "Pulwama 2019, Russia-Ukraine 2022, Indo-Pak 2025",
        "keys": ["India Pakistan", "border tension", "airstrike", "ceasefire",
                 "war declaration", "military escalation", "sanctions"],
    },
    "heavyweight_earnings": {
        "label": "Heavyweight stock quarterly results", "icon": "📊",
        "direction": "MIXED", "magnitude": "LOW",
        "duration_days": 2, "bn_mult": 2.0,
        "range": "-3% to +3% (stock); -1.5% to +1.5% (Nifty)",
        "examples": "HDFC Bank Q3 2024 miss (Nifty −3%), Reliance beat 2023",
        "keys": ["quarterly results", "earnings miss", "earnings beat", "NIM",
                 "PAT miss", "revenue miss", "management guidance", "profit"],
    },
    "post_crash_recovery": {
        "label": "Post-crash V-recovery / bull run", "icon": "🚀",
        "direction": "RALLY", "magnitude": "EXTREME",
        "duration_days": 365, "bn_mult": 2.0,
        "range": "+50% to +87% from bottom (over months)",
        "examples": "2003 (+77%), 2009 (+76%), 2020 (+86.7% by year-end)",
        "keys": ["GDP recovery", "earnings upgrade", "cheap valuations",
                 "FII return", "risk-on", "economic revival", "bargain buying"],
    },
    "uncategorised": {
        "label": "Uncategorised / watch", "icon": "👀",
        "direction": "NEUTRAL", "magnitude": "LOW",
        "duration_days": 1, "bn_mult": 1.0,
        "range": "Unclear",
        "examples": "Does not match any high-confidence historical pattern",
        "keys": [],
    },
}

# ─────────────────────────────────────────────────────────────────
# MODULE 1 — NEWS FETCHER
# ─────────────────────────────────────────────────────────────────

def fetch_news(max_per_feed: int = 15) -> list[dict]:
    items = []
    for src, url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url, request_headers={"User-Agent": "Mozilla/5.0"})
            for entry in feed.entries[:max_per_feed]:
                raw_summary = entry.get("summary", "")
                clean = BeautifulSoup(raw_summary, "html.parser").get_text()[:400]
                items.append({
                    "title":     entry.get("title", "").strip(),
                    "source":    src,
                    "url":       entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "summary":   clean,
                })
        except Exception as e:
            log.warning(f"Feed error {src}: {e}")

    seen, unique = set(), []
    for item in items:
        h = _ck(item["title"][:60])
        if h not in seen and len(item["title"]) > 10:
            seen.add(h)
            unique.append(item)
    return unique

# ─────────────────────────────────────────────────────────────────
# MODULE 2 — NEWS CLASSIFIER (Claude)
# ─────────────────────────────────────────────────────────────────

_CLASSIFY_SYS = """
You are a strict financial news classifier for Indian equity markets (Nifty 50, BankNifty, NSE F&O).

Read the headline and summary. Return ONLY valid JSON — no markdown, no preamble, nothing else.

Category keys (use exactly one):
election_surprise, election_mandate, global_credit_crisis, pandemic_shock,
domestic_policy_shock, china_contagion, fed_hawkish, oil_shock,
domestic_banking_crisis, rbi_rate_cut, fed_dovish, growth_budget,
geopolitical, heavyweight_earnings, post_crash_recovery, uncategorised

Required JSON structure:
{
  "category": "<key>",
  "confidence": <0-100>,
  "direction": "<RALLY|CRASH|MIXED|NEUTRAL>",
  "magnitude": "<LOW|MEDIUM|HIGH|EXTREME>",
  "timing": "<IMMEDIATE|TOMORROW|3DAYS|WEEK>",
  "nifty_impact": "<e.g. -1% to -3%>",
  "bn_multiplier": <1.0-3.0>,
  "sectors": ["sector1", "sector2"],
  "reasoning": "<one sentence, max 20 words>",
  "is_material": <true|false>,
  "related_stock": "<NSE symbol if news is about one specific F&O company, else null>"
}

Rules:
- confidence < 40  →  category = "uncategorised", is_material = false
- is_material = true only if this could move Nifty by more than 0.5% today or tomorrow
- MIXED = cuts both ways (e.g. geopolitical: brief dip then defence stocks rally)
- bn_multiplier: 1.0 = same as Nifty, 2.0 = BankNifty moves 2× Nifty, 3.0 = extreme banking event
"""

def classify_one(item: dict) -> dict:
    user = (f"Headline: {item['title']}\n"
            f"Source: {item['source']}\n"
            f"Summary: {item['summary'][:300]}")
    raw = _claude(_CLASSIFY_SYS, user, max_tokens=350, ttl=120)
    result = _json(raw)
    if not result or "category" not in result:
        return {"category": "uncategorised", "confidence": 0,
                "is_material": False, "direction": "NEUTRAL", "magnitude": "LOW"}
    return result

# ─────────────────────────────────────────────────────────────────
# MODULE 3 — EARNINGS CALENDAR (BSE API)
# ─────────────────────────────────────────────────────────────────

def _bse_result_date(bse_code: str) -> Optional[date]:
    ck = f"bse_{bse_code}"
    cached = _rcache(ck, ttl_min=480)
    if cached is not None:
        val = cached.get("d")
        return date.fromisoformat(val) if val else None

    try:
        url = (f"https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"
               f"?Type=BoardMeeting&scripcode={bse_code}")
        hdrs = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"}
        data = requests.get(url, headers=hdrs, timeout=12).json()
        today = date.today()
        for m in data.get("Table", []):
            p = str(m.get("Purpose", "")).upper()
            if any(k in p for k in ("RESULT", "FINANCIAL", "QUARTERLY")):
                raw = m.get("Meeting_Date", "")
                for fmt in ("%d %b %Y", "%Y-%m-%d", "%d/%m/%Y"):
                    try:
                        d = datetime.strptime(raw[:11].strip(), fmt).date()
                        if d >= today:
                            _wcache(ck, {"d": d.isoformat()})
                            return d
                    except ValueError:
                        continue
    except Exception as e:
        log.debug(f"BSE date fetch failed {bse_code}: {e}")

    _wcache(ck, {"d": None})
    return None

def earnings_calendar(days_ahead: int = 7) -> list[dict]:
    today = date.today()
    out = []
    for sym, (bse, company, nw, bw) in FNO.items():
        d = _bse_result_date(bse)
        if d is None:
            continue
        away = (d - today).days
        if 0 <= away <= days_ahead:
            out.append({"symbol": sym, "company": company,
                        "result_date": d, "days_away": away,
                        "nifty_wt": nw, "bn_wt": bw})
    return sorted(out, key=lambda x: x["days_away"])

# ─────────────────────────────────────────────────────────────────
# MODULE 4 — QUARTERLY FINANCIALS (Screener.in)
# ─────────────────────────────────────────────────────────────────

def _screener_data(sym: str) -> Optional[dict]:
    ck = f"scr_{sym}"
    cached = _rcache(ck, ttl_min=720)
    if cached:
        return cached

    # Try JSON API first
    try:
        url = f"https://www.screener.in/api/company/{sym}/quarters/?format=json"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        if r.status_code == 200:
            data = r.json()
            _wcache(ck, data)
            return data
    except Exception:
        pass

    # Fallback: HTML scrape
    try:
        url = f"https://www.screener.in/company/{sym}/consolidated/"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        table = soup.select_one("#quarters table")
        if table:
            hdrs = [th.get_text(strip=True) for th in table.select("thead th")]
            rows = []
            for tr in table.select("tbody tr"):
                cells = [td.get_text(strip=True) for td in tr.select("td")]
                if cells:
                    rows.append(dict(zip(hdrs, cells)))
            data = {"quarters": rows, "src": "html"}
            _wcache(ck, data)
            return data
    except Exception:
        pass

    return None

def _price_reactions(sym: str) -> list[float]:
    try:
        import yfinance as yf
        hist = yf.Ticker(f"{sym}.NS").history(period="2y")
        if hist.empty:
            return []
        pct = hist["Close"].pct_change().dropna() * 100
        top = pct.abs().nlargest(8)
        return [round(pct[i], 2) for i in top.index[:4]]
    except Exception:
        return []

_EARNINGS_SYS = """
You are a senior equity analyst covering NSE-listed Indian companies.
Given quarterly financial data from Screener.in and historical result-day price reactions,
perform a rigorous YoY and QoQ analysis.

Return ONLY valid JSON — no markdown, no preamble:
{
  "quarter": "<e.g. Q4FY26>",
  "yoy_revenue_growth": "<e.g. +12.3% or Not available>",
  "yoy_pat_growth": "<e.g. +8.5% or Not available>",
  "qoq_pat_growth": "<e.g. -2.1% or Not available>",
  "expectation": "<LIKELY BEAT|LIKELY MISS|IN LINE|UNCERTAIN>",
  "positives": ["point 1", "point 2", "point 3"],
  "risks": ["risk 1", "risk 2", "risk 3"],
  "watch_metrics": ["metric 1", "metric 2", "metric 3"],
  "avg_result_day_move": <float e.g. -2.3>,
  "bull_stock": "<e.g. +3% to +6%>",
  "bear_stock": "<e.g. -5% to -8%>",
  "nifty_bull": "<e.g. +0.4% to +0.9%>",
  "nifty_bear": "<e.g. -0.8% to -1.5%>",
  "banknifty": "<impact for banking stocks only, else 'N/A'>",
  "confidence": <0-100>,
  "summary": "<max 25 words>"
}

Rules:
- Banking stocks: focus on NIM, GNPA, NNPA, loan growth, deposit growth, credit cost
- IT stocks: focus on revenue guidance, deal TCV, attrition, EBIT margins, constant currency growth
- Never hallucinate numbers — say 'Not available' if data is missing
- UNCERTAIN is better than false confidence
"""

def analyse_earnings(sym: str, company: str) -> Optional[dict]:
    qdata = _screener_data(sym)
    reactions = _price_reactions(sym)
    avg = round(sum(reactions) / len(reactions), 2) if reactions else 0.0
    quarter = _quarter()

    if not qdata:
        return {
            "summary": f"No quarterly data found for {company}",
            "expectation": "UNCERTAIN",
            "confidence": 0,
            "avg_result_day_move": avg,
            "reactions": reactions,
            "quarter": quarter,
        }

    user = (f"Company: {company} (NSE: {sym})\n"
            f"Quarter being analysed: {quarter}\n"
            f"Historical result-day reactions last 4 quarters (%): {reactions}\n"
            f"Average historical move on result day: {avg}%\n\n"
            f"Quarterly financial data:\n"
            f"{json.dumps(qdata, indent=2)[:3500]}\n\n"
            f"Analyse YoY and QoQ trends. Identify the 3 most critical metrics. "
            f"Give bull and bear case stock price reactions and Nifty impact.")

    raw = _claude(_EARNINGS_SYS, user, max_tokens=700, ttl=360)
    result = _json(raw)
    if result:
        result["reactions"] = reactions
        result["avg_result_day_move"] = avg
    return result or None

def _quarter() -> str:
    today = date.today()
    m, y = today.month, today.year
    fy = y if m >= 4 else y - 1
    q = ("Q1" if m in (4,5,6) else "Q2" if m in (7,8,9) else
         "Q3" if m in (10,11,12) else "Q4")
    return f"{q}FY{str(fy+1)[2:]}"

# ─────────────────────────────────────────────────────────────────
# MODULE 5 — SIGNAL AGGREGATOR
# ─────────────────────────────────────────────────────────────────

def overall_view(signals: list[dict]) -> dict:
    W = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "EXTREME": 4}
    crash  = [s for s in signals if s.get("direction") == "CRASH"]
    rally  = [s for s in signals if s.get("direction") == "RALLY"]
    cs = sum(W.get(s.get("magnitude","LOW"),1) * s.get("confidence",0)/100 for s in crash)
    rs = sum(W.get(s.get("magnitude","LOW"),1) * s.get("confidence",0)/100 for s in rally)
    net = rs - cs

    if   net >  3:   lbl, col = "STRONGLY BULLISH",  "success"
    elif net >  1:   lbl, col = "BULLISH",            "success"
    elif net >  0.3: lbl, col = "SLIGHTLY BULLISH",   "success"
    elif net < -3:   lbl, col = "STRONGLY BEARISH",   "error"
    elif net < -1:   lbl, col = "BEARISH",            "error"
    elif net < -0.3: lbl, col = "SLIGHTLY BEARISH",   "error"
    else:            lbl, col = "NEUTRAL",             "warning"

    return {"label": lbl, "color": col, "net": round(net,2),
            "crash": len(crash), "rally": len(rally)}

# ─────────────────────────────────────────────────────────────────
# UI HELPERS
# ─────────────────────────────────────────────────────────────────

def _badge(direction: str) -> str:
    C = {
        "CRASH":   ("#fcebeb","#a32d2d"),
        "RALLY":   ("#eaf3de","#3b6d11"),
        "MIXED":   ("#faeeda","#854f0b"),
        "NEUTRAL": ("#f1efe8","#5f5e5a"),
    }
    bg, fg = C.get(direction, C["NEUTRAL"])
    return (f'<span style="background:{bg};color:{fg};padding:2px 10px;'
            f'border-radius:20px;font-size:12px;font-weight:500">{direction}</span>')

def _mbadge(mag: str) -> str:
    C = {
        "EXTREME": ("#a32d2d","#fff"),
        "HIGH":    ("#fcebeb","#a32d2d"),
        "MEDIUM":  ("#faeeda","#854f0b"),
        "LOW":     ("#eaf3de","#3b6d11"),
    }
    bg, fg = C.get(mag, ("#f1efe8","#5f5e5a"))
    return (f'<span style="background:{bg};color:{fg};padding:2px 8px;'
            f'border-radius:20px;font-size:11px">{mag}</span>')

# ─────────────────────────────────────────────────────────────────
# MAIN APP
# ─────────────────────────────────────────────────────────────────

def main():

    # ── SIDEBAR ─────────────────────────────────────────────────
    with st.sidebar:
        st.markdown("## 📰 System 1818 News")
        st.markdown("*NSE Market Intelligence*")
        st.divider()

        st.markdown("### Scan settings")
        news_count   = st.slider("Headlines to scan",       20, 120, 60, step=10)
        earn_days    = st.slider("Earnings look-ahead (days)", 1, 14, 5)
        min_conf     = st.slider("Min signal confidence",   30,  80, 50)
        st.divider()

        st.markdown("### Quick earnings analysis")
        co_map = {v[1]: k for k, v in FNO.items()}
        sel_co = st.selectbox("Pick a stock", sorted(co_map.keys()))
        analyse_btn = st.button("Analyse this stock ↗", use_container_width=True)
        st.divider()

        run_btn = st.button("Run full scan ↗", type="primary", use_container_width=True)

        if not get_api_key():
            st.error("ANTHROPIC_API_KEY not found.\nAdd it in Streamlit → Settings → Secrets.")

    # ── TABS ─────────────────────────────────────────────────────
    t_overview, t_news, t_earnings, t_playbook = st.tabs([
        "Market overview", "Live news signals", "Earnings calendar", "Playbook"
    ])

    # ════════════════════════════════════════════════════════════
    # TAB: PLAYBOOK  (always visible — no API needed)
    # ════════════════════════════════════════════════════════════
    with t_playbook:
        st.markdown("### 20-year Nifty news-category playbook")
        st.caption("Historical patterns 2004–2024. Categories are based on real market events.")

        fdir = st.radio("Filter by direction", ["All","CRASH","RALLY","MIXED"], horizontal=True)

        for key, cat in PLAYBOOK.items():
            if key == "uncategorised":
                continue
            if fdir != "All" and cat["direction"] != fdir:
                continue
            with st.expander(f"{cat['icon']}  {cat['label']}  —  {cat['direction']}  ·  {cat['magnitude']}"):
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**Direction:** {cat['direction']}")
                c2.markdown(f"**Magnitude:** {cat['magnitude']}")
                c3.markdown(f"**Typical duration:** {cat['duration_days']} days")
                c4, c5 = st.columns(2)
                c4.markdown(f"**Historical range:** `{cat['range']}`")
                c5.markdown(f"**BankNifty multiplier:** {cat['bn_mult']}×")
                st.markdown(f"**Historical examples:** {cat['examples']}")
                st.markdown(f"**Trigger keywords:** `{'` · `'.join(cat['keys'][:6])}`")

    # ════════════════════════════════════════════════════════════
    # TAB: EARNINGS CALENDAR
    # ════════════════════════════════════════════════════════════
    with t_earnings:
        st.markdown("### Earnings calendar and deep analysis")

        # Quick analysis from sidebar
        if analyse_btn:
            sym = co_map[sel_co]
            _, company, nw, bw = FNO[sym]
            st.markdown(f"#### {company}  ({sym})  —  {_quarter()} analysis")
            with st.spinner(f"Fetching financials for {company}…"):
                an = analyse_earnings(sym, company)

            if an:
                exp  = an.get("expectation", "UNCERTAIN")
                conf = an.get("confidence", 0)
                ecol = "success" if "BEAT" in exp else "error" if "MISS" in exp else "warning"

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Nifty weight",        f"{nw:.1f}%")
                m2.metric("BankNifty weight",    f"{bw:.1f}%" if bw > 0 else "N/A")
                m3.metric("Avg result-day move", f"{an.get('avg_result_day_move',0):+.1f}%")
                m4.metric("Analysis confidence", f"{conf}%")

                getattr(st, ecol)(f"**Expectation: {exp}**")
                st.caption(an.get("summary", ""))

                a1, a2 = st.columns(2)
                a1.metric("Revenue growth YoY", an.get("yoy_revenue_growth", "N/A"))
                a2.metric("PAT growth YoY",     an.get("yoy_pat_growth",     "N/A"))

                b1, b2 = st.columns(2)
                with b1:
                    st.markdown("**Bull case**")
                    st.success(f"Stock: {an.get('bull_stock','—')}\nNifty: {an.get('nifty_bull','—')}")
                    for pt in an.get("positives", []):
                        st.markdown(f"+ {pt}")
                with b2:
                    st.markdown("**Bear case**")
                    st.error(f"Stock: {an.get('bear_stock','—')}\nNifty: {an.get('nifty_bear','—')}")
                    for pt in an.get("risks", []):
                        st.markdown(f"- {pt}")

                st.markdown("**Critical metrics to watch on result day:**")
                for m in an.get("watch_metrics", []):
                    st.markdown(f"• {m}")

                if bw > 0 and an.get("banknifty") not in (None, "N/A", ""):
                    st.info(f"**BankNifty impact:** {an.get('banknifty')}")

                st.caption(f"Historical result-day moves: {an.get('reactions', [])}")
            else:
                st.warning("Could not fetch financial data. Screener.in may be rate-limiting.")

        # Calendar from last full scan
        cal = st.session_state.get("cal", [])
        if cal:
            st.divider()
            st.markdown("#### Upcoming results (from last full scan)")
            for ev in cal:
                days  = ev["days_away"]
                label = "TODAY" if days == 0 else "TOMORROW" if days == 1 else f"In {days} days"
                icon  = "🔴" if days <= 1 else "🟡" if days <= 3 else "🟢"
                with st.expander(f"{icon}  {ev['company']}  ({ev['symbol']})  —  {label}  ·  {ev['result_date']}"):
                    c1, c2 = st.columns(2)
                    c1.metric("Nifty weight",    f"{ev['nifty_wt']:.1f}%")
                    c2.metric("BankNifty weight", f"{ev['bn_wt']:.1f}%" if ev['bn_wt'] > 0 else "N/A")
                    if st.button(f"Deep analyse {ev['symbol']} ↗", key=f"c_{ev['symbol']}"):
                        with st.spinner(f"Analysing {ev['company']}…"):
                            an = analyse_earnings(ev["symbol"], ev["company"])
                        if an:
                            st.write(an.get("summary", ""))
                            x1, x2 = st.columns(2)
                            x1.success(f"Bull: {an.get('bull_stock','—')}")
                            x2.error(f"Bear: {an.get('bear_stock','—')}")
        elif not analyse_btn:
            st.info("Run a full scan to populate the earnings calendar, "
                    "or pick a stock from the sidebar for an instant deep analysis.")

    # ════════════════════════════════════════════════════════════
    # TAB: LIVE NEWS SIGNALS
    # ════════════════════════════════════════════════════════════
    with t_news:
        signals    = st.session_state.get("signals", [])
        all_clfd   = st.session_state.get("all_clfd", [])

        if not all_clfd:
            st.info("Run a full scan to see live news signals.")
        else:
            f1, f2, f3 = st.columns(3)
            dfilter = f1.selectbox("Direction", ["All","CRASH","RALLY","MIXED","NEUTRAL"])
            mfilter = f2.selectbox("Magnitude", ["All","EXTREME","HIGH","MEDIUM","LOW"])
            show_all = f3.checkbox("Show non-material news too")

            pool = all_clfd if show_all else signals
            if dfilter != "All": pool = [s for s in pool if s.get("direction") == dfilter]
            if mfilter != "All": pool = [s for s in pool if s.get("magnitude") == mfilter]

            st.caption(f"Showing {len(pool)} signals")

            for s in pool:
                pb   = s.get("_pb", {})
                icon = pb.get("icon", "📌")
                d    = s.get("direction", "NEUTRAL")
                conf = s.get("confidence", 0)
                title = s.get("_title", "")

                with st.expander(f"{icon}  [{d}]  {title[:85]}  —  {conf}% confidence"):
                    r1, r2, r3 = st.columns(3)
                    r1.markdown(f"**Category:** {pb.get('label', s.get('category','—'))}")
                    r2.markdown(f"**Direction:** {d}  |  {s.get('magnitude','—')}")
                    r3.markdown(f"**Timing:** {s.get('timing','—')}")

                    r4, r5 = st.columns(2)
                    r4.markdown(f"**Nifty impact:** {s.get('nifty_impact','—')}")
                    r5.markdown(f"**BankNifty:** {s.get('bn_multiplier',1.0):.1f}× Nifty move")

                    secs = s.get("sectors", [])
                    if secs:
                        st.markdown(f"**Sectors:** {', '.join(secs)}")

                    rel = s.get("related_stock")
                    if rel:
                        st.markdown(f"**Related stock:** `{rel}`")

                    st.markdown(f"**Reasoning:** {s.get('reasoning','—')}")
                    st.markdown(f"**Historical precedent:** {pb.get('examples','—')}")

                    url = s.get("_url","")
                    if url:
                        st.markdown(f"[Read full article →]({url})")
                    st.caption(f"Source: {s.get('_source','')}  ·  {s.get('_published','')}")

                    # Deep-dive button if news is about a specific F&O stock
                    if rel and rel in FNO:
                        if st.button(f"Deep analyse {rel} earnings ↗", key=f"n_{_ck(title)}"):
                            _, co, _, _ = FNO[rel]
                            with st.spinner(f"Analysing {rel}…"):
                                an = analyse_earnings(rel, co)
                            if an:
                                st.write(an.get("summary",""))
                                z1, z2 = st.columns(2)
                                z1.success(f"Bull: {an.get('bull_stock','—')}")
                                z2.error(f"Bear: {an.get('bear_stock','—')}")

    # ════════════════════════════════════════════════════════════
    # TAB: MARKET OVERVIEW
    # ════════════════════════════════════════════════════════════
    with t_overview:
        ov       = st.session_state.get("ov")
        signals  = st.session_state.get("signals", [])
        cal      = st.session_state.get("cal", [])
        scanned  = st.session_state.get("scanned", "")

        if not ov:
            st.info("Click **Run full scan** in the sidebar to start.")
            st.markdown("""
**What this app does:**
- Scans 7 Indian financial news RSS feeds in real time
- Classifies each headline against the 20-year Nifty playbook (15 categories)
- Estimates Nifty / BankNifty impact, timing, and affected sectors
- Tracks earnings calendars for 24 F&O stocks via BSE API
- Deep-analyses quarterly financials with YoY/QoQ comparison, bull/bear case, and historical result-day reactions
""")
        else:
            st.caption(f"Last scan: {scanned}")

            lbl = ov["label"]
            getattr(st, ov["color"])(f"### Overall market view: {lbl}  (score: {ov['net']:+.2f})")

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Bearish signals",    ov["crash"])
            m2.metric("Bullish signals",    ov["rally"])
            m3.metric("Upcoming results",   len(cal))
            m4.metric("Net signal score",   f"{ov['net']:+.2f}")

            st.divider()

            if signals:
                st.markdown("**Top signals this scan**")
                top5 = sorted(signals, key=lambda s: s.get("confidence",0), reverse=True)[:5]
                for s in top5:
                    pb   = s.get("_pb", {})
                    icon = pb.get("icon","📌")
                    d    = s.get("direction","NEUTRAL")
                    st.markdown(
                        f"{icon} {_badge(d)} &nbsp;"
                        f"**{s.get('_title','')[:90]}**  \n"
                        f"<small style='color:gray'>"
                        f"{s.get('_source','')} · "
                        f"{pb.get('label','')} · "
                        f"Nifty: {s.get('nifty_impact','?')} · "
                        f"{s.get('confidence',0)}% confidence"
                        f"</small>",
                        unsafe_allow_html=True,
                    )
                    st.markdown("")

            urgent = [e for e in cal if e["days_away"] <= 1]
            if urgent:
                st.divider()
                st.markdown("**🔴 Results today / tomorrow — high watch**")
                for ev in urgent:
                    label = "TODAY" if ev["days_away"] == 0 else "TOMORROW"
                    st.warning(
                        f"**{ev['company']}  ({ev['symbol']})  — {label}**  \n"
                        f"Nifty weight: {ev['nifty_wt']:.1f}%  |  "
                        f"BankNifty weight: {ev['bn_wt']:.1f}%  \n"
                        f"Go to the **Earnings calendar** tab for deep analysis."
                    )

    # ════════════════════════════════════════════════════════════
    # FULL SCAN EXECUTION
    # ════════════════════════════════════════════════════════════
    if run_btn:
        if not get_api_key():
            st.error("Add ANTHROPIC_API_KEY to Streamlit Secrets first.")
            st.stop()

        prog = st.progress(0, text="Fetching news feeds…")

        # Step 1 — Fetch news
        all_news = fetch_news(max_per_feed=news_count // len(NEWS_FEEDS) + 1)
        all_news = all_news[:news_count]
        prog.progress(15, text=f"Fetched {len(all_news)} headlines. Classifying…")

        # Step 2 — Classify
        all_clfd = []
        for i, item in enumerate(all_news):
            clf = classify_one(item)
            clf["_title"]     = item["title"]
            clf["_source"]    = item["source"]
            clf["_url"]       = item["url"]
            clf["_published"] = item["published"]
            clf["_pb"]        = PLAYBOOK.get(clf.get("category","uncategorised"),
                                             PLAYBOOK["uncategorised"])
            all_clfd.append(clf)
            prog.progress(15 + int(55 * i / max(len(all_news),1)),
                          text=f"Classifying {i+1}/{len(all_news)}…")

        signals = [c for c in all_clfd
                   if c.get("is_material") and c.get("confidence",0) >= min_conf]

        # Step 3 — Earnings calendar
        prog.progress(75, text="Fetching earnings calendar from BSE…")
        cal = earnings_calendar(days_ahead=earn_days)

        # Step 4 — Overall view
        ov = overall_view(signals)

        st.session_state.update({
            "all_clfd": all_clfd,
            "signals":  signals,
            "cal":      cal,
            "ov":       ov,
            "scanned":  datetime.now().strftime("%d %b %Y  %H:%M IST"),
        })

        prog.progress(100, text="Done.")
        time.sleep(0.4)
        prog.empty()
        st.rerun()


if __name__ == "__main__":
    main()
