# AGENTS.md — Wiki Memory project map

**Context:** A cross-platform semantic memory system for Hermes Agent.
Conversations → markdown knowledge base + embeddings → search. Runs on Windows
desktop, Linux server, and in a container. Paths are env-driven, never hardcoded.

---

## Architecture (read `ARCHITECTURE.md`)

- `src/wiki_v2/` — core package (extraction, indexing, search, storage).
- `scripts/` — entry points (`run_indexer`, `run_sweep`, facts bridge).
- `plugins/` — Hermes plugins (`wiki-context`, `wiki-session-finalize`).
- `config.py` — the single place that resolves paths per-OS from env.

**Invariants (HARD):**
1. No hardcoded paths or secrets in `src/` — always via `config` / env / `.env`.
2. `content_hash` is written to `sessions` only **after** a successful
   card+embedding (an interrupted index is retried, never "stuck as done").
3. Only **finished** (idle > `WIKI_IDLE_MINUTES`) and **changed** (by hash)
   sessions are indexed by the background sweep. Active sessions are never touched.
4. A file lock guards all indexing (cron vs plugin). Stale locks auto-reclaim
   after `WIKI_LOCK_MAX_AGE` (default 900s).
5. Plugins are fail-open: any error is logged and swallowed — never crash the agent.

---

## Workflow

- **Entry points:** `scripts/run_sweep.py` (cron), `scripts/run_indexer.py`,
  `python -m wiki_v2.search "<query>"`.
- **Tests:** `python -m pytest src/wiki_v2/tests/ -q` (55 tests).
- **Config template:** `.env.example`. Never commit `.env` or DB files.

---

## File map

| Path | What |
|------|------|
| `docs/index.md` | Docs table of contents |
| `docs/design-docs/core-beliefs.md` | Golden principles (taste invariants) |
| `ARCHITECTURE.md` | System design & data flow |
| `INSTALL.md` | Setup for Windows / Linux / Docker |
| `SECURITY.md` | Secrets & boundary rules |
| `QUALITY_SCORE.md` | Doc legibility grading |

---

## Don'ts

- Don't commit `.env`, `*.db`, `*.jsonl`, `__pycache__`.
- Don't hardcode `/opt/data`, `C:\Users\...`, or any username / IP / token.
- Don't call the NVIDIA key anything but `NVIDIA_API_KEY` (env / `.env`).
- Don't edit `config.py` paths by hand — set env vars instead.
- Don't run the sweep while another index is running (the lock handles this,
  but respect `[LOCK]` messages).
