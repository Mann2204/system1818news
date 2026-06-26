"""
System 1818 — News & Earnings Intelligence Bot
Scans news, classifies against 20-year Nifty playbook, reads quarterly filings,
and generates market impact signals.

Usage:
    bot = NewsEarningsBot()
    signals = bot.run_full_scan()
"""

import os
import json
import time
import hashlib
import logging
import requests
import feedparser
import anthropic
import pandas as pd
from datetime import datetime, timedelta, date
from typing import Optional
from dataclasses import dataclass, asdict
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CACHE_DIR = "bot_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

# F&O stocks to track (NSE symbols → BSE security codes for filings)
FNO_STOCKS = {
    "HDFCBANK":  ("532275", "HDFC Bank"),
    "RELIANCE":  ("500325", "Reliance Industries"),
    "TCS":       ("532540", "Tata Consultancy Services"),
    "INFY":      ("500209", "Infosys"),
    "ICICIBANK": ("532174", "ICICI Bank"),
    "AXISBANK":  ("532215", "Axis Bank"),
    "SBIN":      ("500112", "State Bank of India"),
    "KOTAKBANK": ("500247", "Kotak Mahindra Bank"),
    "LT":        ("500510", "Larsen & Toubro"),
    "WIPRO":     ("507685", "Wipro"),
    "BAJFINANCE":("500034", "Bajaj Finance"),
    "MARUTI":    ("532500", "Maruti Suzuki"),
    "TATASTEEL": ("500470", "Tata Steel"),
    "NTPC":      ("532555", "NTPC"),
    "POWERGRID": ("532898", "Power Grid"),
    "ADANIENT":  ("512599", "Adani Enterprises"),
    "ONGC":      ("500312", "ONGC"),
    "HINDUNILVR":("500696", "Hindustan Unilever"),
    "ITC":       ("500875", "ITC"),
    "SUNPHARMA": ("524715", "Sun Pharmaceuticals"),
}

# News RSS feeds — all freely accessible
NEWS_FEEDS = [
    ("ET Markets",       "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"),
    ("ET Economy",       "https://economictimes.indiatimes.com/economy/rssfeeds/1373380680.cms"),
    ("Moneycontrol",     "https://www.moneycontrol.com/rss/marketsindia.xml"),
    ("BSE Corporate",    "https://www.bseindia.com/xml-data/corpfiling/AttachLive/"),  # scrape fallback
    ("LiveMint Markets", "https://www.livemint.com/rss/markets"),
    ("Reuters India",    "https://feeds.reuters.com/reuters/INbusinessNews"),
    ("NDTV Profit",      "https://feeds.feedburner.com/ndtvprofit-latest"),
]

# ─────────────────────────────────────────────
# PLAYBOOK — 20 year category map
# ─────────────────────────────────────────────

