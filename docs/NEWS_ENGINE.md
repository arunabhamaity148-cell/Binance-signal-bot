# News engine (as built)

The subsystem loads source definitions and category half-lives from `config/news_sources.yaml`. `NewsCollector` fetches RSS or JSON over HTTPS using TLS verification, per-source timeouts, retry/backoff with jitter, and HTTP 429/Retry-After handling. Items are parsed, deduplicated, categorized/correlated, assigned credibility and impact, and decayed by category before `NewsEngine.build_news_state` produces input for evaluation and the news-shock veto.

## Runtime scheduling

After market-data readiness, `SignalBot.start_news_collectors()` starts the background news task. It traverses configured sources through `run_collection_cycle`, records per-source health, logs source failures, and waits approximately 60 seconds before the next cycle. Collection traverses sources serially; timeouts/retries can extend a cycle.

Health degradation is category-aware: when relevant known sources for a category are unhealthy, that category is excluded/marked unhealthy rather than freezing every other category. This does not prove a source is available or content is complete.

## Important: source URLs are literal placeholders

The checked-in entries in `config/news_sources.yaml` still contain strings such as `"<official Fed RSS URL>"`, `"<CoinDesk RSS URL>"`, and `"<GDELT public API endpoint>"`. These are placeholders, not verified endpoints. **Do not assume the source registry can fetch real news as shipped.** An operator must replace/verify URLs and confirm access terms, formats, and free-tier/rate limits before relying on news. This document intentionally does not invent replacement URLs.

News collection, parsing, source-health tracking, and degradation logic have offline tests. Live source availability, terms, coverage, news-to-market mapping quality, and real-world degradation behavior have not been verified. No live news-derived event performance is claimed.
