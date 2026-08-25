"""Wiki search v3: embeddings + keywords, LLM synthesis only on hits."""
import json
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiki_v2 import config
from wiki_v2.embed import top_k_cosine
from wiki_v2.graph import bfs
from wiki_v2.index_db import IndexDB
from wiki_v2.logging_setup import logger
from wiki_v2.gateway import api_state, chat_completion, embed, ensure_embed_ready
from wiki_v2.relevance_gate import BETA, get_hubness, top_k_cosine_hub

WIKI_PATH = str(config.WIKI_PATH)
INDEX_DB = os.path.join(WIKI_PATH, ".index_v2.db")
MIN_SEMANTIC_SCORE = 0.40
# Keyword-результаты никогда не выше этого (semantic выигрывает)
MAX_KEYWORD_SCORE = 0.35
# Минимальный keyword-скор, иначе мусор («расскажи что-нибудь»)
MIN_KEYWORD_SCORE = 0.30
# Короткие запросы («ок») не ищем — только мусор находят
# Единый порог из config (Этап 5.2): WIKI_MIN_QUERY_LEN
MIN_QUERY_LEN = config.get("WIKI_MIN_QUERY_LEN", 3)

# S2.5.11: TOP_K из config (настраиваемый, не хардкод)
TOP_K = config.TOP_K

# S2.5.2: Query Expansion
QUERY_EXPANSION_VARIANTS = getattr(config, "QUERY_EXPANSION_VARIANTS", 4)
QUERY_EXPANSION_ENABLED = getattr(config, "QUERY_EXPANSION_ENABLED", True)
QUERY_EXPANSION_TTL = getattr(config, "QUERY_EXPANSION_TTL", 3600)
QUERY_EXPANSION_CACHE_MAX = getattr(config, "QUERY_EXPANSION_CACHE_MAX", 128)

# кэш расширений: {query: (variants, timestamp)}
_expansion_cache: dict = {}


def _normalize(text):
    text = text.lower()
    # Только ё→е (и мягкий/твёрдый знаки) — буква «э» НЕ заменяется,
    # иначе «это» превращается в «ето» и ломает русский поиск
    for old, new in {"ё": "е", "ъ": "", "ь": ""}.items():
        text = text.replace(old, new)
    return text