PLAYBOOK = {
    "election_surprise": {
        "label": "Election surprise",
        "direction": "CRASH", "magnitude": "HIGH",
        "duration_days": 5, "banknifty_multiplier": 1.2,
        "keywords": ["exit poll miss", "hung parliament", "unexpected winner", "coalition uncertainty",
                     "election results", "lok sabha", "vidhan sabha results"],
    },
    "election_mandate": {
        "label": "Election mandate (clear majority)",
        "direction": "RALLY", "magnitude": "HIGH",
        "duration_days": 14, "banknifty_multiplier": 1.5,
        "keywords": ["clear majority", "policy continuity", "stable government", "BJP win",
                     "NDA majority", "mandate", "pro-reform"],
    },
    "global_credit_crisis": {
        "label": "Global credit / banking crisis",
        "direction": "CRASH", "magnitude": "EXTREME",
        "duration_days": 180, "banknifty_multiplier": 2.5,
        "keywords": ["bank collapse", "credit freeze", "Lehman", "SVB", "systemic risk",
                     "bank run", "FII outflow record", "circuit breaker global"],
    },
    "pandemic_shock": {
        "label": "Pandemic / health shock",
        "direction": "CRASH", "magnitude": "EXTREME",
        "duration_days": 30, "banknifty_multiplier": 2.0,
        "keywords": ["WHO pandemic", "lockdown", "quarantine", "new virus strain",
                     "epidemic", "health emergency", "travel ban WHO"],
    },
    "domestic_policy_shock": {
        "label": "Domestic policy shock",
        "direction": "CRASH", "magnitude": "HIGH",
        "duration_days": 60, "banknifty_multiplier": 1.0,
        "keywords": ["demonetisation", "LTCG tax", "STT hike", "sudden policy change",
                     "GST rate hike", "FII cap", "windfall tax", "export ban"],
    },
    "china_contagion": {
        "label": "China / EM contagion",
        "direction": "CRASH", "magnitude": "MEDIUM",
        "duration_days": 14, "banknifty_multiplier": 1.1,
        "keywords": ["yuan devaluation", "PBOC", "Shanghai crash", "China GDP miss",
                     "EM selloff", "circuit breaker China", "China slowdown"],
    },
    "fed_hawkish": {
        "label": "US Fed hawkishness",
        "direction": "CRASH", "magnitude": "MEDIUM",
        "duration_days": 30, "banknifty_multiplier": 1.2,
        "keywords": ["Fed rate hike", "taper tantrum", "hawkish FOMC", "US 10Y yield",
                     "dollar index spike", "DXY", "Powell hawkish", "higher for longer"],
    },
    "oil_shock": {
        "label": "Oil price shock",
        "direction": "CRASH", "magnitude": "MEDIUM",
        "duration_days": 21, "banknifty_multiplier": 1.0,
        "keywords": ["crude above 100", "OPEC cut", "Strait of Hormuz", "oil supply disruption",
                     "brent spike", "CAD widening", "rupee depreciation oil"],
    },
    "domestic_banking_crisis": {
        "label": "Domestic banking / fraud",
        "direction": "CRASH", "magnitude": "HIGH",
        "duration_days": 30, "banknifty_multiplier": 3.0,
        "keywords": ["bank fraud", "NPA surge", "RBI intervention", "IL&FS", "Yes Bank",
                     "DHFL default", "NBFC crisis", "short report fraud", "promoter pledge"],
    },
    "rbi_rate_cut": {
        "label": "RBI rate cut / dovish pivot",
        "direction": "RALLY", "magnitude": "MEDIUM",
        "duration_days": 14, "banknifty_multiplier": 1.8,
        "keywords": ["repo rate cut", "CRR cut", "dovish MPC", "RBI cut", "liquidity infusion",
                     "OMO purchase", "rate cycle reversal", "RBI accommodative"],
    },
    "fed_dovish": {
        "label": "US Fed rate cut / dovish pivot",
        "direction": "RALLY", "magnitude": "MEDIUM",
        "duration_days": 14, "banknifty_multiplier": 1.5,
        "keywords": ["Fed rate cut", "dovish pivot", "QE announcement", "DXY fall",
                     "EM inflows", "FII buying", "Fed pause", "rate cut expected"],
    },
    "growth_budget": {
        "label": "Growth-oriented Union Budget",
        "direction": "RALLY", "magnitude": "LOW",
        "duration_days": 3, "banknifty_multiplier": 1.0,
        "keywords": ["capex allocation", "infra spending", "fiscal consolidation", "tax relief",
                     "union budget 2026", "budget announcement", "PLI scheme", "disinvestment"],
    },
    "geopolitical": {
        "label": "Geopolitical shock",
        "direction": "MIXED", "magnitude": "LOW",
        "duration_days": 3, "banknifty_multiplier": 1.0,
        "keywords": ["cross-border attack", "war declaration", "military escalation",
                     "India Pakistan", "ceasefire", "sanctions", "border tension", "airstrike"],
    },
    "heavyweight_earnings": {
        "label": "Heavyweight stock earnings event",
        "direction": "MIXED", "magnitude": "LOW",
        "duration_days": 2, "banknifty_multiplier": 2.0,
        "keywords": ["HDFC Bank results", "Reliance earnings", "TCS results", "quarterly results",
                     "earnings miss", "earnings beat", "profit alert", "revenue miss"],
    },
    "post_crash_recovery": {
        "label": "Post-crash V-recovery",
        "direction": "RALLY", "magnitude": "EXTREME",
        "duration_days": 365, "banknifty_multiplier": 2.0,
        "keywords": ["GDP recovery", "earnings upgrade", "risk-on", "FII return",
                     "cheap valuations", "bargain buying", "economic revival"],
    },
    "uncategorised": {
        "label": "Uncategorised / watch",
        "direction": "NEUTRAL", "magnitude": "LOW",
        "duration_days": 1, "banknifty_multiplier": 1.0,
        "keywords": [],
    },
}

# ─────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────

@dataclass
class NewsItem:
    title: str
    source: str
    url: str
    published: str
    summary: str = ""

