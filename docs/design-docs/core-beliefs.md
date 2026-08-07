# Core Beliefs — Golden Principles

Mechanical rules for this project. SOFT rules are preferences; HARD rules are
never violated. Promote SOFT → HARD on the first real bug.

## 1. No hardcoded paths or secrets (HARD)
All paths resolve via `config.py` from env / `.env`; no user paths, IPs, or
tokens in source.
- **Why:** The system must run on Windows, Linux, and containers unchanged;
  hardcoded paths break portability and leak the operator's environment.
- **Enforcement:** `grep -rE '/opt/data|C:\\\\Users|nvidia-...' src/` should be clean.
- **Promote:** already HARD.

## 2. `content_hash` written after success (HARD)
Write a session's `content_hash` only after its card and embedding succeeded.
- **Why:** An interrupted index must be retried, not "stuck as done".
- **Enforcement:** unit test `test_indexer_smoke.py` (skip-unchanged / reindex-changed).
- **Promote:** already HARD.

## 3. Index only finished + changed sessions (HARD)
The background sweep indexes only sessions idle > `WIKI_IDLE_MINUTES` whose
content changed (by hash). Active sessions are never touched.
- **Why:** Don't build partial cards from mid-conversation state; avoid wasted API.
- **Enforcement:** `test_indexer_smoke.py::test_indexer_skips_active_session`.
- **Promote:** already HARD.

## 4. File lock guards indexing (HARD)
All indexing runs under `IndexLock`; concurrent runs are skipped, stale locks
reclaim after `WIKI_LOCK_MAX_AGE`.
- **Why:** Cron and the `/new` plugin must never collide on the same DB.
- **Enforcement:** `test_index_lock.py`.
- **Promote:** already HARD.

## 5. Plugins are fail-open (HARD)
Any plugin error is logged and swallowed — never crash the agent.
- **Why:** A memory plugin must not take down the conversation.
- **Enforcement:** plugin handlers wrapped in try/except; `test_on_finalize_never_raises`.
- **Promote:** already HARD.

## 6. Single embedding dimension (HARD)
All vectors must be 1024-dim float32; never mix dimensions.
- **Why:** Mixing dims silently breaks cosine search.
- **Enforcement:** manual DB check (`dims` map); document in ARCHITECTURE.
- **Promote:** already HARD.

## 7. Cross-platform config is the single source of truth (SOFT)
Prefer extending `config.py` over adding new hardcoded paths.
- **Promote to HARD when:** a second path gets hardcoded anywhere.
