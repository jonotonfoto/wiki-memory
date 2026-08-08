# CHANGELOG — wiki_v2

This changelog records what was BUILT and what was FOUND (not what is planned).

## 2026-08-08 — Live-code sync + short-query guard

### Sync (ops)
- Live `~/AppData/Local/hermes/scripts/wiki_v2/` and project `scripts/wiki_v2/`
  updated to the cross-platform version from `wiki-memory/src/wiki_v2/` (single
  `config.py`). Previously both held the old Linux variant hardcoded to
  `/opt/data/wiki`, so CLI search pointed at a non-existent Windows DB and
  returned "Nothing found" while the `wiki-context` plugin worked (it sets
  `WIKI_PATH` itself).
- Verified: CLI search from the live dir now resolves
  `LOCALAPPDATA\hermes\wiki` and finds VPS info.

### Short-query guard (search.py)
- Found: a query shorter than 15 chars (`MIN_QUERY_LEN=15`) returns empty
  immediately — an intentional anti-garbage guard, NOT a bug. "vps" (3) → empty,
  "статус hermes бота на vps сервере" (30+) → full synthesis. Pitfall recorded
  in the `wiki-v2-knowledge-base` skill (item 10a).

---

## 2026-08-08 — Triangulation, cache, duplicate cleanup

### Search (search.py)
- Keyword search now matches by ROOTS (first 5 letters): «сознания» ≈ «сознание»,
  «делегировать» ≈ «делегирование». Previously — exact word match.
- Removed the half-cap on keyword score (score*0.5 → min(score, 0.35)):
  root search gave 0.333*0.5=0.166 and was dropped as garbage.

### wiki-context plugin (triangulation)
- Relevance filter: semantics + confirmation from the page's "## Темы" section.
  A page passes if score >= 0.60, OR (score >= 0.40 AND ≥ 2 common roots
  with topics). Keyword hits — strict check (≥ 2 roots).
- Removes false positives (e.g. "Крейсер Аврора" on a memory question).
- Parameters moved to config.json (re-read on every request, no restart).

### Cache (plugin)
- cache.json: {question: {ctx, ts}}. Similar questions answered instantly.
- LRU rotation: limit 100, TTL 7 days, used entries refreshed.

### Extractor (extract.py)
- Prompt asks Nemotron to add RELATED concepts and synonyms to key_topics
  («сознание», «психология» for a Vygotsky page) so abstract queries find
  pages by meaning.

### Merging (pages.py)
- Jaccard 0.34 → 0.20 on roots (5 letters). Threshold lowered because the
  extractor now adds synonyms — exact topic overlap decreased.

### Duplicate cleanup (cleanup_duplicates.py — NEW)
- Auto-deletes duplicate pages (file + DB + embedding).
- Safety: skips `untitled` and groups with different sources.
- Runs after each indexing pass.
- First run on VPS: removed 59 duplicates + 2 fallback (153 → 92 pages).

### Permissions
- Wiki files belong to `hermes` (uid 10000); root-owned files are fixed
  externally (`chown -R hermes:hermes <wiki-path>`).

---

## 2026-08-04 — Search tuning + wiki-context plugin

### Problem: garbage in search results
- "привет" → "Первый контакт с ИИ" (0.50)
- random letters → "Untitled" (0.34)
- "расскажи что-нибудь" → garbage (0.38)

### Relevance thresholds (search.py)

| Param | Before | After |
|-------|--------|-------|
| MIN_SEMANTIC_SCORE | 0.35 | **0.40** |
| MAX_KEYWORD_SCORE | — | **0.35** |
| MIN_KEYWORD_SCORE | — | **0.30** |
| MIN_QUERY_LEN | — | **15** |

### wiki-context plugin (new)
Hooks `pre_llm_call`: searches the wiki before every model call.
Runs in the Hermes venv but uses numpy from `.venv-wiki`. YAML frontmatter stripped.

---

## 2026-08-04 — Post-audit fixes

- **#1 (CRITICAL): facts bridge dead** — nothing read `.facts_pending.jsonl`.
  Created `facts_bridge_import.py`. Verified: 23+ facts imported.
- **#2: conversation end lost** — on limit, keep head (70%) + tail (30%).
- **#3: search ignored page content** — now reads full .md text.
- **#4: garbage "Untitled" pages** — unnamed session named after first user line.
- **#5: letter «э» broke search** — removed the э→е replacement.

---

## 2026-08-03 — Initial release

### Built
- quality.py, nvidia_client.py, extract.py, index_db.py, embed.py, slug.py,
  pages.py, indexer.py, search.py, migrate.py, facts_bridge.py

### Bugs found and fixed
- **#1: min_len too high** (40 → 30)
- **#2: Jaccard merge never fired** — title added as ONE string, not words
- **#3: NVIDIA API returns list, not ndarray**
- **#4: same list-vs-array bug in indexer.py**

### Migration v1 → v2
| Metric | Value |
|--------|-------|
| Pages migrated | 20 |
| Embeddings generated | 20 |
| Pages flagged needs-review | 10 |

---

## Lessons (harvest engineering)
1. Don't pre-plan parameters — validate against real data.
2. Edge-case tests beat happy-path unit tests.
3. JSON ≠ numpy — always wrap in `np.array()`.
4. Minimum deps — numpy + requests suffice.
5. SQLite + WAL is enough for single-threaded indexing.