def keyword_hits(query: str, pages: list, k: int = 5):
    words = {w for w in re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", _normalize(query))}
    scored = []
    for p in pages:
        hay = _normalize(f"{p['title']} {p['summary']}")
        # Добавляем содержимое страницы из БД (full_text), НЕ с диска (этап 1.4)
        full = p.get("full_text", "")
        if full:
            hay += " " + _normalize(full)
        score = 0
        # Сравнение по корням (префикс 5 букв): «сознания» ≈ «сознание»,
        # «делегировать» ≈ «делегирование». Одно и то же слово в разных
        # падежах/формах считается одним совпадением.
        hay_words = set(re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", hay))
        matched = set()
        for w in words:
            for hw in hay_words:
                m = min(len(w), len(hw))
                if m >= 5 and w[:5] == hw[:5]:
                    matched.add(w[:5])
                    break
                elif m < 5 and w == hw:
                    matched.add(w)
                    break
        score = len(matched)
        if score:
            scored.append((p["slug"], score / max(len(words), 1)))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]

def _rrf(rank_lists, k=None):
    """Reciprocal Rank Fusion.

    score(slug) = sum over each ranked list of 1/(k + rank), rank 1-indexed.
    A slug present in multiple lists gets a higher score (dual contribution).
    rank_lists: list of lists of slugs, each ordered by relevance (best first).
    Returns dict {slug: score}, higher = better.
    """
    if k is None:
        k = config.RRF_K  # S2.5.11: параметр из config, не хардкод
    scores = {}
    for ranked in rank_lists:
        for rank, slug in enumerate(ranked, start=1):
            scores[slug] = scores.get(slug, 0.0) + 1.0 / (k + rank)
    return scores


def _rank_multi_vec(query_vec, store, hubness, k):
    """Ранжирование страниц по ЛУЧШЕМУ их вектору (max-per-slug, фикс 2026-08-24).

    Для семей с многими векторами на страницу (tag:*, chunk:*, page_chunk:*):
    скор страницы = max по её векторам от cos(q,v) − β·h(p) при cos ≥ 0.
    Заменяет перезапись последней копии через dict.update() в bucket-сборке.
    """
    qn = np.asarray(query_vec, dtype=np.float64)
    qnorm = np.linalg.norm(qn)
    if qnorm == 0 or not store:
        return []
    qn = qn / qnorm
    best = {}
    for slug, vecs in store.items():
        hb = BETA * hubness.get(slug, 0.0)
        top_sc = None
        for vec in vecs:
            vn = np.asarray(vec, dtype=np.float64)
            nv = np.linalg.norm(vn)
            if nv == 0:
                continue
            cos = float(np.dot(qn, vn / nv))
            if cos < 0.0:
                continue
            sc = cos - hb
            if top_sc is None or sc > top_sc:
                top_sc = sc
        if top_sc is not None:
            best[slug] = top_sc
    ranked = sorted(best.items(), key=lambda x: -x[1])
    return [s for s, _ in ranked[:k]]


def _lru_expansion_cache():
    """Кэш расширений с TTL-очисткой и LRU-эвикцией по размеру.

    Возвращает dict {query: (variants, timestamp)}. Перед возвратом
    удаляет протухшие записи и при превышении лимита — самые старые.
    """
    global _expansion_cache
    now = time.time()
    # 1. TTL-очистка: убрать протухшие записи
    stale = [q for q, (_, ts) in _expansion_cache.items()
             if (now - ts) >= QUERY_EXPANSION_TTL]
    for q in stale:
        del _expansion_cache[q]
    # 2. LRU-эвикция по размеру: если превышен лимит — убрать самые старые
    if len(_expansion_cache) > QUERY_EXPANSION_CACHE_MAX:
        oldest = sorted(_expansion_cache.items(),
                        key=lambda kv: kv[1][1])[:len(_expansion_cache) - QUERY_EXPANSION_CACHE_MAX]
        for q, _ in oldest:
            del _expansion_cache[q]
    return _expansion_cache


def _cache_bump(name: str) -> None:
    """Increment a cache metric (cache_hits_total / cache_misses_total).

    fail-open: never raises, so cache bookkeeping can't break search.
    """
    try:
        from wiki_v2 import metrics as _m
        _m.inc(name)
    except Exception:
        pass


def _parse_variants(raw, original):
    """Распарсить ответ LLM в список строк. Не падает → [original] на ошибке."""
    if not raw:
        return [original]
    text = raw.strip()
    # убрать markdown-обёртки
    text = text.strip("`").strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list) and parsed:
            strs = [str(x).strip() for x in parsed if str(x).strip()]
            if strs:
                return [original] + strs
    except (ValueError, TypeError):
        pass
    # построчно: каждая строка — вариант
    lines = [ln.strip().strip('"').strip("'").strip("-").strip()
             for ln in text.splitlines() if ln.strip()]
    if lines:
        return [original] + lines
    return [original]


def expand_query(query, variants=QUERY_EXPANSION_VARIANTS):
    """Расширение запроса: LLM генерирует перефразы, кэш LRU с TTL.

    Возвращает список [исходный, ...варианты]. НИКОГДА не бросает:
    на любой ошибке → [query]. Повторный вызов → из кэша (API не тратится).
    """
    if not query:
        return [query]
    cache = _lru_expansion_cache()
    now = time.time()
    cached = cache.get(query)
    if cached and (now - cached[1]) < QUERY_EXPANSION_TTL:
        _cache_bump("cache_hits_total")
        return cached[0]
    _cache_bump("cache_misses_total")
    try:
        prompt = (
            "Ты — помощник по расширению поисковых запросов для базы знаний. "
            f"Перефразируй следующий запрос {max(variants - 1, 1)} разными способами, "
            "чтобы охватить синонимы и близкие по смыслу формулировки. "
            "Верни ТОЛЬКО JSON-массив строк, без markdown, на русском.\n"
            f"Запрос: {query}"
        )
        raw = chat_completion(
            "Ты перефразируешь поисковые запросы. Отвечаешь только JSON-массивом строк.",
            prompt, max_tokens=300)
        result = _parse_variants(raw, query)
    except Exception:
        result = [query]  # fail-open
    cache[query] = (result, now)
    return result


