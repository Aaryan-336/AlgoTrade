"""News ingestion from RSS (docs/data-sources.md §6).

Only headlines, links and short snippets are stored, never full articles.
Check each site's terms before enabling a feed. Social media is excluded.
"""

from __future__ import annotations

import calendar
import hashlib
import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import quote_plus

import feedparser
import httpx

from algotrade.core.types import Instrument

# General market feeds (verify URLs and terms periodically).
GENERAL_FEEDS: dict[str, str] = {
    "moneycontrol_latest": "https://www.moneycontrol.com/rss/latestnews.xml",
    "moneycontrol_markets": "https://www.moneycontrol.com/rss/marketreports.xml",
    "moneycontrol_business": "https://www.moneycontrol.com/rss/business.xml",
    "et_markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "et_stocks": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
}

FEED_PUBLISHER = {
    "moneycontrol_latest": "Moneycontrol", "moneycontrol_markets": "Moneycontrol",
    "moneycontrol_business": "Moneycontrol", "et_markets": "The Economic Times",
    "et_stocks": "The Economic Times",
}

# Credibility tiers weight sentiment (1.0 exchange, 0.8 major outlets, 0.5 other).
MAJOR_PUBLISHERS = {
    "moneycontrol", "the economic times", "economic times", "livemint", "mint",
    "business standard", "reuters", "bloomberg", "cnbc tv18", "cnbctv18", "cnbc-tv18",
    "financial express", "the financial express", "the hindu businessline",
    "hindu businessline", "businessline", "ndtv profit", "business today", "the hindu",
    "bse", "nse",
}
EXCHANGES = {"bse", "nse", "bse india", "nse india"}

_WS = re.compile(r"\s+")
_TAGS = re.compile(r"<[^>]+>")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class RawNews:
    id: str
    source: str
    publisher: str
    url: str
    headline: str
    snippet: str
    published_at: datetime
    symbols: tuple[str, ...]
    credibility: float


def clean_text(text: str, limit: int) -> str:
    text = html.unescape(_TAGS.sub(" ", text or ""))
    text = _CTRL.sub(" ", text)
    return _WS.sub(" ", text).strip()[:limit]


def credibility(publisher: str) -> float:
    p = publisher.strip().lower()
    if p in EXCHANGES:
        return 1.0
    if p in MAJOR_PUBLISHERS:
        return 0.8
    return 0.5


def news_id(headline: str) -> str:
    """Dedupe on the normalized headline so syndicated copies collapse."""
    norm = re.sub(r"[^a-z0-9 ]", "", headline.lower())
    return hashlib.sha256(_WS.sub(" ", norm).strip().encode()).hexdigest()[:32]


def _alias_patterns(inst: Instrument) -> list[re.Pattern[str]]:
    pats = []
    for a in {inst.name, *inst.aliases}:
        flags = 0 if a.isupper() else re.IGNORECASE  # "SBI", "ITC" match case-sensitively
        pats.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])", flags))
    return pats


class SymbolMatcher:
    def __init__(self, instruments: list[Instrument]) -> None:
        self._pats = {i.symbol: _alias_patterns(i) for i in instruments}

    def match(self, text: str) -> tuple[str, ...]:
        return tuple(sorted(s for s, pats in self._pats.items()
                            if any(p.search(text) for p in pats)))


def google_news_url(inst: Instrument, days: int) -> str:
    q = f'"{inst.name}" (share OR stock OR shares) when:{days}d'
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"


def _published(entry: dict[str, object], now: datetime) -> datetime | None:
    st = entry.get("published_parsed") or entry.get("updated_parsed")
    if not st:
        return None
    ts = datetime.fromtimestamp(calendar.timegm(st), tz=UTC)  # type: ignore[arg-type]
    return min(ts, now)


def parse_feed(content: bytes, source: str, now: datetime, since: datetime,
               matcher: SymbolMatcher, forced_symbol: str | None = None,
               default_publisher: str = "") -> list[RawNews]:
    parsed = feedparser.parse(content)
    out: list[RawNews] = []
    for e in parsed.entries:
        title = clean_text(str(e.get("title", "")), 300)
        if not title:
            continue
        publisher = default_publisher
        src = e.get("source")
        if isinstance(src, dict) and src.get("title"):
            publisher = clean_text(str(src["title"]), 100)
        if publisher and title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        published = _published(e, now)
        if published is None or published < since:
            continue
        snippet = clean_text(str(e.get("summary", "")), 300)
        symbols = matcher.match(title)
        if forced_symbol and forced_symbol not in symbols:
            symbols = (*symbols, forced_symbol)
        if not symbols:
            continue
        out.append(RawNews(news_id(title), source, publisher or source, str(e.get("link", "")),
                           title, snippet if snippet != title else "", published, symbols,
                           credibility(publisher or source)))
    return out


async def fetch_all(instruments: list[Instrument], now: datetime, days: int,
                    client: httpx.AsyncClient) -> tuple[list[RawNews], list[str]]:
    """Fetch per-symbol Google News and general feeds. Returns (items, errors)."""
    since = now - timedelta(days=days)
    matcher = SymbolMatcher(instruments)
    items: dict[str, RawNews] = {}
    errors: list[str] = []
    headers = {"User-Agent": "algotrade-personal/0.1 (+personal research bot)"}

    async def grab(url: str) -> bytes | None:
        try:
            r = await client.get(url, headers=headers, follow_redirects=True, timeout=20)
            r.raise_for_status()
            return r.content
        except httpx.HTTPError as exc:
            errors.append(f"{url.split('/')[2]}: {type(exc).__name__}")
            return None

    for inst in instruments:
        body = await grab(google_news_url(inst, days))
        if body:
            for n in parse_feed(body, "google_news", now, since, matcher, inst.symbol):
                items.setdefault(n.id, n)
    for key, url in GENERAL_FEEDS.items():
        body = await grab(url)
        if body:
            for n in parse_feed(body, key, now, since, matcher,
                                default_publisher=FEED_PUBLISHER.get(key, key)):
                items.setdefault(n.id, n)
    return list(items.values()), errors
