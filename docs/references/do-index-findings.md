# Do-Index Findings — nuances discovered during implementation

These are the non-obvious lessons from building the do-index subsystem. They
are documented here so future work does not repeat the exploration.

## 1. The correct end-of-session hook is `on_session_finalize`, not `on_session_reset`

Hermes lists `on_session_reset` in `VALID_HOOKS`, but it is **not emitted** for
plugins in the desktop/gateway path. The hook that actually fires on `/new`,
`/reset`, and session expiry — and that carries `session_id` — is
**`on_session_finalize`** (verified in `tui_gateway/server.py`).
`on_session_end`, by contrast, fires on **every turn** and carries no content —
useless for finalize-on-close.

**Action:** the `wiki-session-finalize` plugin registers `on_session_finalize`.

## 2. A killed process leaves a stale lock on Windows

`IndexLock` uses `os.open(..., O_EXCL)`. If a process is killed (cron timeout,
SIGKILL), the `.index.lock` file remains and blocks later runs until `max_age`
passes. Symptom: `[LOCK] another process indexing` with no live indexer.

**Fix:** default `max_age = 900s` (env `WIKI_LOCK_MAX_AGE`) — **less than the
3 h cron interval**, so a stale lock never blocks the next sweep. If a fresh
lock from a just-killed process is stuck, delete it manually.

## 3. Cron `no_agent=true` accepts only a relative script name from the Hermes scripts dir

Hermes cron rejects absolute/project paths: "Script path must be relative to
~/.hermes/scripts/". To avoid duplicating code, put a thin loader in the Hermes
scripts dir that adds the project to `sys.path`, `os.chdir()`s into it, and
`runpy.run_path()`s the real `run_sweep.py`.

## 4. Indexing "hangs" is normal (NVIDIA latency)

5 sessions ≈ 2 minutes (each = chat + embed call). For a large backlog this
looks like a hang, but it is real progress. `run_sweep.py` loops 5-at-a-time.

## 5. Migration: first sweep re-indexes everything once

Adding `content_hash` via `ALTER TABLE` leaves existing sessions with an empty
hash (`''`). The first sweep therefore treats all of them as "changed" and
re-indexes once (idempotent MERGE, no duplicates), then hashes are set.

## 6. "Everything already indexed" — the only leftover is the active session

`get_unindexed_sessions` returning ~1 is expected: that's the current active
conversation. Counters that differ (sessions in state.db vs sessions table) are
explained by sessions with no user/assistant messages (service-only), which are
not candidates.
