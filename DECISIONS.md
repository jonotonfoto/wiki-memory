# Decisions & rationale

| # | Decision | Why |
|---|---|---|
| 1 | Store vectors in SQLite, exact numpy cosine | Desktop scale (dozens–hundreds of pages); no extra service to run |
| 2 | Keep multi-vector retrieval (title/summary/tag/chunk + RRF) | Single-vector loses recall on short titles/tags; channels capture different intents |
| 3 | Cheap fail-open relevance gate, no LLM in hot path | Junk-query filtering must add ~0 latency and never break search |
| 4 | Env-inject `WIKI_EMBED_BACKEND` in launching code | Backends switch without editing `.env` or agent config |
| 5 | One codebase, deployment profiles | Desktop/VPS differ in paths/backends only; code branches would drift apart |
| 6 | Atomic lease guard for watchdog (`O_CREAT\|O_EXCL`) | `msvcrt.locking`/`flock` are not atomic across interpreters on Windows |
| 7 | Graceful stop between sessions | Killing mid-page corrupts pages; stop-flag checked before each session |
| 8 | Clean start over migration when changing embed model | Vectors from different models are incomparable even at equal dim |
| 9 | Mirror folders for plugins stay separate | Session-side Python plugins and UI JS plugins have different lifecycles |
| 10 | Dashboard shows only observable metrics | Dead gauges (nonexistent caches) erode trust; removed in the dashboard audit |

## Promoted to hard rules by real bugs

- Wipe all six tables on re-index (leftover `pages` rows crashed first run).
- Cron inside Docker must use `-u <app user>` (root-owned files silently broke writes).
- After any patch, verify the symbol actually landed on disk (a patch once
  reported success without writing the file).
