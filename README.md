# Wiki Memory for Hermes Agent

Automatically turns Hermes Agent conversations into a durable, searchable
knowledge base. Each session is extracted (via an NVIDIA LLM), validated,
merged into markdown pages with semantic embeddings (SQLite + numpy), and made
searchable through a two-tier retrieval (embeddings + keywords) that the agent
can query on every turn. A facts bridge optionally feeds extracted facts into
holographic memory.

**Cross-platform:** the same package runs on Windows desktop, Linux server, and
inside a container — paths are resolved per-OS from environment variables, never
hardcoded.

> This repository is the cleaned-up, public-ready version of a working memory
> system that runs on both a Windows desktop Hermes and a Linux VPS Hermes bot.

---

## What it does

```
sessions (state.db)
     │
     ▼
indexer.py ──► extract (Nemotron) ──► validate ──► garbage? retry/fallback
     │                                          │
     │                                          ▼
     │                                 topic matcher ──► existing page? MERGE
     │                                          │ no
     │                                          ▼
     │                                 new page (unique slug)
     ▼
.index_v2.db (SQLite)
  - pages (slug, title, section, content_hash, embedding BLOB, ...)
  - embeddings (1024-dim float32 vectors)
  - sessions (which sessions indexed, + content_hash for change detection)
     │
     ▼
search.py ──► embed query ──► cosine top-K + keyword search
     └──► LLM synthesis (only on hit)
```

### Indexing lifecycle (the "do-index" system)

Hermes sessions are long-lived and not auto-closed. To index a session at the
right time, this project uses **its own notion of "finished"**:

- A session is **finished** when no new messages arrive for `WIKI_IDLE_MINUTES`
  (default **32 min**).
- Two layers keep the wiki up to date:
  1. **Cron sweep** (`run_sweep.py`, every 3 h) — the safety net. Indexes
     finished, *changed* sessions (via `content_hash` change detection).
  2. **Plugin `wiki-session-finalize`** — the accelerator. Hooks
     `on_session_finalize` (fires on `/new`/`/reset`/expiry) and immediately
     re-indexes the just-closed session in the background.

A file lock (`index_lock.py`) prevents concurrent indexing (cron vs plugin).
`content_hash` is written **after** a successful card+embedding, so an
interrupted index is retried (idempotent, no duplicates).

---

## Repository layout

```
wiki-memory/
├── AGENTS.md              # agent map (read first)
├── README.md              # this file
├── ARCHITECTURE.md        # system design
├── SECURITY.md            # secrets & boundaries
├── INSTALL.md             # install & cron setup (Windows + Linux + Docker)
├── QUALITY_SCORE.md       # doc legibility grading
├── docs/
│   ├── index.md
│   ├── design-docs/core-beliefs.md
│   └── exec-plans/        # history of the do-index implementation
├── src/wiki_v2/           # the core package (cross-platform)
│   ├── config.py          # env-driven path resolution
│   ├── nvidia_client.py   # NVIDIA chat + embeddings client
│   ├── indexer.py         # indexing pipeline
│   ├── index_db.py        # SQLite store (pages, embeddings, sessions)
│   ├── search.py          # two-tier search + synthesis
│   ├── session_status.py  # "is session finished?" detector
│   ├── index_lock.py      # cross-platform file lock
│   └── tests/             # pytest suite
├── scripts/               # entry points (run_indexer, run_sweep, facts bridge)
├── plugins/               # Hermes plugins (wiki-context, wiki-session-finalize)
├── examples/              # sample configs / docker-compose
├── pyproject.toml
└── .env.example
```

---

## Quick start

```bash
# 1. Install
pip install -e .
# or just run from source (numpy + requests required)

# 2. Configure (copy to your Hermes .env)
cp .env.example .env   # fill in NVIDIA_API_KEY

# 3. Index your whole history (one pass per 5 sessions, loops)
python scripts/run_sweep.py

# 4. Search
python -m wiki_v2.search "how does networking work"

# 5. Install the plugins (see INSTALL.md)
#    - wiki-context: auto-inject relevant wiki into every message
#    - wiki-session-finalize: immediate index on /new

# 6. Schedule the cron sweep (every 3 h) — see INSTALL.md
```

See **INSTALL.md** for full desktop / server / Docker setup.

---

## Requirements

- Python 3.10+
- `numpy`, `requests`
- An **NVIDIA API key** (`NVIDIA_API_KEY`) for extraction + embeddings
  (free tier at build.nvidia.com). *Optional:* a local embedding backend
  (e.g. LM Studio with a 1024-dim model like `bge-m3`) can replace NVIDIA
  embeddings to save quota — see ARCHITECTURE.md.
- Hermes Agent (sessions DB, plugin system).

---

## License

MIT
