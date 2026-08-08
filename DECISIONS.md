# Architecture decisions — wiki_v2

Records decisions made about what was BUILT and WHY, not what is planned.

---

## 1. NVIDIA API as the single point

**Decision:** Use `https://integrate.api.nvidia.com/v1` for both chat and embeddings.

**Why:** one dependency, nemotron-3-super-120b-a12b is a good extraction model,
nv-embedqa-e5-v5 is 1024-dim good for <1000 docs, one API key / config.

---

## 2. numpy instead of FAISS

**Decision:** Cosine search via `np.dot`, loading all embeddings into memory.

**Why:** at 20-100 pages that's 80-400KB in RAM. numpy already required.
FAISS is a heavy dependency. Switch when >1000 pages, without changing the interface.

---

## 3. SQLite instead of PostgreSQL

**Decision:** Everything stored in `.index_v2.db` via sqlite3.

**Why:** no separate service, WAL mode is enough, BLOB works for embeddings, portable.

---

## 4. Double retry on extraction

**Decision:** Two passes: temp 0.3, then 0.1. On total failure — raw-text fallback.

**Empirically:** second pass needed ~10% of the time. Fallback <1% but important as a safety net.

---

## 5. Jaccard 0.20 on roots for merging (was 0.34)

**Decision:** Pages merge if overlap of title + key_topics words ≥ 0.20 on roots.

**Why:** 0.50 too strict, 0.20 too loose. On roots (first 5 letters); lowered
from 0.34 because the extractor now adds synonyms to topics, shrinking exact overlap.

---

## 6. 5 sessions per run

**Decision:** indexer processes at most 5 sessions per run.

**Why:** ~15 API calls per run, within rate limits. Cron covers 35 sessions/week.

---

## 7. 8000 chars per chunk, 500 per message

**Decision:** Sessions chunked to 8000 chars; each message truncated to 500 chars.

---

## 8. BLOB for embeddings

**Decision:** Embeddings stored as `struct.pack(f'{dim}f', *vec)` in a SQLite BLOB.

---

## 9. Migration as a separate script

**Decision:** `migrate.py` is a one-shot script, not part of the indexer.

---

## 10. facts_bridge as a separate module

**Decision:** `facts_bridge.py` appends facts/decisions to `.facts_pending.jsonl`.
The fact_store picks them up on the next run — polling, not push.

---

## 2026-08-08 — Triangulation instead of trusting the embedder

The weak Russian embedder produces garbage in the 0.40-0.60 gray zone
(«Крейсер Аврора» on a memory question = 0.46). Decision: don't trust semantics
alone — require confirmation from the page's "## Темы" section (≥ 2 common roots).
Result: 15/16 on test queries (the only failure — the negation «куда НЕ водить
детей», unsolvable without an LLM).

## 2026-08-08 — Keyword search by roots

Exact word matching didn't find «сознания» ≈ «сознание». Decision: compare the
first 5 letters. Also removed the half-cap on score. Now root keyword finds
Vygotsky on abstract queries («теория сознания»).

## 2026-08-08 — LRU cache

Repeated queries shouldn't hit the embed API every time. Decision: cache.json
{question: {ctx, ts}}, limit 100 entries, TTL 7 days, LRU rotation.

## 2026-08-08 — Auto duplicate cleanup

The indexer created page copies on every run (153 pages at ~90 unique). Decision:
cleanup_duplicates.py — group by slug root, delete only when sources match, skip
`untitled`. First run: 153 → 92 pages.

## 2026-08-08 — Extractor adds synonyms to topics

extract.py's prompt asks Nemotron to include related concepts in key_topics
(«сознание» for a Vygotsky page). This lets abstract queries find pages by
meaning, without an LLM at search time.
