# FIXES — Fix log (for the second Hermes)

> **Purpose:** this file is a quick onboarding for any agent (the second Hermes,
> future sessions). It lists ALL fixes to wiki-memory and its plugins with dates,
> so nobody has to figure things out from scratch. Updated by hand after each fix.

---

## 2026-08-08 — Search couldn't find Vygotsky on abstract queries

**Symptom:** "Who was the father of thinking theory in psychology in Russia and the USSR?"
didn't find the "Vygotsky Principles" page, even though it has the topics "thinking",
"psychology", "consciousness". Garbage ("Bot delegation") was pulled instead.

**Cause:** semantic garbage (score 0.40-0.47) filled the whole top-10 candidates,
pushing out the useful keyword hit (Vygotsky, score 0.333). Keyword hits ranked
below semantic and never made it into the plugin output.

**Fix:** the `wiki-context/__init__.py` plugin now requests `top_k * 3` candidates
(30 instead of 10) from search — keyword hits are guaranteed to be among the
candidates, then filtered by topic triangulation.

**Files:** `/opt/data/plugins/wiki-context/__init__.py`

**Status:** ✅ fixed, tests 15/16 (the only ❌ is negation, see below)

---

## 2026-08-08 — Cache served stale results after an algorithm change

**Symptom:** after the top_k*3 fix, search still didn't find Vygotsky — the plugin
returned the old cached result.

**Cause:** `cache.json` held a result computed BEFORE the algorithm changed. The
cache didn't know the algorithm had changed.

**Fix (lesson):** when you change the search algorithm you MUST clear the cache:
```bash
rm /opt/data/plugins/wiki-context/cache.json
```
(or via the plugin). The cache repopulates itself.

**Status:** ✅ lesson recorded. Recommendation: add an algorithm version to the
cache key (TODO).

---

## 2026-08-08 — Keyword search didn't match words in different forms

**Symptom:** «сознания» didn't match «сознание» (different cases), «делегировать»
didn't match «делегирование». Abstract queries didn't find pages.

**Cause:** keyword search compared exact word occurrences (`w in hay`).

**Fix:** `search.py` — keyword search now matches by ROOTS (first 5 letters):
«сознания» ≈ «сознание». Also removed the half-cap on keyword score
(`score*0.5` → `min(score, 0.35)`), which turned 0.333 into 0.166 and dropped
it as garbage.

**Files:** `/opt/data/scripts/wiki_v2/search.py`

**Status:** ✅ fixed

---

## 2026-08-08 — Garbage in output: "Cruiser Aurora" on a memory question

**Symptom:** the weak NVIDIA embedder on Russian gave garbage scores 0.40-0.46,
indistinguishable from relevant. "Cruiser Aurora" was pulled on a question about
auto-injecting memory.

**Cause:** the plugin trusted semantics alone in the gray zone 0.40-0.60.

**Fix:** triangulation in the plugin — a page passes only if:
- semantics ≥ `high_confidence` (0.60), **OR**
- semantics ≥ 0.40 **AND** the question's words matched the page's "## Темы"
  (≥ 2 common roots; for keyword hits — strict check ≥ 2).

**Files:** `/opt/data/plugins/wiki-context/__init__.py`

**Status:** ✅ fixed

---

## 2026-08-08 — Duplicate pages on every indexing pass

**Symptom:** the indexer created a copy of each page on every run
(153 pages for ~90 unique). MERGE wasn't firing.

**Cause 1:** `find_merge_target` used Jaccard 0.34 on EXACT topics — after adding
synonyms to the extractor, topic overlap shrank and the threshold wasn't reached.

**Cause 2:** fallback pages (quality=fallback, "raw fragment") became merge targets
instead of normal pages.

**Fix 1:** `pages.py` — Jaccard 0.20 on ROOTS (5 letters).
**Fix 2:** `cleanup_duplicates.py` (NEW) — auto-cleans duplicates:
- groups by slug root (ignoring suffixes -2, -3, -20260804);
- skips `untitled` (different unnamed sessions);
- skips groups with different sources (different conversations);
- deletes file + DB record + embedding;
- `--apply` for real deletion (dry-run by default).

**Files:** `/opt/data/scripts/wiki_v2/pages.py`,
`/opt/data/scripts/wiki_v2/cleanup_duplicates.py`,
`/opt/data/scripts/wiki_v2/index_db.py` (added `delete_page`),
`/opt/data/scripts/run_wiki_indexer.sh` (cleanup after indexing).

**Status:** ✅ fixed, first run: 153 → 92 pages.

---

## 2026-08-08 — Extractor didn't add related concepts to topics