def _stem(word):
    """Лёгкий root-stemming для русского (и латиницы).

    Убирает ё→е, ъ/ь, и обрезает частые окончания существительных/прилагательных,
    чтобы «сознание/сознания/сознанию/сознанием» → один корень «сознани».
    """
    w = word.lower()
    for old, new in {"ё": "е", "ъ": "", "ь": ""}.items():
        w = w.replace(old, new)
    # частые русские окончания (грубо, для поиска)
    for suf in ("ениями", "ениях", "ения", "ению", "ением", "ений",
                "ание", "ания", "анию", "анием", "аний",
                "ость", "ости", "остью",
                "ов", "ах", "ям", "ях", "ой", "ому", "ого", "его",
                "ого", "ому", "ым", "ими", "ая", "ое", "ую", "ие", "ий",
                "ей", "ев", "ом", "а", "у", "е", "и", "ы", "я"):
        if len(w) > 4 and w.endswith(suf):
            return w[:len(w) - len(suf)]
    return w


# Fuzzy-fallback BM25 (2026-08-25): опечатка в запросе ломала точное
# совпадение стеммов («киркоров» ≠ «керкоров») → keyword-канал умирал,
# страница держалась только на шумном semantic-ранге. Токен запроса без
# точного совпадения в словаре корпуса маппится на ближайший по
# Левенштейну токен с весом FUZZY_TERM_WEIGHT. Границы детерминированы
# длиной (без «хрупких порогов»): len ≥ 8 → dist ≤ 2, len 5–7 → dist ≤ 1,
# короче — не исправляем («код»→«год» и т.п. ложные совпадения).
FUZZY_BM25_ENABLED = os.environ.get("WIKI_FUZZY_BM25", "1") != "0"
FUZZY_TERM_WEIGHT = 0.7


def _levenshtein(a: str, b: str) -> int:
    """Расстояние редактирования (классический DP, O(len(a)*len(b)))."""
    if a == b:
        return 0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _fuzzy_max_dist(word: str) -> int:
    """Допустимая дистанция по длине слова: ≥8 → 2, 5–7 → 1, <5 → 0."""
    if len(word) >= 8:
        return 2
    if len(word) >= 5:
        return 1
    return 0


def _fuzzy_term_weights(terms: set, df: dict) -> dict:
    """Query-side коррекция опечаток: {term: weight} для BM25.

    Точные совпадения → вес 1.0; токен без вхождения в словарь корпуса (df)
    маппится на ближайшего кандидата (Левенштейн ≤ границы по длине, обе
    стороны ≥ 5 символов); при равной дистанции побеждает более частотный.
    """
    weights = {t: 1.0 for t in terms}
    for t in terms:
        maxd = _fuzzy_max_dist(t)
        if t in df or not maxd:
            continue
        best, best_d, best_df = None, maxd + 1, -1
        for cand, cand_df in df.items():
            if abs(len(cand) - len(t)) > maxd or _fuzzy_max_dist(cand) == 0:
                continue
            d = _levenshtein(t, cand)
            if d <= maxd and (d < best_d or (d == best_d and cand_df > best_df)):
                best, best_d, best_df = cand, d, cand_df
        if best is not None:
            weights[best] = max(weights.get(best, 0.0), FUZZY_TERM_WEIGHT)
    return weights