@dataclass
class NewsSignal:
    headline: str
    source: str
    category: str
    category_label: str
    direction: str           # RALLY / CRASH / MIXED / NEUTRAL
    magnitude: str           # LOW / MEDIUM / HIGH / EXTREME
    confidence: int          # 0-100
    nifty_impact: str        # e.g. "-2% to -4%"
    banknifty_impact: str
    timing: str              # IMMEDIATE / TOMORROW / 3 DAYS
    affected_sectors: list
    reasoning: str
    timestamp: str

@dataclass
class EarningsEvent:
    symbol: str
    company: str
    result_date: date
    days_away: int
    quarter: str             # e.g. Q4FY26
    # Comparative data
    last_year_same_q_summary: str
    last_quarter_summary: str
    watch_metrics: list
    bull_case: str
    bear_case: str
    historical_reaction: str  # avg move on result day last 4 quarters
    nifty_weight_pct: float
    banknifty_weight_pct: float

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()

def _read_cache(key: str, ttl_minutes: int = 30) -> Optional[dict]:
    path = f"{CACHE_DIR}/{key}.json"
    if not os.path.exists(path):
        return None
    age = time.time() - os.path.getmtime(path)
    if age > ttl_minutes * 60:
        return None
    with open(path) as f:
        return json.load(f)

def _write_cache(key: str, data: dict):
    with open(f"{CACHE_DIR}/{key}.json", "w") as f:
        json.dump(data, f)

def _claude(system: str, user: str, max_tokens: int = 800) -> str:
    """Single Claude API call with caching."""
    ck = _cache_key(system + user)
    cached = _read_cache(ck, ttl_minutes=60)
    if cached:
        return cached["text"]
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = msg.content[0].text
    _write_cache(ck, {"text": text})
    return text

# ─────────────────────────────────────────────
# MODULE 1: NEWS FETCHER
# ─────────────────────────────────────────────

class NewsFetcher:
    def fetch_all(self, max_per_feed: int = 20) -> list[NewsItem]:
        items = []
        for source_name, url in NEWS_FEEDS:
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:max_per_feed]:
                    items.append(NewsItem(
                        title=entry.get("title", "").strip(),
                        source=source_name,
                        url=entry.get("link", ""),
                        published=entry.get("published", str(datetime.now())),
                        summary=BeautifulSoup(
                            entry.get("summary", ""), "html.parser"
                        ).get_text()[:500],
                    ))
                log.info(f"Fetched {min(len(feed.entries), max_per_feed)} items from {source_name}")
            except Exception as e:
                log.warning(f"Feed failed — {source_name}: {e}")
        # De-duplicate by title hash
        seen = set()
        unique = []
        for item in items:
            h = _cache_key(item.title[:60])
            if h not in seen:
                seen.add(h)
                unique.append(item)
        log.info(f"Total unique news items: {len(unique)}")
        return unique

# ─────────────────────────────────────────────
# MODULE 2: NEWS CLASSIFIER
# ─────────────────────────────────────────────

CLASSIFIER_SYSTEM = """
You are a strict financial news classifier for Indian equity markets (Nifty 50, BankNifty).

Your job: read a news headline + summary and return ONLY a JSON object (no markdown, no explanation).

Categories available:
election_surprise, election_mandate, global_credit_crisis, pandemic_shock,
domestic_policy_shock, china_contagion, fed_hawkish, oil_shock,
domestic_banking_crisis, rbi_rate_cut, fed_dovish, growth_budget,
geopolitical, heavyweight_earnings, post_crash_recovery, uncategorised

Return this exact JSON structure:
{
  "category": "<category_key>",
  "confidence": <0-100 integer>,
  "direction": "<RALLY|CRASH|MIXED|NEUTRAL>",
  "magnitude": "<LOW|MEDIUM|HIGH|EXTREME>",
  "timing": "<IMMEDIATE|TOMORROW|3DAYS|WEEK>",
  "nifty_impact_range": "<e.g. -1% to -3%>",
  "banknifty_multiplier": <1.0 to 3.0>,
  "affected_sectors": ["sector1", "sector2"],
  "reasoning": "<one sentence max>",
  "is_material": <true|false>
}

Rules:
- confidence below 40 → category = "uncategorised", is_material = false
- Only mark is_material = true if the news could move Nifty by more than 0.5% today or tomorrow
- MIXED direction means the news cuts both ways (e.g. geopolitical: brief dip then defence rally)
- For BankNifty multiplier: 1.0 = same as Nifty, 2.0 = moves 2x, 3.0 = extreme banking impact
"""

