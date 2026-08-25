# Wiki Memory v3 — agent map

> Map, not manual. Start here, follow links. Keep under 100 lines.

## What this is

Semantic memory for Hermes Agent: conversations → compressed markdown pages +
multi-vector embeddings in SQLite → meaning-based search injected back into
agent context. Runs on Windows desktop and Linux VPS (Docker) from one
codebase; paths are env-driven, never hardcoded.

## Where to look

| Need | File |
|---|---|
| Plain-words overview + pipeline diagram | `README.md` |
| System design, data flow, channels | `ARCHITECTURE.md` |
| Install (desktop / VPS / Docker) | `INSTALL.md` |
| Why it is built this way | `DECISIONS.md` |
| Secrets & boundaries | `SECURITY.md` |
| Core package (search/index/extract/embed/dashboard) | `src/wiki_v2/` |
| Entry points (sweep loaders, dashboard server, watchdog) | `scripts/` |
| Hermes plugins (context injection, auto-index) | `plugins/` |
| Deployment profiles (env templates) | `profiles/` |

## Invariants

1. **Cross-platform or it does not merge.** No absolute personal paths in code;
   everything resolves via `config.py` from env (`WIKI_PATH`,
   `WIKI_EMBED_BACKEND`, ...).
2. **Search stays multi-vector.** title/summary/tag/chunk channels + RRF fusion;
   do not collapse to a single vector.
3. **Fail-open everywhere.** A broken hook/embedder degrades service, never
   crashes the agent: catch + log + skip.
4. **Embeddings are model-bound.** Vectors from different models are not
   comparable; switching embed models = full re-index.
5. **Tests are invariants, not snapshots** — run `pytest src/wiki_v2/tests/`.

## Terminology (strict)

- **session** = raw transcript · **page** = compressed `.md` knowledge page ·
  **chunk** = text slice carrying its own embedding.

## Don'ts

- Do not copy files from a running installation wholesale into this repo —
  port algorithmic deltas only (older installs may contain hardcoded paths).
- Do not commit secrets: API keys live in the host environment, never in git.
- Do not add an LLM call to the search hot path (relevance gate is cheap and
  fail-open by design).