def _bm25_rank(query, pages, k=20):
    """BM25-ранжирование страниц по full_text (и title/summary).

    Возвращает список slug, упорядоченных по BM25-скор (лучший первый).
    fail-open: пустая строка/ошибка → [] (векторный ранг остаётся).
    """
    if not query or not pages:
        return []
    try:
        terms = [t for t in (re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", _normalize(query)))]
        terms = [_stem(t) for t in terms]
        if not terms:
            return []
        # документы: нормализованный full_text + title + summary
        docs = []
        doc_terms = []
        for slug, p in pages.items():
            hay = _normalize(p.get("full_text", "") or "")
            hay += " " + _normalize(p.get("title", "") or "")
            hay += " " + _normalize(p.get("summary", "") or "")
            toks = [t for t in re.findall(r"[а-яА-ЯёЁa-zA-Z0-9]{3,}", hay)]
            toks = [_stem(t) for t in toks]
            docs.append(slug)
            doc_terms.append(toks)
        # IDF по корпусам
        import math
        n_docs = len(docs)
        df = {}
        for toks in doc_terms:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1
        idf = {t: math.log(1 + (n_docs - f + 0.5) / (f + 0.5)) for t, f in df.items()}
        # BM25 скор (k1=1.5, b=0.75); термины взвешены (fuzzy-коррекция опечаток)
        term_weights = (_fuzzy_term_weights(set(terms), df)
                        if FUZZY_BM25_ENABLED else {t: 1.0 for t in set(terms)})
        k1, b = 1.5, 0.75
        avg_len = sum(len(t) for t in doc_terms) / max(n_docs, 1)
        scored = []
        for slug, toks in zip(docs, doc_terms):
            dl = len(toks)
            score = 0.0
            tf_count = {}
            for t in toks:
                tf_count[t] = tf_count.get(t, 0) + 1
            for t, w in term_weights.items():
                tf = tf_count.get(t, 0)
                if tf == 0:
                    continue
                t_idf = idf.get(t, 0.0)
                score += w * t_idf * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / max(avg_len, 1)))
            if score > 0:
                scored.append((slug, score))
        scored.sort(key=lambda x: -x[1])
        return [s for s, _ in scored[:k]]
    except Exception:
        return []


def _hybrid_merge(vector_ranks, bm25_rank, k=5):
    """Объединить векторные ранги + BM25-ранг через взвешенный RRF.

    vector_ranks: list of list-of-slug (semantic/QE).
    bm25_rank: list-of-slug (keyword/BM25) или [].
    BM25-список получает вес config.W_BM25 (фикс 2026-08-24: согласие
    каналов должно влиять на порядок, W_BM25=1.3 по лаборатории).
    Возвращает список (slug, score) упорядоченный по убыванию RRF.
    """
    all_lists = [(lst, 1.0) for lst in vector_ranks]
    if bm25_rank:
        all_lists.append((bm25_rank, config.W_BM25))
    if not all_lists:
        return []
    scores = {}
    for lst, w in all_lists:
        for rank, slug in enumerate(lst, start=1):
            scores[slug] = scores.get(slug, 0.0) + w / (config.RRF_K + rank)
    return sorted(scores.items(), key=lambda x: -x[1])[:k]


def adaptive_top_k(query: str, base_k: int = TOP_K) -> int:
    """Адаптивный top_k в зависимости от длины запроса."""
    if not config.WIKI_ADAPTIVE_TOP_K_ENABLED:
        return base_k
    try:
        # Короткий запрос (< 15 символов) -> возвращаем 2 (или меньше, если base_k < 2)
        if len(query) < 15:
            return min(base_k, 2)
        # Длинный/многословный запрос (>= 30 символов) -> увеличиваем до max
        elif len(query) >= 30:
            return min(base_k * 2, config.WIKI_MAX_TOP_K)
        # Средний запрос -> возвращаем base_k
        else:
            return base_k
    except Exception:
        return base_k


def forgetting_factor(updated, confidence=0.5, now=None):
    """Ebbinghaus forgetting curve decay factor for a fact's confidence.

    Returns 1.0 (no decay) when WIKI_FORGET_ENABLED is False or updated is
    missing/invalid — fail-open: no date means full confidence preserved.

    Formula: decay = 0.5 ** (age_days / half_life)
    """
    try:
        if not config.WIKI_FORGET_ENABLED:
            return 1.0
        if updated is None or not isinstance(updated, (int, float)) or updated <= 0:
            return 1.0
        age_days = ((now or time.time()) - updated) / 86400.0
        half_life = config.WIKI_FORGET_HALF_LIFE_DAYS
        if half_life <= 0:
            return 1.0
        decay = 0.5 ** (age_days / half_life)
        return max(decay, 0.0)
    except Exception:
        return 1.0


