---
name: wiki-memory
description: "Semantic conversation memory: sessions to knowledge."
version: 1.0.0
author: jonotonfoto
license: MIT
metadata:
  hermes:
    tags: [wiki, memory, knowledge-base, embeddings, semantic-search]
    related_skills: [llm-wiki, obsidian]
---

# Wiki Memory — Semantic Conversation Memory for Hermes Agent

Turns Hermes Agent conversations into a durable, searchable knowledge base.
Each session is extracted (NVIDIA LLM), validated, merged into markdown pages
with semantic embeddings (SQLite + numpy), and made searchable through a
two-tier retrieval (embeddings + keywords). A facts bridge optionally feeds
extracted facts into holographic memory. Cross-platform: Windows desktop, Linux
server, container.

## When This Skill Activates

Use when the user:
- Wants to set up, run, or repair Wiki Memory on their Hermes
- Asks how to index their conversation history into a searchable knowledge base
- Wants automatic recall of past conversations in new chats
- Needs to understand or extend the indexing / search / do-index pipeline

**Don't use for:** plain Obsidian vault management (use `obsidian`), or basic
markdown wiki editing (use `llm-wiki`).

## Prerequisites

- Python 3.10+ with `numpy` and `requests`
- An NVIDIA API key (`NVIDIA_API_KEY`) — free tier at build.nvidia.com
- Hermes Agent (sessions DB + plugin system)

## How to Run

```bash
# Install from source (or add as a tap: hermes skills tap add jonotonfoto/wiki-memory)
pip install -e .

# Configure
cp .env.example .env   # set NVIDIA_API_KEY

# Index finished/changed sessions (catch-up, loops)
python scripts/run_sweep.py

# Search
python -m wiki_v2.search "some topic from past conversations"

# Run tests
python -m pytest src/wiki_v2/tests/ -q   # expect 55 passed
```

## Plugins

Two Hermes plugins extend the agent:

| Plugin | Hook | Purpose |
|--------|------|---------|
| `wiki-context` | `pre_llm_call` | Auto-search wiki on every message; inject relevant pages |
| `wiki-session-finalize` | `on_session_finalize` | Immediately index a closed session on `/new` |

```bash
# copy plugins/* into <HERMES_PLUGINS>, then:
hermes plugins enable wiki-context
hermes plugins enable wiki-session-finalize
```

Plugins take effect on the **next session** (Hermes caches the pipeline).

## Do-Index Subsystem

Hermes sessions are long-lived and not auto-closed. Wiki Memory uses its own
"finished" metric (idle > `WIKI_IDLE_MINUTES`, default 32 min) and two layers:

1. **Cron sweep** (`run_sweep.py`, every 3 h) — the safety net. Indexes
   finished, *changed* sessions (via `content_hash`).
2. **Plugin `wiki-session-finalize`** — the accelerator. Re-indexes a
   just-closed session immediately.

A file lock (`index_lock.py`) prevents concurrent indexing. `content_hash` is
written **after** a successful card+embedding, so an interrupted index is
retried idempotently.

## Procedure

1. **Configure** — copy `.env.example` → `.env`, set `NVIDIA_API_KEY`.
2. **Initial index** — run `python scripts/run_sweep.py` (processes 5 sessions
   per pass, loops until done).
3. **Install plugins** — copy `plugins/*` into your Hermes plugins dir, enable
   with `hermes plugins enable`.
4. **Schedule cron** — run `run_sweep.py` every 3 h (see INSTALL.md for
   Windows / Linux / Docker specifics).
5. **Verify** — `python -m pytest src/wiki_v2/tests/ -q` (55 tests) and a manual
   search.

## Architecture (quick)

```
sessions (state.db) -> indexer.py -> extract (NVIDIA) -> validate -> merge/new page
  -> .index_v2.db (pages + 1024-dim embeddings BLOB + sessions)
  -> search.py -> embed query -> cosine top-K + keyword -> LLM synthesis
```

Paths are resolved per-OS from env via `src/wiki_v2/config.py` — never hardcoded.

## Pitfalls

1. **Stale lock blocks indexing.** A killed process can leave
   `<WIKI_PATH>/.index.lock`. It auto-reclaims after `WIKI_LOCK_MAX_AGE` (900s),
   or delete it manually. Symptom: `[LOCK] another process indexing`.
2. **Indexing "hangs" is normal.** NVIDIA is slow (~2 min per 5 sessions). Let it run.
3. **Plugins activate next session**, not current.
4. **Facts bridge is optional.** Run `scripts/run_facts_bridge.py` (or it runs
   after the sweep) to push wiki facts into holographic memory. If holographic
   memory is absent, the bridge is skipped harmlessly.
5. **One embedding dimension.** All vectors must be 1024-dim; mixing dims
   silently breaks cosine search.

## Verification

- [ ] `python -m pytest src/wiki_v2/tests/ -q` → 55 passed
- [ ] `python scripts/run_sweep.py` creates pages in `wiki/entities/*.md`
- [ ] `python -m wiki_v2.search "<topic>"` returns results
- [ ] Plugins show `enabled` in `hermes plugins list`