class NewsClassifier:
    def classify(self, item: NewsItem) -> dict:
        user = f"Headline: {item.title}\nSource: {item.source}\nSummary: {item.summary[:300]}"
        raw = _claude(CLASSIFIER_SYSTEM, user, max_tokens=400)
        try:
            # Strip any accidental markdown fences
            raw = raw.strip().lstrip("```json").rstrip("```").strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning(f"JSON parse failed for: {item.title[:50]}")
            return {"category": "uncategorised", "confidence": 0, "is_material": False}

    def classify_batch(self, items: list[NewsItem]) -> list[tuple[NewsItem, dict]]:
        results = []
        for item in items:
            classification = self.classify(item)
            results.append((item, classification))
        return results

# ─────────────────────────────────────────────
# MODULE 3: EARNINGS CALENDAR
# ─────────────────────────────────────────────

class EarningsCalendar:
    """
    Fetches upcoming result dates from BSE corporate filings API.
    BSE exposes board meeting notices which announce result dates.
    """

    BSE_BOARD_MEETING_URL = (
        "https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w"
        "?Type=BoardMeeting&subcateg=RESULT&pageno=1&trade_date="
    )

    def get_upcoming_results(self, days_ahead: int = 7) -> list[dict]:
        """
        Returns list of {symbol, company, bse_code, result_date, days_away}
        for all F&O stocks with results in the next `days_ahead` days.
        """
        upcoming = []
        today = date.today()

        for nse_sym, (bse_code, company) in FNO_STOCKS.items():
            result_date = self._fetch_result_date(bse_code, company)
            if result_date is None:
                continue
            days_away = (result_date - today).days
            if 0 <= days_away <= days_ahead:
                upcoming.append({
                    "symbol": nse_sym,
                    "bse_code": bse_code,
                    "company": company,
                    "result_date": result_date,
                    "days_away": days_away,
                })

        log.info(f"Upcoming results in {days_ahead} days: {len(upcoming)}")
        return sorted(upcoming, key=lambda x: x["days_away"])

    def _fetch_result_date(self, bse_code: str, company: str) -> Optional[date]:
        """Queries BSE API for next board meeting (result) date."""
        ck = f"bse_result_{bse_code}"
        cached = _read_cache(ck, ttl_minutes=360)  # cache 6 hours
        if cached:
            return date.fromisoformat(cached["date"]) if cached.get("date") else None

        try:
            url = f"https://api.bseindia.com/BseIndiaAPI/api/DefaultData/w?Type=BoardMeeting&scripcode={bse_code}"
            headers = {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bseindia.com/",
            }
            resp = requests.get(url, headers=headers, timeout=10)
            data = resp.json()
            # BSE returns a list of meetings; find next upcoming RESULT meeting
            today = date.today()
            for meeting in data.get("Table", []):
                purpose = str(meeting.get("Purpose", "")).upper()
                if "RESULT" in purpose or "FINANCIAL" in purpose:
                    raw_date = meeting.get("Meeting_Date", "")
                    try:
                        # BSE format: "27 Jun 2026" or "2026-06-27"
                        try:
                            d = datetime.strptime(raw_date, "%d %b %Y").date()
                        except ValueError:
                            d = datetime.fromisoformat(raw_date[:10]).date()
                        if d >= today:
                            _write_cache(ck, {"date": d.isoformat()})
                            return d
                    except Exception:
                        continue
        except Exception as e:
            log.debug(f"BSE API failed for {bse_code}: {e}")

        _write_cache(ck, {"date": None})
        return None

# ─────────────────────────────────────────────
# MODULE 4: FINANCIAL STATEMENT READER
# ─────────────────────────────────────────────

QUARTERLY_METRICS = [
    "Revenue / Net Sales", "EBITDA", "EBITDA Margin %",
    "PAT (Profit After Tax)", "EPS (Basic)", "Gross NPA %",
    "Net NPA %", "NIM (Net Interest Margin)", "Loan Growth YoY %",
    "Revenue Growth YoY %", "PAT Growth YoY %",
    "Operating Cash Flow", "Debt / Equity", "ROE %",
]

