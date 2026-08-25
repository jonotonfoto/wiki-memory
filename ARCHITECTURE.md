# Architecture

## Layers

```
config.py  ── env-driven paths & backends (no internal deps)
   │
index_db.py (SQLite: pages/chunks/sessions/entities/links/edges)
   │
embed.py ──► OpenAI-compatible endpoint (llama.cpp / LM Studio / NVIDIA)
   │
chunker → extract → quality → pages → indexer     (pipeline)
   │
search (multivector + RRF) ← gateway ← plugins/dashboard/cron   (consumers)
```

Dependency direction is strictly forward: consumers depend on `gateway`, which
depends on `search`/`index_db`; nothing imports `nvidia_client` directly except
through `gateway`.

## Storage

Single SQLite file `.index_v2.db` with six tables (`pages`, `embeddings`,
`sessions`, `entities`, `links`, `edges`). Vectors are float32 BLOBs
(1024-dim). Exact cosine top-K in numpy — deliberately no vector database at
desktop scale. A clean start means wiping **all six** tables; leftover `pages`
rows point at deleted files and crash the first re-index.

A clean start also requires wiping queue files and disabling old cron/plugins;
sessions themselves stay visible because they are read from Hermes `state.db`.

## Multi-vector search

Each item carries several embeddings (title / summary / tag / chunk channels).
Query time: embed once, cosine top-K per channel, plus a BM25 keyword channel,
fused with Reciprocal Rank Fusion. A cheap fail-open relevance gate filters
junk queries before embedding. No LLM in the hot path.

## Indexing pipeline

`sessions → chunker (block packing, spans) → extract (chat LLM, temp retry
0.3→0.1) → quality gate (fallback on invalid output) → page merge/create →
multivector + per-chunk embeddings → content_hash commit`. Interrupted runs
retry idempotently; the file lock prevents concurrent indexers; stop requests
finish the current session before exiting (never kill mid-page).

## Auto-indexing

Two layers: plugin `wiki-session-finalize` (immediate, hook-based) and a cron
sweep (safety net, hash-change detection). Both inject `WIKI_EMBED_BACKEND`
into the child process environment.

## Observability

Raw events go to JSONL; a small time-series DB feeds the dashboard
(`dashboard.py`, port 9120): extraction status/errors, indexing progress,
server load, cache hits/misses. Metrics that cannot be observed are not shown.
