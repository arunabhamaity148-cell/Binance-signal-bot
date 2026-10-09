# News Engine

The news engine collects configured RSS/JSON sources, follows redirects, normalizes items, deduplicates them, applies credibility/impact/decay, and maintains source health. Sources with `enabled: false` are skipped and produce an informational `news_source_disabled` event rather than an outage warning. Five consecutive failures disable a source automatically.

News is enrichment only. The retired G7 news veto is not part of the production pipeline.
