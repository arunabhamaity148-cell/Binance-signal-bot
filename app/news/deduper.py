"""Deduplication by canonical URL + content hash.

Per spec section 14: "deduplication by canonical URL + content hash".
Two ParsedNewsItems are duplicates if EITHER their canonical_url
matches OR their content_hash matches (a republish under a different
URL with identical text is still the same story; a URL that gets
re-crawled with a tracking-param variant is caught by URL
canonicalization in parser.py before it ever reaches here).

Deduplication is stateful across polling cycles (a story seen 10
minutes ago must still be recognized as a duplicate now), so this
module exposes a small in-memory store rather than a pure function —
the store's lifetime is owned by the news engine (engine.py), not by
this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.news.parser import ParsedNewsItem


@dataclass
class DedupStore:
    """Tracks seen canonical URLs and content hashes. Entries are never
    actively expired here (the news engine's decay logic in decay.py
    handles when an EVENT stops being active; this store only prevents
    re-ingesting the same story as a second, distinct NewsEvent).
    """

    seen_urls: set[str] = field(default_factory=set)
    seen_hashes: set[str] = field(default_factory=set)

    def is_duplicate(self, item: ParsedNewsItem) -> bool:
        return item.canonical_url in self.seen_urls or item.content_hash in self.seen_hashes

    def record(self, item: ParsedNewsItem) -> None:
        self.seen_urls.add(item.canonical_url)
        self.seen_hashes.add(item.content_hash)


def dedupe_batch(items: list[ParsedNewsItem], store: DedupStore) -> list[ParsedNewsItem]:
    """Filter `items` down to only genuinely new items (not already in
    `store`, and not duplicates of each other within this same batch),
    recording each surviving item into `store` as it's accepted.

    Order is preserved; the FIRST occurrence of a duplicate pair within
    a batch is kept (consistent with "keep first-seen" used elsewhere
    in this codebase, e.g. normalize_kline_series's duplicate-bar
    handling).
    """
    out: list[ParsedNewsItem] = []
    for item in items:
        if store.is_duplicate(item):
            continue
        store.record(item)
        out.append(item)
    return out
