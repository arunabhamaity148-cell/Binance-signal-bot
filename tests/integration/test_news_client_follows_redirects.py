from __future__ import annotations

import pytest

from app.news.collectors import NewsCollector, RetryConfig, SourceConfig, SourceFormat, build_source_configs
from app.config import load_all


@pytest.mark.asyncio
async def test_default_news_client_follows_redirects():
    collector = NewsCollector(RetryConfig(1, 1, 1, True))
    try:
        assert collector._client.follow_redirects is True
    finally:
        await collector.close()


def test_source_config_reads_enabled_and_poll_interval():
    sources = build_source_configs(load_all().news_sources)
    binance = next(source for source in sources if source.name == "binance_announcements")
    gdelt = next(source for source in sources if source.name == "gdelt")
    assert binance.enabled is False
    assert gdelt.poll_interval_s == 1800.0


def test_source_config_defaults_enabled_and_poll_interval():
    source = SourceConfig("x", "https://example.com", 1, SourceFormat.RSS, 5.0, 1.0)
    assert source.enabled is True
    assert source.poll_interval_s == 60.0