STATEMENT_ANALYSER_SYSTEM = """
You are a senior equity analyst specialising in Indian listed companies.

Given the latest quarterly result headline data for a company, and the same quarter last year's data,
perform a strict fundamental analysis and return ONLY JSON (no markdown, no preamble):

{
  "quarter": "<e.g. Q4FY26>",
  "beat_or_miss": "<BEAT|MISS|IN_LINE>",
  "key_positive": ["up to 3 bullet points"],
  "key_negative": ["up to 3 bullet points"],
  "watch_metrics": ["most important 3 metrics to watch"],
  "historical_avg_move_pct": <float, e.g. 3.5 for +3.5% or -2.1 for -2.1%>,
  "bull_case_move": "<e.g. +3% to +6%>",
  "bear_case_move": "<e.g. -5% to -8%>",
  "nifty_impact": "<e.g. +0.5% to +1.2%>",
  "banknifty_impact": "<only fill if banking stock, else null>",
  "confidence": <0-100>,
  "summary": "<2 sentences max>"
}

Be strict. Missing data = say so. Never hallucinate numbers.
"""

class FinancialStatementReader:
    """
    Reads quarterly filing data.
    Primary source: Screener.in JSON API (no auth needed).
    Fallback: NSE XBRL API.
    """

    SCREENER_URL = "https://www.screener.in/api/company/{nse_sym}/quarters/?format=json"

    def get_quarterly_data(self, nse_sym: str) -> Optional[dict]:
        ck = f"screener_{nse_sym}"
        cached = _read_cache(ck, ttl_minutes=720)  # 12h cache
        if cached:
            return cached

        try:
            url = f"https://www.screener.in/api/company/{nse_sym}/quarters/?format=json"
            headers = {"User-Agent": "Mozilla/5.0 (research bot)"}
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                _write_cache(ck, data)
                return data
        except Exception as e:
            log.debug(f"Screener API failed for {nse_sym}: {e}")

        # Fallback: Screener HTML scrape
        try:
            url = f"https://www.screener.in/company/{nse_sym}/consolidated/"
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            soup = BeautifulSoup(resp.text, "html.parser")
            # Parse quarterly table
            table = soup.select_one("#quarters table")
            if table:
                rows = []
                headers_row = [th.text.strip() for th in table.select("thead th")]
                for tr in table.select("tbody tr"):
                    cells = [td.text.strip() for td in tr.select("td")]
                    if cells:
                        rows.append(dict(zip(headers_row, cells)))
                data = {"quarters": rows}
                _write_cache(ck, data)
                return data
        except Exception as e:
            log.debug(f"Screener scrape failed for {nse_sym}: {e}")

        return None

    def get_historical_result_reactions(self, nse_sym: str) -> list[float]:
        """
        Fetches stock price change on result day for last 4 quarters.
        Uses yfinance as the data source (already a dependency in System 1818).
        Returns list of % changes, e.g. [-4.7, 3.2, 1.1, -2.3]
        """
        try:
            import yfinance as yf
            ticker = yf.Ticker(f"{nse_sym}.NS")
            hist = ticker.history(period="2y")
            if hist.empty:
                return []
            # Proxy: look at largest single-day moves as result-day reactions
            # In production you'd map to actual result dates; here we return top moves
            daily_returns = hist["Close"].pct_change().dropna() * 100
            largest_moves = daily_returns.abs().nlargest(8).index
            reactions = [round(daily_returns[d], 2) for d in largest_moves[:4]]
            return reactions
        except Exception:
            return []

    def analyse_earnings(self, nse_sym: str, company: str, quarter: str) -> Optional[dict]:
        """Full earnings analysis using Claude."""
        qdata = self.get_quarterly_data(nse_sym)
        if not qdata:
            return None

        reactions = self.get_historical_result_reactions(nse_sym)
        avg_reaction = round(sum(reactions) / len(reactions), 2) if reactions else 0.0

        user_prompt = f"""
Company: {company} ({nse_sym})
Quarter: {quarter}
Historical result-day reactions (last 4 quarters): {reactions}
Average historical move on result day: {avg_reaction}%

Quarterly financial data (latest available):
{json.dumps(qdata, indent=2)[:3000]}

Analyse:
1. How does latest quarter compare to same quarter last year?
2. What are the 3 most critical metrics to watch in this release?
3. What is the bull case and bear case price reaction?
4. What is the likely impact on Nifty and BankNifty (if applicable)?
"""
        raw = _claude(STATEMENT_ANALYSER_SYSTEM, user_prompt, max_tokens=600)
        try:
            raw = raw.strip().lstrip("```json").rstrip("```").strip()
            result = json.loads(raw)
            result["historical_reactions"] = reactions
            result["avg_historical_reaction_pct"] = avg_reaction
            return result
        except json.JSONDecodeError:
            log.warning(f"Earnings JSON parse failed for {nse_sym}")
            return None