def search(query: str, k: int = TOP_K):
    """Return (hits, pages_by_slug). hits: [(slug, score, source)]."""
    # Короткий запрос — не ищем (только мусор находит)
    if not query or len(query.strip()) < MIN_QUERY_LEN:
        return [], {}

    if k == TOP_K:  # дефолт — применить адаптацию
        k = adaptive_top_k(query, k)

    _t0 = time.time()  # Ф0.5.1/0.5.2: замер длительности поиска

    db = IndexDB(INDEX_DB)
    pages = {p["slug"]: p for p in db.all_pages()}
    if not pages:
        db.close()
        return [], {}

    hits = {}

    # S2.5.2: Query Expansion из ТЁПЛОГО кэша (не ждать LLM в хуке!).
    # Первый раз кэш холодный → поиск по исходному (быстро, без API-ожидания).
    # Повторный запрос → кэш тёплый → расширяем варианты и объединяем RRF.
    variants = [query]
    _cache = _lru_expansion_cache()
    _now = time.time()
    _cached = _cache.get(query)
    if (QUERY_EXPANSION_ENABLED and _cached
            and (_now - _cached[1]) < QUERY_EXPANSION_TTL):
        variants = _cached[0]
        _cache_bump("cache_hits_total")
    elif QUERY_EXPANSION_ENABLED:
        _cache_bump("cache_misses_total")

    # S2.5.3: гибридный поиск — векторные ранги (semantic/QE) + BM25 по full_text,
    # объединяются через RRF (_hybrid_merge) вместо хрупких порогов.
    vector_ranks = []   # list of slug-lists (источник "semantic")
    bm25_rank = []      # BM25-ранг (источник "keyword")

    # S2.5.5: Multi-vector search — per-kind RRF с весами W_MULTIVECTOR_*
    # kinds: {kind_name: weight} из config
    kind_weights = {
        "title": config.W_MULTIVECTOR_TITLE,   # 1.0 (наивысший приоритет)
        "summary": config.W_MULTIVECTOR_SUMMARY,  # 0.8
        "page": 0.5,                            # fallback для legacy / kind="page"
        "tag": config.W_MULTIVECTOR_TAG,       # 0.6 (низший приоритет)
        "chunk": 0.5,                           # S2.5.8: эмбеддинги на чанки
    }

    vecs = None
    if api_state() == "degraded":
        logger.warning("[BREAKER] degraded — keyword-only")
        # ── metrics: search_fallback_total ─────────────────────────────
        try:
            from wiki_v2 import metrics as _m
            _m.inc("search_fallback_total")
        except Exception:
            pass
    else:
        # Убедиться, что embed-модель готова через единый фасад gateway
        # (no-op для nvidia/llamaserver — LM Studio не грузим под облако/CPU).
        try:
            ensure_embed_ready()
        except Exception:
            pass  # fail-open: не блокируем поиск, embed сам попробует
        try:
            vecs = embed(variants, input_type="query")
        except Exception:
            vecs = None
            # ── metrics: search_fallback_total ───────────────────────────
            try:
                from wiki_v2 import metrics as _m
                _m.inc("search_fallback_total")
            except Exception:
                pass

    # По-видовому RRF с весами: {slug: weighted_score}
    # - "tag:<topic>" из БД группируем в единый тег-канал (kind="tag")
    # - legacy "page" обрабатываем как title-level (вес title), не ослабляя
    # - для каждого slug берём МАКСИМУМ по kind (а не сумму!) — спека S2.5.5:
    #   score = max(title×w_t, summary×w_s, tag×w_g). Сумма награждала страницу
    #   за количество векторов и размывала топ-1 (причина MRR 0.306).
    kind_scores = {}  # {kind: {slug: rrf_score}} (kind: title|summary|tag)
    if vecs and len(vecs) == len(variants):
        embeddings_by_kind = db.get_all_embeddings_by_kind()
        # S2.5.5.1: хаб-коррекция. h(p) — характеристика страницы (кэш по сигнатуре).
        # score'(q,p)=cos(q,p)−β·h(p) снимает «бонус за количество векторов» у хаб-страниц.
        hubness = get_hubness(embeddings_by_kind)
        # Фикс 2026-08-24 (specfix): обе чанк-семьи видны в поиске.
        # Индексатор пишет chunk:N, а искался только префикс page_chunk: —
        # свежие чанк-векторы не участвовали в ранжировании. Семьи tag/chunk
        # собирают ВСЕ векторы страницы; скор — по лучшему вектору
        # (_rank_multi_vec), вместо перезаписи последней копии dict.update().
        scalar_buckets = {}   # {title|summary|...: {slug: vec}}
        multi_stores = {"tag": {}, "chunk": {}}  # {slug: [vec, ...]}
        for _k, _kvecs in embeddings_by_kind.items():
            if _k in ("page", "title"):
                scalar_buckets.setdefault("title", {}).update(_kvecs)
            elif _k == "summary":
                scalar_buckets.setdefault("summary", {}).update(_kvecs)
            elif _k.startswith("tag"):
                for _sl, _vv in _kvecs.items():
                    multi_stores["tag"].setdefault(_sl, []).append(_vv)
            elif _k.startswith(("chunk:", "page_chunk:", "session_chunk:")):
                for _sl, _vv in _kvecs.items():
                    multi_stores["chunk"].setdefault(_sl, []).append(_vv)
            else:
                scalar_buckets.setdefault(_k, {}).update(_kvecs)
        for v in vecs:
            qn = np.array(v, dtype=np.float32)
            for kind, w in kind_weights.items():
                if kind in multi_stores:
                    store = multi_stores[kind]
                    if not store:
                        continue
                    ranked = _rank_multi_vec(qn, store, hubness, k=k)
                else:
                    kvecs = scalar_buckets.get(kind, {})
                    if not kvecs:
                        continue
                    ranked = [s for s, _ in top_k_cosine_hub(qn, kvecs, hubness, k=k)]
                if ranked:
                    for rank, rslug in enumerate(ranked, start=1):
                        sc = (1.0 / (60 + rank)) * w
                        prev = kind_scores.get(kind, {}).get(rslug, 0.0)
                        if sc > prev:  # максимум по kind (и по вариантам QE)
                            kind_scores.setdefault(kind, {})[rslug] = sc

    # объединяем per-kind скоры в единый {slug: combined_score}
    # берём МАКСИМУМ по kind для каждого slug (не сумму) — один лучший аспект
    semantic_scores = {}
    for scores in kind_scores.values():
        for slug, sc in scores.items():
            if sc > semantic_scores.get(slug, 0.0):
                semantic_scores[slug] = sc

    # BM25-ранг (источник "keyword") — честный keyword-аналог вместо порогов; fail-open
    try:
        bm25_rank = _bm25_rank(query, pages)
    except Exception:
        bm25_rank = []

    # объединяем semantic + BM25 через RRF (_hybrid_merge принимает rank-lists)
    # semantic_rank_list — slugs упорядочены по убыванию weighted score (для RRF)
    if semantic_scores:
        semantic_rank_list = [s for s, _ in sorted(semantic_scores.items(), key=lambda x: -x[1])]
    else:
        semantic_rank_list = []
    vector_ranks_list = [semantic_rank_list] if semantic_rank_list else []
    ordered = _hybrid_merge(vector_ranks_list, bm25_rank, k=k)

    # источник: semantic если в векторных, иначе keyword (BM25)
    vector_set = set(semantic_scores.keys())
    hits = {}
    for slug, score in ordered:
        src = "semantic" if slug in vector_set else "keyword"
        if src == "semantic":
            # Фикс 2026-08-24: порядок по ФЬЮЖН-скору (semantic ⊕ BM25),
            # а не по изолированному semantic — иначе согласие каналов
            # (например BM25 #1) не влияет на итоговый топ.
            hits[slug] = (score, src)
        else:
            hits[slug] = (min(score, MAX_KEYWORD_SCORE), src)

    # S2.5.6: confidence-вес к semantic-хитам.
    # final_score *= (1 + confidence * CONFIDENCE_WEIGHT); keyword-хиты не трогаем.
    for slug, (score, src) in hits.items():
        if src == "semantic":
            conf = pages.get(slug, {}).get("confidence")
            if conf is None or not isinstance(conf, (int, float)):
                conf = 0.5
            hits[slug] = (score * (1 + conf * config.CONFIDENCE_WEIGHT), src)

    # S2.5.13: фактор свежести — кривая забывания Эббингауза (спад confidence).
    # Если WIKI_FORGET_ENABLED → применяем forgetting_factor; иначе fallback на RECENCY_BONUS.
    for slug, (score, src) in hits.items():
        if src == "semantic":
            page = pages.get(slug, {})
            updated = page.get("updated")
            decay = forgetting_factor(updated, confidence=page.get("confidence", 0.5))
            new_score = score * decay
            # fallback: если forget отключён и факт свежий — мягкий бонус
            if not config.WIKI_FORGET_ENABLED and updated is not None:
                age_days = (time.time() - updated) / 86400.0
                if age_days < config.RECENCY_DAYS:
                    new_score = score * (1 + config.RECENCY_BONUS)
            hits[slug] = (new_score, src)

    # S2.5.7: BFS-расширение кандидатов по графу связей (fail-open)
    try:
        _, links_dict = db.get_graph()
        if links_dict and hits:
            start = list(hits.keys())
            extra = bfs(start, links_dict, depth=2)
            for slug in extra:
                if slug in pages and slug not in hits:
                    hits[slug] = (0.20, "graph")  # ниже semantic/keyword
    except Exception:
        pass  # fail-open: граф не должен ронять поиск

    db.close()
    ranked = sorted(hits.items(), key=lambda x: -x[1][0])[:k]

    # ── Ф0.5.1: событие поиска в wiki_search_events.jsonl (fail-open) ──────
    # ⚠️ ranked = [(slug, score, src)] — индексация [0][0]/[0][1]/[0][2]
    try:
        from wiki_v2 import events as _ev
        _ev.log_event(
            query,
            hits=len(ranked),
            top_slug=ranked[0][0] if ranked else "",
            top_score=ranked[0][1] if ranked else 0.0,
            context_chars=0,
            duration_ms=(time.time() - _t0) * 1000,
            source=ranked[0][2] if ranked else "",
            session_id="",
        )
    except Exception:
        pass  # fail-open: события не должны ронять поиск

    # ── Ф0.5.2: длительность поиска в метрики (fail-open) ──────────────────
    try:
        from wiki_v2 import metrics as _m
        _m.record("search_duration_ms", (time.time() - _t0) * 1000)
    except Exception:
        pass  # fail-open

    return [(s, sc, src) for s, (sc, src) in ranked], pages


