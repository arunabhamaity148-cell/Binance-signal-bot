# News engine (as built)

`app/news/` provides configured source construction and collection, parsing, deduplication, credibility lookup, related-item grouping/corroboration, impact assessment, and category-based decay. `NewsEngine.ingest_batch` accumulates deduplicated items; `build_news_state` returns active events and unhealthy categories. A collection cycle records source health and isolates source-unavailable failures.

Fetching is scheduled by a caller; the engine module says this orchestration belongs to a main/bot module that is not present in this snapshot. Source URLs, access terms, and availability must be verified from the actual YAML and with providers. No source URL is invented here. Category half-lives and credibility/impact behavior are not empirically calibrated.
