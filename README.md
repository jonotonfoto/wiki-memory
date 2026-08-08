# Wiki Memory for Hermes Agent

> **New agent / second Hermes:** read [`FIXES.md`](FIXES.md) first — the dated
> log of every fix, so you're up to speed before touching the code.

## What this is, in plain words

Think of it as giving your AI assistant a **notebook that never forgets**.

By default, an AI assistant starts each conversation fresh — it remembers only
what you tell it *in that moment*. Anything you discussed yesterday is gone the
moment you close the chat. Wiki Memory fixes that: it quietly writes down
everything useful you talk about, organizes it, and lets the assistant look it
up later — even weeks or months from now.

It's like the difference between having a **really good friend** who remembers
every detail of your life, and a stranger you have to re-explain everything to
every single time you meet.

### A concrete example

> You run a small online store. One day you spend an hour with your assistant
> figuring out **how to fix a shipping label that keeps printing with the wrong
> address**. You try a few things, find the cause (the address field had a
> trailing space), and agree on a fix.
>
> A month later, the same bug shows up again. A normal assistant would make you
> re-explain the whole problem. **With Wiki Memory**, the assistant already knows:
> *"Last time we found the address field had a trailing space — want me to apply
> the same fix?"* It remembers the diagnosis, the solution, and even *why*.

That's the whole point: **knowledge you build once becomes knowledge you keep.**

### How it works, in one breath

Every conversation is turned into a short, structured "page" — like a note card
in an index box. Each card gets a **semantic fingerprint** (an embedding: a list
of numbers capturing *meaning*, not just words). Later, when the assistant needs
to recall something, it doesn't do a literal word search — it compares the
*meaning* of your question against the *meaning* of every card it has saved, and
pulls up the closest matches. Word-search still backs it up as a fallback.

The technical details (extraction, embeddings, two-tier search, the
"auto-index finished sessions" system) are below.

---

## Why "wiki + embeddings" is so powerful

Two ordinary ideas become something special when combined:

- **A wiki** = knowledge stored as readable, organized pages. A human (or agent)
  can open them, skim them, correct them. It's *your* knowledge, in a form you
  can see and trust.
- **Embeddings** = every page gets a *meaning fingerprint*. The system
  understands what a page is *about*, not just which words appear in it.

### The magic is the *combination*

A wiki alone is just files — great for browsing, useless for "find me what I
need right now." Embeddings alone give you fuzzy "related stuff" but no
structure you can read or edit. Together:

1. **You read and edit the wiki** (it's human-friendly markdown).
2. **The agent finds meaning** (via embeddings) — even when your question uses
   completely different words than the page does.

### Example: synonyms don't break recall

> One day you and your assistant talk about **"fixing the printer that prints
> blank pages"**. The assistant saves a page about it.
>
> Weeks later you ask: **"why does the office copier spit out empty sheets?"**
>
> A plain keyword search would find *nothing* — the words "printer", "blank",
> and "fix" don't appear in your new question ("copier", "empty sheets").
>
> **With embeddings**, the assistant sees that "printer ≈ copier",
> "blank pages ≈ empty sheets" — the *meaning* matches — and pulls up the right
> page instantly, even though you used entirely different words.

That's the core strength: **the assistant understands what you mean, not just
what you say.** A wiki gives it a trustworthy, editable memory; embeddings give
it the ability to find the right memory at the right moment.

---

## Quick overview (technical)

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

### Retrieval quality (root matching, triangulation, cache, dedup)

Beyond the two-tier search, the retrieval path has several quality guards
(2026-08-08):

- **Root-based keyword matching** — words are compared by their first 5 letters,
  so «сознания» ≈ «сознание» and «делегировать» ≈ «делегирование». Keyword
  score is capped at `MAX_KEYWORD_SCORE` (0.35) so semantic (0.40+) always wins.
- **Triangulation in `wiki-context`** — a weak Russian embedder can return
  garbage in the 0.40–0.60 gray zone. The plugin doesn't trust semantics alone:
  a page passes if the score is confidently high (≥ `high_confidence`, 0.60),
  **or** the query shares ≥ 2 roots with the page's `## Темы` section.
- **LRU answer cache** (`cache.json`) — similar questions are answered instantly,
  cutting repeated embed-API calls (limit 100 entries, TTL 7 days).
- **`cleanup_duplicates.py`** — groups pages by slug root and deletes true
  duplicates (same source), skipping `untitled` and differently-sourced groups.
  Run with `--dry-run` by default; `--apply` deletes.
- **Extractor synonyms** — the `extract.py` prompt asks Nemotron to add related
  concepts / synonyms to `key_topics` so abstract queries find pages by meaning.

The plugin's tunables live in `config.json` (re-read on every request — no
restart). See `CHANGELOG.md` and `DECISIONS.md` for the rationale.

---

## Repository layout

```
wiki-memory/
├── AGENTS.md              # agent map (read first)
├── README.md              # this file
├── ARCHITECTURE.md        # system design
├── CHANGELOG.md           # build history
├── DECISIONS.md           # architecture decisions & rationale
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
│   ├── cleanup_duplicates.py  # duplicate/markdown cleanup
│   ├── session_status.py  # "is session finished?" detector
│   ├── index_lock.py      # cross-platform file lock
│   └── tests/             # pytest suite
├── scripts/               # entry points (run_indexer, run_sweep, facts bridge)
├── plugins/               # Hermes plugins (wiki-context, wiki-session-finalize)
├── skills/                # Hermes skill tap (SKILL.md)
├── promo/                 # ready-to-post announcements (Discord, HN, Reddit, X)
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

## Install as a Hermes skill tap

The repo is structured as a Hermes **skills tap** — Hermes users can add it and
install the skill directly from GitHub:

```bash
hermes skills tap add jonotonfoto/wiki-memory
hermes skills install wiki-memory   # or browse with: hermes skills search wiki-memory
```

This makes the skill (setup/repair instructions) available to any Hermes agent,
alongside the code in this repo.

---

## Spreading the word

Ready-to-post announcements for Discord, Hacker News, Reddit, and X are in
[`promo/`](promo/README.md) — human, no-fluff copy you can copy-paste.

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
