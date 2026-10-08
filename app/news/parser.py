"""News parsing: entity extraction, category classification, direction
classification, and timestamp normalization.

Per spec section 14's mandatory component list: "entity extraction",
"category classification", "direction classification", and
"timestamp normalization (published_ts, fetched_ts, receipt_ts)".

All classification here is conservative. When a category or direction
cannot be confidently determined from the parsed text, the conservative
default is used (NewsCategory.OTHER, NewsDirection.UNKNOWN) — nothing
is guessed, per the fail-closed principle applied to news
interpretation, not just to market data.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from app.core.models import NewsCategory, NewsDirection

# The 20-pair universe's base assets, used for entity extraction.
# Matches STRATEGIES_SPEC.md / config/top20_pairs.yaml's symbol list
# (base asset only, since news text refers to "Bitcoin"/"BTC", not the
# USDT-margined futures ticker).
_TRACKED_BASE_ASSETS: dict[str, str] = {
    "BTC": "BTCUSDT", "BITCOIN": "BTCUSDT",
    "ETH": "ETHUSDT", "ETHEREUM": "ETHUSDT",
    "SOL": "SOLUSDT", "SOLANA": "SOLUSDT",
    "BNB": "BNBUSDT",
    "XRP": "XRPUSDT", "RIPPLE": "XRPUSDT",
    "DOGE": "DOGEUSDT", "DOGECOIN": "DOGEUSDT",
    "ADA": "ADAUSDT", "CARDANO": "ADAUSDT",
    "AVAX": "AVAXUSDT", "AVALANCHE": "AVAXUSDT",
    "LINK": "LINKUSDT", "CHAINLINK": "LINKUSDT",
    "BCH": "BCHUSDT",
    "LTC": "LTCUSDT", "LITECOIN": "LTCUSDT",
    "DOT": "DOTUSDT", "POLKADOT": "DOTUSDT",
    "TRX": "TRXUSDT", "TRON": "TRXUSDT",
    "SUI": "SUIUSDT",
    "APT": "APTUSDT", "APTOS": "APTUSDT",
    "ARB": "ARBUSDT", "ARBITRUM": "ARBUSDT",
    "OP": "OPUSDT", "OPTIMISM": "OPUSDT",
    "ZEC": "ZECUSDT",
    "NEAR": "NEARUSDT",
    "ATOM": "ATOMUSDT", "COSMOS": "ATOMUSDT",
}

_MACRO_TERMS = ("FED", "FEDERAL RESERVE", "FOMC", "CPI", "INTEREST RATE", "INFLATION", "TREASURY")

_MARKET_WIDE_HINT_TERMS = _MACRO_TERMS + ("SEC", "CFTC", "CRYPTO MARKET", "ALL EXCHANGES")

_CATEGORY_KEYWORDS: dict[NewsCategory, tuple[str, ...]] = {
    NewsCategory.REGULATORY: ("SEC", "CFTC", "LAWSUIT", "ENFORCEMENT", "REGULATOR", "COMPLIANCE", "SANCTION", "OFAC"),
    NewsCategory.MACRO: _MACRO_TERMS,
    NewsCategory.HACK: ("HACK", "EXPLOIT", "BREACH", "STOLEN", "VULNERABILITY", "DRAINED"),
    NewsCategory.LISTING: ("LISTS", "LISTING", "WILL LIST", "NOW AVAILABLE"),
    NewsCategory.DELISTING: ("DELIST", "DELISTING", "REMOVES", "SUSPENDS TRADING"),
    NewsCategory.ETF: ("ETF", "SPOT ETF", "FUTURES ETF"),
    NewsCategory.LIQUIDATION: ("LIQUIDATION", "LIQUIDATED", "CASCADE", "LONG SQUEEZE", "SHORT SQUEEZE"),
    NewsCategory.OUTAGE: ("OUTAGE", "DOWNTIME", "MAINTENANCE", "SERVICE DISRUPTION", "DOWN FOR"),
}

_BULLISH_TERMS = ("SURGE", "RALLY", "APPROVED", "PARTNERSHIP", "ADOPTION", "BREAKOUT", "INFLOW", "UPGRADE")
_BEARISH_TERMS = ("CRASH", "PLUNGE", "BAN", "REJECTED", "HACK", "EXPLOIT", "OUTFLOW", "DELIST", "LAWSUIT", "SANCTION")


@dataclass(frozen=True)
class RawFeedItem:
    """A single, minimally-parsed item as read from a feed (RSS/JSON),
    before classification. This is what collectors.py produces and
    parser.py consumes."""

    source_name: str
    tier: int
    title: str
    summary: str
    url: str
    published_ts_ms: int
    fetched_ts_ms: int


@dataclass(frozen=True)
class ParsedNewsItem:
    """A RawFeedItem after entity/category/direction classification and
    canonicalization, ready for credibility/impact/corroboration
    scoring."""

    source_name: str
    tier: int
    canonical_url: str
    domain: str
    published_ts_ms: int
    fetched_ts_ms: int
    receipt_ts_ms: int
    category: NewsCategory
    direction: NewsDirection
    entities: tuple[str, ...]
    content_hash: str
    raw_text: str


def canonicalize_url(url: str) -> str:
    """Normalize a URL for deduplication: strip common tracking query
    parameters, lowercase scheme/host, drop fragment, strip trailing
    slash. Two URLs that differ only in tracking params or fragment
    canonicalize to the same value.
    """
    _TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "ref", "source",
    }
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    query_pairs.sort()
    query = urlencode(query_pairs)
    return urlunsplit((scheme, netloc, path, query, ""))


def extract_domain(url: str) -> str:
    return urlsplit(url).netloc.lower()


def compute_content_hash(title: str, summary: str) -> str:
    """SHA-256 of normalized title+body, per spec section 14's
    deduplication requirement (canonical URL + content hash)."""
    normalized = re.sub(r"\s+", " ", f"{title.strip()} {summary.strip()}").strip().upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_entities(text: str) -> tuple[str, ...]:
    """Extract tracked-symbol entities and/or MARKET from free text.

    Matching is whole-word, case-insensitive, against both the ticker
    (BTC) and common name (Bitcoin) forms. Ambiguous or unmatched text
    yields an empty entity set — callers must not assume a non-empty
    result; a news item with no matched entity is simply not a
    candidate for asset-specific severity (it may still be market-wide
    if it matches a market-wide hint term, checked separately).
    """
    upper = text.upper()
    found_symbols: set[str] = set()
    for term, symbol in _TRACKED_BASE_ASSETS.items():
        if re.search(rf"\b{re.escape(term)}\b", upper):
            found_symbols.add(symbol)

    is_market_wide = any(re.search(rf"\b{re.escape(term)}\b", upper) for term in _MARKET_WIDE_HINT_TERMS)

    entities: list[str] = sorted(found_symbols)
    if is_market_wide:
        entities.append("MARKET")
    return tuple(entities)


def classify_category(text: str) -> NewsCategory:
    """Keyword-based category classification. Falls back to OTHER when
    no category's keywords match — never guesses a specific category
    without textual support."""
    upper = text.upper()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(re.search(rf"\b{re.escape(kw)}\b", upper) for kw in keywords):
            return category
    return NewsCategory.OTHER


def classify_direction(text: str) -> NewsDirection:
    """Keyword-based direction classification. Ambiguous cases
    (matching both bullish and bearish terms, or matching neither)
    conservatively return UNKNOWN rather than guessing — never NEUTRAL
    as a default, since NEUTRAL is a positive claim ("this has no
    directional implication"), while UNKNOWN is an honest admission of
    not knowing. A sentence like 'X approved despite lawsuit' matching
    both 'approved' and 'lawsuit' should not be forced into a single
    direction.
    """
    upper = text.upper()
    is_bullish = any(re.search(rf"\b{re.escape(t)}\b", upper) for t in _BULLISH_TERMS)
    is_bearish = any(re.search(rf"\b{re.escape(t)}\b", upper) for t in _BEARISH_TERMS)
    if is_bullish and not is_bearish:
        return NewsDirection.BULLISH
    if is_bearish and not is_bullish:
        return NewsDirection.BEARISH
    return NewsDirection.UNKNOWN


def parse_raw_item(item: RawFeedItem, *, receipt_ts_ms: int) -> ParsedNewsItem:
    """Full parse of one RawFeedItem into a ParsedNewsItem: URL
    canonicalization, domain extraction, content hashing, entity
    extraction, and category/direction classification.
    """
    combined_text = f"{item.title} {item.summary}"
    canonical_url = canonicalize_url(item.url)
    domain = extract_domain(canonical_url)
    content_hash = compute_content_hash(item.title, item.summary)
    entities = extract_entities(combined_text)
    category = classify_category(combined_text)
    direction = classify_direction(combined_text)

    return ParsedNewsItem(
        source_name=item.source_name,
        tier=item.tier,
        canonical_url=canonical_url,
        domain=domain,
        published_ts_ms=item.published_ts_ms,
        fetched_ts_ms=item.fetched_ts_ms,
        receipt_ts_ms=receipt_ts_ms,
        category=category,
        direction=direction,
        entities=entities,
        content_hash=content_hash,
        raw_text=combined_text,
    )