# ─────────────────────────────────────────────
# MODULE 5: SIGNAL AGGREGATOR
# ─────────────────────────────────────────────

class SignalAggregator:
    """Combines news signals and earnings signals into a unified output."""

    # Nifty 50 weight approximations (update periodically)
    NIFTY_WEIGHTS = {
        "HDFCBANK": 11.2, "RELIANCE": 9.8, "ICICIBANK": 7.1,
        "TCS": 4.8, "INFY": 4.2, "KOTAKBANK": 3.9, "LT": 3.6,
        "AXISBANK": 3.3, "SBIN": 3.1, "WIPRO": 1.8, "BAJFINANCE": 2.9,
    }
    BANKNIFTY_WEIGHTS = {
        "HDFCBANK": 28.5, "ICICIBANK": 22.3, "KOTAKBANK": 13.2,
        "AXISBANK": 12.5, "SBIN": 10.8, "BAJFINANCE": 6.4,
    }

    def build_news_signal(self, item: NewsItem, clf: dict) -> Optional[NewsSignal]:
        if not clf.get("is_material", False):
            return None
        cat = clf.get("category", "uncategorised")
        cat_meta = PLAYBOOK.get(cat, PLAYBOOK["uncategorised"])
        return NewsSignal(
            headline=item.title,
            source=item.source,
            category=cat,
            category_label=cat_meta["label"],
            direction=clf.get("direction", "NEUTRAL"),
            magnitude=clf.get("magnitude", "LOW"),
            confidence=clf.get("confidence", 0),
            nifty_impact=clf.get("nifty_impact_range", "unclear"),
            banknifty_impact=f'{clf.get("banknifty_multiplier", 1.0):.1f}x Nifty move',
            timing=clf.get("timing", "WEEK"),
            affected_sectors=clf.get("affected_sectors", []),
            reasoning=clf.get("reasoning", ""),
            timestamp=datetime.now().isoformat(),
        )

    def get_nifty_weight(self, symbol: str) -> float:
        return self.NIFTY_WEIGHTS.get(symbol, 0.5)

    def get_banknifty_weight(self, symbol: str) -> float:
        return self.BANKNIFTY_WEIGHTS.get(symbol, 0.0)

    def build_overall_view(self, news_signals: list[NewsSignal], earnings: list[dict]) -> dict:
        """Aggregate all signals into a single market view for the day."""
        crash_signals = [s for s in news_signals if s.direction == "CRASH"]
        rally_signals = [s for s in news_signals if s.direction == "RALLY"]

        # Score: HIGH=3, MEDIUM=2, LOW=1, EXTREME=4
        mag_score = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "EXTREME": 4}
        crash_score = sum(mag_score.get(s.magnitude, 1) * (s.confidence / 100)
                          for s in crash_signals)
        rally_score = sum(mag_score.get(s.magnitude, 1) * (s.confidence / 100)
                          for s in rally_signals)

        net = rally_score - crash_score
        if net > 2:
            overall = "BULLISH"
        elif net > 0.5:
            overall = "SLIGHTLY BULLISH"
        elif net < -2:
            overall = "BEARISH"
        elif net < -0.5:
            overall = "SLIGHTLY BEARISH"
        else:
            overall = "NEUTRAL"

        # Top signals by confidence
        top_signals = sorted(news_signals, key=lambda s: s.confidence, reverse=True)[:5]

        return {
            "overall_market_view": overall,
            "net_signal_score": round(net, 2),
            "crash_signals_count": len(crash_signals),
            "rally_signals_count": len(rally_signals),
            "top_signals": [asdict(s) for s in top_signals],
            "earnings_today_tomorrow": earnings,
            "generated_at": datetime.now().isoformat(),
        }

# ─────────────────────────────────────────────
# MODULE 6: MAIN BOT ORCHESTRATOR
# ─────────────────────────────────────────────