**Symptom:** the Vygotsky page's topics were "Vygotsky, cultural-historical theory,
behaviorism" — but NOT "consciousness", "psychology", "thinking". Abstract queries
didn't find the page.

**Cause:** the extract.py prompt only asked for "key conversation topics" — the
model took literal words, no synonyms.

**Fix:** `extract.py` — the prompt asks Nemotron to add RELATED concepts and
synonyms to key_topics («сознание» for the Vygotsky page), even if they weren't
spoken explicitly.

**Files:** `/opt/data/scripts/wiki_v2/extract.py`

**Status:** ✅ fixed. BUT: on long sessions the model sometimes returns invalid
JSON (fallback). This is a model limitation, not a code bug.

---

## 2026-08-08 — Permissions: root-owned files broke indexing

**Symptom:** `.facts_pending.jsonl` and many pages belonged to root — the indexer
(uid 10000) couldn't write, the facts bridge crashed (Permission denied).

**Cause:** the indexer was run as root (docker exec) in the past.

**Fix:** chown from outside the container:
```bash
chown -R hermes:hermes /opt/data/wiki /opt/data/plugins/wiki-session-finalize /opt/data/scripts/wiki_v2
```
Done by the second Hermes (desktop) from the host.

**Status:** ✅ fixed. RULE: the wiki must always belong to hermes (uid 10000).
If files become root again — chown from outside.

---

## 2026-08-08 — Repeated-query cache (NEW FEATURE)

**What:** the plugin caches search results in `cache.json` `{question: {ctx, ts}}`.

**How it works:**
- similar question (≥ 2 common roots; for short ≤ 2 words — ≥ 1)
  → instant answer from cache;
- LRU rotation: limit `CACHE_MAX_ENTRIES = 100`, TTL `CACHE_MAX_AGE = 7 days`;
- used entries are refreshed (not evicted).

**Files:** `/opt/data/plugins/wiki-context/__init__.py` (functions `_cache_*`),
`/opt/data/plugins/wiki-context/cache.json` (auto-created).

**Status:** ✅ works. DON'T FORGET: after changing the search algorithm, clear the
cache (see the 2026-08-08 fix above).

---

## 2026-08-08 — Plugin config moved to a separate file

**What:** all plugin settings are in `/opt/data/plugins/wiki-context/config.json`
(re-read on EVERY request, no gateway restart):

| Key | Value | Meaning |
|-----|-------|---------|
| `top_k` | 10 | how many candidates to take from search |
| `min_score` | 0.40 | lower semantic threshold |
| `high_confidence` | 0.60 | above — take without topic check |
| `max_context_chars` | 3000 | injected context size limit |
| `min_query_len` | 15 | shorter — don't search |
| `log_filtered` | true | log filtered pages |

**Status:** ✅ works.

---

## 2026-08-08 — Crons moved to free NVIDIA NIM

**What:** LLM crons (`hermes-update-check`, `hermes-android-tls-check`)
moved from OpenRouter (deepseek-v4-flash, paid) to NVIDIA NIM
(provider=`nvidia`, model=`nemotron-3-super-120b-a12b`, free).

**Endpoint:** `https://integrate.api.nvidia.com/v1`, key NVIDIA_API_KEY.

**Check:** `/opt/data/cron/jobs.json` — model/provider updated.

**Status:** ✅ fixed. ALL background tasks are now free:
- wiki indexing: nemotron-3-super (Nvidia, free)
- embeddings: nv-embedqa-e5-v5 (free)
- vision: nemotron-nano-12b-vl (free)
- crons: nemotron-3-super (free)

---

## Known limitations (not bugs)

1. **Negations** — "where NOT to take kids" is algorithmically almost
   indistinguishable from "where to take kids". Not solvable without an LLM. Accepted.
2. **NVIDIA embedder is weak on Russian** — relevant and garbage scores overlap
   (0.40-0.55). Compensated by root-based keyword search + triangulation.
3. **Long sessions** — the extractor sometimes returns invalid JSON (fallback page).
   Not critical; merge fixes it on the next pass.

---

## How to verify everything works

```bash
# Search (should find Vygotsky)
cd /opt/data && /opt/hermes/.venv/bin/python -c "
import sys, importlib.util, re
spec = importlib.util.spec_from_file_location('wctx', '/opt/data/plugins/wiki-context/__init__.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
ctx = mod._build_context('Who was the father of thinking theory in psychology in Russia and the USSR?')
print(re.findall(r'### Wiki: ([^\n]+)', ctx or ''))
"

# Tests
cd /opt/data/scripts && source /opt/data/.venv-wiki/bin/activate
python -m pytest wiki_v2/tests/ -q
```
