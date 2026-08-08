# Architecture

## Overview

Wiki Memory turns Hermes Agent conversation sessions into a durable semantic
knowledge base. It is pure Python + SQLite + numpy — no external vector DB.

## Data flow

```
sessions (state.db)
      │
      ▼
indexer.py ──► extract (NVIDIA chat) ──► quality check ──► garbage? retry / fallback
      │                                          │
      │                                          ▼
      │                              topic matcher ──► existing page? MERGE
      │                                          │ no
      │                                          ▼
      │                                  new page (unique slug)
      ▼
.index_v2.db (SQLite)
  pages(slug, title, section, path, content_hash, summary, quality, created, updated)
  embeddings(slug, vector BLOB)          # 1024-dim float32
  sessions(session_id, indexed_at, page_slug, content_hash)
      │
      ▼
search.py ──► embed query ──► cosine top-K (numpy) + keyword search
      └──► LLM synthesis (only on hit)
```

## The do-index subsystem

Hermes sessions are long-lived and not auto-closed. This project uses its own
"finished" metric (idle > `WIKI_IDLE_MINUTES`, default 32) and two indexing
layers:

```
                  own "finished" metric (idle > 32 min)
                                  │
            ┌─────────────────────┴─────────────────────┐
            ▼                                           ▼
┌────────────────────────┐                  ┌───────────────────────────┐
│ Cron sweep (every 3h)   │                  │ Plugin wiki-session-       │
│ run_sweep.py →          │                  │ finalize (on_session_      │
│ indexer.main() loop     │                  │ finalize) →                │
│ = the safety net        │                  │ indexer --session <id>     │
└───────────┬────────────┘                  └───────────┬───────────────┘
            │  (both)                                    │  (accelerator)
            ▼                                            ▼
┌────────────────────────────────────────────────────────────────┐
│ indexer.main() — 3 filters + lock                               │
│  1. finished?  (session_status.is_session_finished, 32 min)     │
│  2. changed?   (content_hash vs current session text)           │
│  3. under file lock (index_lock, vs concurrent runs)            │
│  hash written AFTER successful card + embedding                 │
└────────────────────────────────────────────────────────────────┘
```

### Key modules

| Module | Responsibility |
|--------|----------------|
| `config.py` | Resolve all paths per-OS from env; load `.env`; wire `sys.path`. |
| `nvidia_client.py` | NVIDIA chat (extraction) + embeddings. Key from env. |
| `quality.py` | Garbage detection ("spaced letters", too-short text). |
| `extract.py` | Structured extraction with retry + fallback. |
| `index_db.py` | SQLite store: pages, embeddings (BLOB), sessions + `content_hash`. |
| `indexer.py` | Main pipeline: finished+changed filtering, lock, indexing. |
| `session_status.py` | `is_session_finished()` — idle-based detector. |
| `index_lock.py` | Cross-platform file lock (msvcrt/fcntl), stale reclaim. |
| `search.py` | Two-tier retrieval (embeddings + keywords) + LLM synthesis. |
| `cleanup_duplicates.py` | Group pages by slug root; delete true duplicates (same source); skip `untitled`. |
| `pages.py` | Render/parse/merge markdown pages; Jaccard topic matching. |
| `slug.py` | Unique slug generation. |
| `facts_bridge.py` | Queue facts/decisions to `.facts_pending.jsonl`. |

## Storage

SQLite `.index_v2.db`, 3 tables. Embeddings are 1024-dim float32 stored as
BLOBs (~4 KB/page). At <1000 pages, numpy cosine over all vectors is
milliseconds — no FAISS needed. **All vectors must share one dimension**
(1024); mixing a different dim silently breaks cosine search.

## Search

Two tiers, merged and re-ranked:
1. **Semantic:** embed the query, cosine top-K against all page vectors
   (`MIN_SEMANTIC_SCORE=0.40`).
2. **Keywords by roots:** words compared by first-5-letter prefix
   («сознания» ≈ «сознание»), so inflected forms match. Keyword score capped
   at `MAX_KEYWORD_SCORE` (0.35) so semantic always wins ties.
LLM synthesis runs only when there is at least one hit.

### Retrieval quality guards (2026-08-08)

- **Triangulation (`wiki-context`)**: a weak Russian embedder returns garbage in
  the 0.40–0.60 gray zone. The plugin requires a page's `## Темы` section to
  share ≥ 2 roots with the query, unless the semantic score is confidently high
  (≥ `high_confidence`, 0.60).
- **LRU cache**: `cache.json` answers similar questions instantly (limit 100,
  TTL 7 days), cutting repeated embed-API calls.
- **Duplicate cleanup**: `cleanup_duplicates.py` (dry-run by default).
- **Extractor synonyms**: `extract.py` asks for related concepts in `key_topics`.

## Plugins

- **`wiki-context`** — `pre_llm_call` hook: on each message ≥15 chars, search the
  wiki and return a `<wiki-memory>` context block, or `None`. Tunables live in
  `config.json` (re-read on every request — no restart).
- **`wiki-session-finalize`** — `on_session_finalize` hook: on `/new`/`/reset`/
  expiry, spawn the indexer for that session in the background.

Both are fail-open and path-resolved via `config`.

## Reliability

- All state on disk (SQLite + `.md`), so a crash never loses committed data.
- `content_hash` written after success → interrupted index is retried.
- File lock with stale-reclaim → cron/plugin can't collide.
- Plugins are fail-open → they never break the agent.

## Technology choices

| Choice | Why |
|--------|-----|
| SQLite + numpy (no FAISS) | Sufficient <1000 pages; zero heavy deps |
| NVIDIA for LLM + embeddings | One API, one key; free tier |
| BLOB for vectors | Atomic, simple, fast deserialize (`np.frombuffer`) |
| Jaccard 0.20 on roots for merge | Lowered from 0.34: extractor adds synonyms, shrinking exact topic overlap |