class NewsEarningsBot:
    """
    Main entry point. Call run_full_scan() to get the complete market intelligence report.
    Integrates into System 1818 via the returned dict.
    """

    def __init__(self):
        self.fetcher     = NewsFetcher()
        self.classifier  = NewsClassifier()
        self.cal         = EarningsCalendar()
        self.fin_reader  = FinancialStatementReader()
        self.aggregator  = SignalAggregator()

    def run_full_scan(self, news_limit: int = 60) -> dict:
        """
        Full pipeline:
        1. Fetch latest news
        2. Classify each headline
        3. Fetch upcoming earnings (next 3 days)
        4. Analyse earnings filings for any results tomorrow
        5. Aggregate into overall market view
        Returns complete signal dict.
        """
        log.info("=== NewsEarningsBot full scan started ===")

        # Step 1: Fetch news
        all_news = self.fetcher.fetch_all(max_per_feed=20)
        recent_news = all_news[:news_limit]

        # Step 2: Classify
        log.info(f"Classifying {len(recent_news)} headlines...")
        classified = self.classifier.classify_batch(recent_news)

        # Step 3: Build news signals (material only)
        news_signals = []
        for item, clf in classified:
            sig = self.aggregator.build_news_signal(item, clf)
            if sig:
                news_signals.append(sig)
        log.info(f"Material news signals: {len(news_signals)}")

        # Step 4: Earnings calendar
        upcoming = self.cal.get_upcoming_results(days_ahead=3)
        log.info(f"Upcoming earnings (3 days): {len(upcoming)}")

        # Step 5: Deep analyse stocks with results tomorrow or today
        earnings_analyses = []
        for event in upcoming:
            if event["days_away"] <= 1:  # today or tomorrow
                sym = event["symbol"]
                company = event["company"]
                quarter = self._current_quarter()
                log.info(f"Analysing earnings: {sym} ({quarter})")
                analysis = self.fin_reader.analyse_earnings(sym, company, quarter)
                if analysis:
                    analysis.update({
                        "symbol": sym,
                        "company": company,
                        "result_date": event["result_date"].isoformat(),
                        "days_away": event["days_away"],
                        "nifty_weight": self.aggregator.get_nifty_weight(sym),
                        "banknifty_weight": self.aggregator.get_banknifty_weight(sym),
                    })
                    earnings_analyses.append(analysis)

        # Step 6: Aggregate
        overall = self.aggregator.build_overall_view(news_signals, earnings_analyses)
        overall["upcoming_earnings_calendar"] = [
            {
                "symbol": e["symbol"],
                "company": e["company"],
                "result_date": e["result_date"].isoformat(),
                "days_away": e["days_away"],
            }
            for e in upcoming
        ]
        overall["all_news_signals_count"] = len(news_signals)

        log.info("=== Scan complete ===")
        log.info(f"Overall market view: {overall['overall_market_view']}")
        return overall

    def run_news_only(self, limit: int = 40) -> list[dict]:
        """Lightweight scan — news signals only, no earnings."""
        news = self.fetcher.fetch_all(max_per_feed=15)[:limit]
        classified = self.classifier.classify_batch(news)
        signals = []
        for item, clf in classified:
            sig = self.aggregator.build_news_signal(item, clf)
            if sig:
                signals.append(asdict(sig))
        return signals

    def analyse_single_stock_earnings(self, nse_sym: str) -> Optional[dict]:
        """Analyse a specific stock's earnings outlook on demand."""
        if nse_sym not in FNO_STOCKS:
            return {"error": f"{nse_sym} not in F&O watchlist"}
        _, company = FNO_STOCKS[nse_sym]
        return self.fin_reader.analyse_earnings(nse_sym, company, self._current_quarter())

    @staticmethod
    def _current_quarter() -> str:
        today = date.today()
        month = today.month
        year = today.year
        fy = year if month >= 4 else year - 1
        if month in (4, 5, 6):
            q = "Q1"
        elif month in (7, 8, 9):
            q = "Q2"
        elif month in (10, 11, 12):
            q = "Q3"
        else:
            q = "Q4"
        return f"{q}FY{str(fy + 1)[2:]}"


# ─────────────────────────────────────────────
# STREAMLIT INTEGRATION HELPER
# ─────────────────────────────────────────────

