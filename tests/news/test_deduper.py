from __future__ import annotations

from app.core.models import NewsCategory, NewsDirection
from app.news.deduper import DedupStore, dedupe_batch
from app.news.parser import ParsedNewsItem


def _item(url="https://a.com/1", content_hash="hash1", source="coindesk", tier=2):
    return ParsedNewsItem(
        source_name=source, tier=tier, canonical_url=url, domain="a.com",
        published_ts_ms=1000, fetched_ts_ms=1000, receipt_ts_ms=1000,
        category=NewsCategory.OTHER, direction=NewsDirection.UNKNOWN,
        entities=("BTCUSDT",), content_hash=content_hash, raw_text="text",
    )


def test_dedup_store_not_duplicate_initially():
    store = DedupStore()
    assert not store.is_duplicate(_item())


def test_dedup_store_duplicate_after_record():
    store = DedupStore()
    item = _item()
    store.record(item)
    assert store.is_duplicate(item)


def test_dedup_store_duplicate_by_url_different_hash():
    store = DedupStore()
    store.record(_item(url="https://a.com/1", content_hash="h1"))
    assert store.is_duplicate(_item(url="https://a.com/1", content_hash="h2"))


def test_dedup_store_duplicate_by_hash_different_url():
    store = DedupStore()
    store.record(_item(url="https://a.com/1", content_hash="h1"))
    assert store.is_duplicate(_item(url="https://b.com/2", content_hash="h1"))


def test_dedup_store_distinct_items_not_duplicate():
    store = DedupStore()
    store.record(_item(url="https://a.com/1", content_hash="h1"))
    assert not store.is_duplicate(_item(url="https://b.com/2", content_hash="h2"))


def test_dedupe_batch_removes_duplicates_within_batch():
    store = DedupStore()
    items = [
        _item(url="https://a.com/1", content_hash="h1"),
        _item(url="https://a.com/1", content_hash="h1"),  # exact duplicate
    ]
    result = dedupe_batch(items, store)
    assert len(result) == 1


def test_dedupe_batch_keeps_first_occurrence():
    store = DedupStore()
    first = _item(url="https://a.com/1", content_hash="h1", source="coindesk")
    second = _item(url="https://a.com/1", content_hash="h1", source="the_block")
    result = dedupe_batch([first, second], store)
    assert len(result) == 1
    assert result[0].source_name == "coindesk"


def test_dedupe_batch_against_prior_store_state():
    store = DedupStore()
    store.record(_item(url="https://a.com/1", content_hash="h1"))
    new_batch = [_item(url="https://a.com/1", content_hash="h1")]
    result = dedupe_batch(new_batch, store)
    assert result == []


def test_dedupe_batch_preserves_order_of_survivors():
    store = DedupStore()
    items = [
        _item(url="https://a.com/1", content_hash="h1"),
        _item(url="https://a.com/2", content_hash="h2"),
        _item(url="https://a.com/3", content_hash="h3"),
    ]
    result = dedupe_batch(items, store)
    assert [i.canonical_url for i in result] == [
        "https://a.com/1", "https://a.com/2", "https://a.com/3"
    ]


def test_dedupe_batch_records_survivors_into_store():
    store = DedupStore()
    items = [_item(url="https://a.com/1", content_hash="h1")]
    dedupe_batch(items, store)
    assert store.is_duplicate(_item(url="https://a.com/1", content_hash="different"))