def synthesize(query: str, slugs: list, pages: dict) -> str:
    """Синтез ответа из релевантных чанков страниц (S2.5.10).

    Вместо чтения всей страницы читает только релевантные query чанки,
    суммарно ≤ WIKI_CONTEXT_MAX_LEN (2000). Fail-open: ошибка → начало файла.
    """
    import numpy as _np

    from wiki_v2.chunker import page_intro, split_text_spans, trim_meta_blocks  # noqa: E501 local to avoid circular
    from wiki_v2.embed import top_k_cosine
    from wiki_v2.index_db import IndexDB

    texts = []
    budget = config.WIKI_CONTEXT_MAX_LEN  # 2000
    query_words = {w.lower() for w in query.split() if len(w) > 3}

    # чанк-эмбеддинги по slug: {slug: {kind(page_chunk:N): vector}}
    try:
        _db = IndexDB(INDEX_DB)
        _chunks_by_slug = {}
        for _k, _m in _db.get_all_embeddings_by_kind().items():
            if _k.startswith("page_chunk:"):
                for _sl, _v in _m.items():
                    _chunks_by_slug.setdefault(_sl, {})[_k] = _v
        _db.close()
    except Exception:
        _chunks_by_slug = {}

    for slug in slugs:
        path = pages[slug]["path"]
        try:
            with open(path, encoding="utf-8") as f:
                full = f.read()
            spans = split_text_spans(full)
            relevant = []
            # S2.5.8d/e: если есть чанк-эмбеддинги — семантический отбор по косинусу с запросом
            _cv = _chunks_by_slug.get(slug)
            if _cv:
                try:
                    qv = embed([query], input_type="query")
                    if qv:
                        qvec = _np.array(qv[0], dtype=_np.float32)
                        ranked = top_k_cosine(qvec, {k: v for k, v in _cv.items()}, k=len(_cv))
                        # Один самый релевантный СОДЕРЖАТЕЛЬНЫЙ чанк на страницу:
                        # облака тегов пропускаются (они для поиска и карты ссылок).
                        for kind, _sc in ranked:
                            idx = kind.split(":", 1)[1]
                            try:
                                i = int(idx)
                            except ValueError:
                                continue
                            if 0 <= i < len(spans):
                                s, e = spans[i]
                                cand = trim_meta_blocks(full[s:e])
                                if cand:
                                    intro = page_intro(full)
                                    if intro and intro.lower() not in cand.lower():
                                        relevant = [intro, cand]
                                    else:
                                        relevant = [cand]
                                    break
                except Exception:
                    relevant = []  # fallback ниже
            # fallback: чанк-эмбеддингов нет или embed упал → keyword-match (как было)
            if not relevant:
                for i, (s, e) in enumerate(spans):
                    c = full[s:e]
                    if query_words and any(w in c.lower() for w in query_words):
                        relevant = [c]
                        break
            # fallback: нет релевантных → начало файла
            if not relevant:
                relevant = [full[:budget]]
            # ограничить суммарный размер по бюджету
            text_ctx = ""
            for c in relevant:
                if len(text_ctx) + len(c) > budget:
                    text_ctx += c[:budget - len(text_ctx)]
                    break
                text_ctx += c
        except Exception:
            # fail-open: ошибка чтения → начало файла 2000 симв
            try:
                with open(path, encoding="utf-8") as f:
                    full = f.read()
                text_ctx = full[:budget]
            except Exception:
                text_ctx = ""
        texts.append(f"--- Страница: {pages[slug]['title']} ---\n{text_ctx}")

    if not texts:
        return ""
    prompt = (f"Вопрос пользователя: {query}\n\n"
              f"Вот страницы из базы знаний:\n{''.join(texts)}\n\n"
              "Ответь на вопрос, опираясь ТОЛЬКО на факты из этих страниц.\n"
              "ПРАВИЛА:\n"
              "1. Если в предоставленных страницах НЕТ ответа — прямо скажи, что информации не хватает. НЕ выдумывай.\n"
              "2. НЕ описывай содержимое страниц, которых нет в списке, и не додумывай детали по названиям/заголовкам.\n"
              "3. В конце кратко укажи, из каких страниц взят ответ (названия страниц).\n"
              "4. Отвечай подробно и по существу, на русском языке.")
    return chat_completion(
        "Ты — точный ассистент по базе знаний. Отвечаешь строго по предоставленным фактам, "
        "не галлюцинируешь, честно признаёшь нехватку информации. Работаешь на русском.",
        prompt, max_tokens=4000) or ""


def main():
    query = " ".join(sys.argv[1:])
    if not query:
        print("Usage: search.py <query>")
        return
    print(f"🔍 {query}")
    hits, pages = search(query)
    if not hits:
        print("❌ Ничего не найдено в wiki (ни семантика, ни ключевые слова)")
        return
    for slug, score, src in hits:
        print(f"  [{src} {score:.2f}] {pages[slug]['title']}")
    print("\n🤖 Синтез ответа...")
    answer = synthesize(query, [s for s, _, _ in hits], pages)
    print(f"\n📖 Ответ:\n{answer}" if answer else "⚠️ Nemotron не ответил")


if __name__ == "__main__":
    main()
        