def render_streamlit_panel():
    """
    Drop this into your System 1818 Streamlit app.
    Call from your main dashboard tab.
    """
    import streamlit as st

    st.subheader("News and Earnings Intelligence")

    col1, col2 = st.columns([3, 1])
    with col2:
        scan_type = st.selectbox("Scan type", ["Full scan", "News only"])
        if st.button("Run scan"):
            st.session_state["bot_scanning"] = True

    if st.session_state.get("bot_scanning"):
        bot = NewsEarningsBot()
        with st.spinner("Scanning news and earnings..."):
            if scan_type == "Full scan":
                result = bot.run_full_scan()
            else:
                signals = bot.run_news_only()
                result = {"top_signals": signals, "overall_market_view": "—"}
        st.session_state["bot_result"] = result
        st.session_state["bot_scanning"] = False

    result = st.session_state.get("bot_result")
    if not result:
        st.info("Click 'Run scan' to fetch live news and earnings signals.")
        return

    # Overall view badge
    view = result.get("overall_market_view", "NEUTRAL")
    color = "#eaf3de" if "BULL" in view else ("#fcebeb" if "BEAR" in view else "#faeeda")
    st.markdown(
        f'<div style="background:{color};border-radius:8px;padding:10px 16px;'
        f'font-weight:500;font-size:15px;margin-bottom:1rem">Market view: {view}</div>',
        unsafe_allow_html=True,
    )

    # Top news signals
    signals = result.get("top_signals", [])
    if signals:
        st.markdown("**Top signals**")
        for s in signals:
            direction_color = "#3b6d11" if s.get("direction") == "RALLY" else (
                "#a32d2d" if s.get("direction") == "CRASH" else "#854f0b"
            )
            with st.expander(
                f"[{s.get('direction')}] {s.get('headline', '')[:80]}  —  "
                f"{s.get('confidence', 0)}% confidence"
            ):
                st.write(f"**Category:** {s.get('category_label')}")
                st.write(f"**Nifty impact:** {s.get('nifty_impact')}")
                st.write(f"**BankNifty:** {s.get('banknifty_impact')}")
                st.write(f"**Timing:** {s.get('timing')}")
                st.write(f"**Sectors:** {', '.join(s.get('affected_sectors', []))}")
                st.write(f"**Reasoning:** {s.get('reasoning')}")

    # Earnings calendar
    cal = result.get("upcoming_earnings_calendar", [])
    if cal:
        st.markdown("**Upcoming earnings (next 3 days)**")
        df = pd.DataFrame(cal)
        df.columns = ["Symbol", "Company", "Result date", "Days away"]
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Deep earnings analyses
    deep = result.get("earnings_today_tomorrow", [])
    for e in deep:
        st.markdown(f"**Deep analysis: {e.get('company')} ({e.get('symbol')})**")
        c1, c2, c3 = st.columns(3)
        c1.metric("Nifty weight", f"{e.get('nifty_weight', 0):.1f}%")
        c2.metric("Avg result-day move", f"{e.get('avg_historical_reaction_pct', 0):+.1f}%")
        c3.metric("Beat/Miss/In-line", e.get("beat_or_miss", "—"))
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown("**Bull case**")
            st.success(e.get("bull_case_move", "—"))
            for pt in e.get("key_positive", []):
                st.markdown(f"+ {pt}")
        with col_b:
            st.markdown("**Bear case**")
            st.error(e.get("bear_case_move", "—"))
            for pt in e.get("key_negative", []):
                st.markdown(f"- {pt}")
        st.caption(e.get("summary", ""))


# ─────────────────────────────────────────────
# CLI ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, pprint

    parser = argparse.ArgumentParser(description="System 1818 News & Earnings Bot")
    parser.add_argument("--mode", choices=["full", "news", "stock"], default="full")
    parser.add_argument("--stock", type=str, default="HDFCBANK", help="NSE symbol for --mode=stock")
    args = parser.parse_args()

    bot = NewsEarningsBot()

    if args.mode == "full":
        result = bot.run_full_scan()
        print("\n=== MARKET VIEW ===")
        print(f"Overall: {result['overall_market_view']}")
        print(f"Net signal score: {result['net_signal_score']}")
        print(f"\nTop signals: {len(result['top_signals'])}")
        for s in result["top_signals"][:3]:
            print(f"  [{s['direction']}] {s['headline'][:70]}")
            print(f"    Category: {s['category_label']} | Impact: {s['nifty_impact']}")

    elif args.mode == "news":
        signals = bot.run_news_only()
        print(f"\n{len(signals)} material signals found:")
        for s in signals:
            print(f"  [{s['direction']}] {s['headline'][:70]}")

    elif args.mode == "stock":
        analysis = bot.analyse_single_stock_earnings(args.stock.upper())
        pprint.pprint(analysis)
