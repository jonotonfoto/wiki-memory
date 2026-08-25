# Wiki Memory v3 for Hermes Agent

[![CI](https://github.com/jonotonfoto/wiki-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/jonotonfoto/wiki-memory/actions/workflows/ci.yml)

> Semantic memory that turns agent conversations into a durable, searchable
> knowledge base. Successor of [wiki-memory v2](../../wiki-memory) вЂ” same idea,
> rebuilt around multi-vector retrieval, a local-first embedding backend, and
> an observability dashboard.

## What this is, in plain words

Your AI assistant forgets everything the moment a chat closes. Wiki Memory
quietly writes down what matters from every conversation, compresses it into
readable markdown pages, gives each page a **meaning fingerprint** (an
embedding), and lets the assistant recall it later — by meaning, not just by
exact words.

Knowledge you build once becomes knowledge you keep.

## How it works

```
sessions (Hermes state.db)
     |
     v
indexer.py --> chunker --> extract (chat LLM) --> validate --> merge/create page
     |                                                     (multivector embeddings)
     v
.index_v2.db (SQLite)
  - pages       (compressed markdown knowledge pages)
  - chunks      (text slices, each with its own embedding)
  - sessions    (raw transcripts, indexed flag + content_hash)
  - embeddings  (float32 BLOBs, 1024-dim, several vectors per item)
     |
     v
search.py --> embed query --> cosine top-K per channel (title/summary/tag/chunk)
     |                          + BM25 keyword channel --> RRF fusion
     └──> relevance gate (cheap, fail-open) --> optional LLM synthesis on hits
```

Terminology used everywhere in this repo:

- **session** — a raw conversation transcript,
- **page** — a compressed `.md` knowledge page distilled from sessions,
- **chunk** — a text slice of a page/session that carries its own embedding.

### Retrieval quality guards

- **Multi-vector fusion**: title / summary / tag / chunk channels are searched
  independently and merged with Reciprocal Rank Fusion (RRF). Never reduce this
  to a single vector — see `DECISIONS.md`.
- **Relevance gate**: junk queries ("continue", typos, pasted paths) are filtered
  cheaply before embedding — no LLM in the hot path, fail-open.
- **Root-based keyword matching** (`w[:5]` comparison) so Russian morphology
  («сознания» ≈ «сознание») does not break recall; keyword score is capped so
  semantics always wins ties.
- **Triangulation in `wiki-context`**: a weak hit must also share topic roots
  with the page before it is injected into context.
- **LRU answer cache** (100 entries, 7-day TTL) cuts repeated embed calls.

### Keeping the wiki up to date

1. **Cron sweep** — every few hours, indexes finished *changed* sessions
   (content-hash change detection).
2. **Plugin `wiki-session-finalize`** — hooks `on_session_finalize`
   (`/new`, `/reset`, expiry) and re-indexes the just-closed session immediately
   in the background.
3. A cross-platform file lock prevents concurrent indexing; `content_hash` is
   written only after a successful write, so interrupted runs retry idempotently.

## Repository layout

```
wiki-memory/
├── AGENTS.md              # agent map (read first)
├── README.md              # this file
├── ARCHITECTURE.md        # system design
├── INSTALL.md             # desktop + VPS setup
├── DECISIONS.md           # architecture decisions & rationale
├── SECURITY.md            # secrets & boundaries
├── src/wiki_v2/           # the core package (cross-platform, env-driven paths)
│   └── tests/             # pytest suite (invariants, not snapshots)
├── scripts/               # entry points: dashboard server, sweep loaders, watchdog
├── plugins/               # Hermes plugins: wiki-context, wiki-session-finalize
├── desktop-plugins/       # desktop UI button for the dashboard
├── profiles/              # deployment profiles (desktop / vps)
└── examples/              # docker-compose / config samples
```

## Quick start

```bash
# 1. Get the code
git clone https://github.com/jonotonfoto/wiki-memory.git
cd wiki-memory

# 2. Install dependencies (Python 3.10+)
pip install numpy requests

# 3. Point the package at your data dir and embedding backend
cp profiles/desktop.env.example profiles/desktop.env   # edit paths if needed

# 4. Index your history
python scripts/wiki_v3_sweep_loader.py                 # background sweep

# 5. Search
PYTHONPATH=src python -m wiki_v2.search "how does networking work"

# 6. Plugins + cron — see INSTALL.md
```

See **INSTALL.md** for full Windows-desktop and Linux-VPS (Docker) setups.

## Requirements

- Python 3.10+, `numpy`, `requests`
- An OpenAI-compatible **embedding endpoint**: llama.cpp server (CPU is fine,
  `Qwen3-Embedding-0.6B` quantized works), LM Studio, or NVIDIA API
- Hermes Agent (sessions DB, plugin system)

## License

MIT
