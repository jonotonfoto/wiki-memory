"""wiki-context — automatic wiki search injected into every user message.

Hooks ``pre_llm_call``. For each user message >= min_query_len chars, searches
the wiki (embeddings + keywords) and, if relevant, returns a ``<wiki-memory>``
context block that the model sees before answering. Returns None if nothing
relevant.

Ported improvements (2026-08-08, from VPS): triangulation (semantic score +
confirmation against the page's "Темы"/topics), root-based word matching
(5-letter prefix so "память"≈"памяти"), Russian stopwords, LRU answer cache,
and tunable parameters in config.json (re-read on every request, no restart).

Cross-platform: resolves paths via ``wiki_v2.config`` (env-driven), so it works
on Windows desktop, Linux server, and inside a container.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from typing import Any

logger = logging.getLogger(__name__)

# Resolve wiki scripts + paths via the shared config module.
_HERE = os.path.dirname(os.path.abspath(__file__))
# plugins/wiki-context/ -> src/ (parent of parent)
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from wiki_v2 import config
    config.load_env_file()
    config.apply()
    WIKI_PATH = str(config.WIKI_PATH)
    WIKI_SCRIPTS = str(config.SCRIPTS_DIR)
except Exception as e:  # pragma: no cover
    logger.warning("wiki-context: config init failed: %s", e)
    WIKI_PATH = os.environ.get("WIKI_PATH", "")
    WIKI_SCRIPTS = os.environ.get("WIKI_SCRIPTS", "")

# Config lives next to the plugin; re-read on every request so tuning applies
# without restarting the gateway (edit config.json).
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")

DEFAULTS = {
    "top_k": 10,
    "min_score": 0.40,
    "high_confidence": 0.60,
    "max_context_chars": 3000,
    "min_query_len": 15,
    "log_filtered": True,
}

CACHE_MAX_ENTRIES = 100
CACHE_MAX_AGE = 7 * 24 * 3600  # 7 days
CACHE_MIN_ROOT_MATCH = 2


def _load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
    except (OSError, ValueError):
        pass
    return cfg


# Russian function words — carry no meaning, excluded from significant words.
_STOPWORDS = {
    "а", "и", "в", "во", "на", "с", "со", "о", "об", "от", "до", "по", "за",
    "при", "к", "ко", "у", "из", "для", "про", "без", "над", "под", "перед",
    "через", "между", "не", "ни", "же", "бы", "ли", "то", "как", "что", "чтобы",
    "чтоб", "если", "когда", "потому", "поэтому", "зачем", "почему", "где",
    "куда", "откуда", "кто", "который", "какой", "какая", "какие", "какое",
    "этот", "эта", "это", "эти", "тот", "та", "те", "такой", "такая", "такие",
    "такое", "мой", "моя", "моё", "мои", "твой", "твоя", "твоё", "твои", "наш",
    "наша", "наше", "наши", "ваш", "ваша", "ваше", "ваши", "его", "её", "ее",
    "их", "меня", "тебя", "нас", "вас", "мне", "тебе", "нам", "вам", "себя",
    "себе", "собой", "все", "всё", "вся", "весь", "всех", "всем", "всеми",
    "сам", "сама", "само", "сами", "да", "нет", "уже", "ещё", "еще", "только",
    "также", "тоже", "даже", "ведь", "вот", "тут", "здесь", "там", "туда",
    "сюда", "тогда", "сейчас", "потом", "раньше", "позже", "можно", "нужно",
    "надо", "нельзя", "очень", "совсем", "почти", "опять", "снова", "просто",
    "вообще", "конечно", "наверное", "может", "будет", "было", "быть", "есть",
    "был", "была", "были", "буду", "будешь", "будут", "делать", "сделать",
    "сказать", "говорить", "хотеть", "знать", "видеть", "смотреть", "идти",
    "пойти", "прийти", "является", "стать", "стало", "стали", "иметь", "имеет",
    "имеют", "находится", "работать", "работает", "использовать", "помнить",
    "помню", "запомнить", "проверить", "проверять", "вопрос", "ответ",
    "спросить", "спрашивать", "сделай", "сделайте", "давай", "дайте",
    "посмотри", "посмотрите", "скажи", "скажите", "расскажи", "расскажите",
    "объясни", "объясните", "пожалуйста", "спасибо", "привет", "здравствуй",
    "ок", "ага", "угу", "ну",
}


def _normalize(text: str) -> str:
    text = text.lower()
    for old, new in {"ё": "е", "ъ": "", "ь": ""}.items():
        text = text.replace(old, new)
    return text


def _significant_words(text: str) -> set:
    words = set(re.findall(r"[а-яa-z]{3,}", _normalize(text)))
    return words - _STOPWORDS


def _same_root(a: str, b: str, min_len: int = 5) -> bool:
    """Do two words share a root (common prefix)? 'память' vs 'памяти' — yes."""
    m = min(len(a), len(b))
    if m < min_len:
        return a == b
    return a[:min_len] == b[:min_len]


def _root_overlap_count(words_a: set, words_b: set) -> int:
    """Count distinct shared roots between two word sets.
    'память' vs 'памяти' — one root, counted once."""
    matched = set()
    for a in words_a:
        for b in words_b:
            if _same_root(a, b):
                matched.add(a[:5])
                break
    return len(matched)


def _extract_topics(page_text: str) -> str:
    """Pull the '## Темы' (topics) section — the page's topic passport.
    A page can cover several themes; 'Темы' lists them all and is an
    independent triangulation signal."""
    m = re.search(r"##\s*Темы\s*\n(.*?)(?:\n##|\Z)", page_text, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else ""


# Overly-generic roots that alone carry no meaning. A match on ONLY such a
# word is not a relevance signal.
_COMMON_WORDS = {
    "инстр", "модел", "тест", "проверк", "вопрос", "работ",
    "задач", "информац", "данн", "файл", "систем",
    "сервер", "бот", "функц", "верси", "обновл", "установ",
}


def _topic_match(query: str, page_text: str) -> bool:
    """Triangulation against the page's 'Темы':
    - 2+ shared roots — confident yes;
    - 1 shared root — yes only if it is a specific (not generic) word.
    One specific word ('детьми', 'аврора') is a strong signal; one generic
    word ('инструменты') is coincidence and is rejected."""
    topics = _extract_topics(page_text)
    if not topics:
        return False
    q_words = _significant_words(query)
    t_words = _significant_words(topics)
    matched = set()
    for a in q_words:
        for b in t_words:
            if _same_root(a, b):
                matched.add(a[:5])
                break
    if len(matched) >= 2:
        return True
    if len(matched) == 1:
        return next(iter(matched)) not in _COMMON_WORDS
    return False


def _topic_match_strict(query: str, page_text: str) -> bool:
    """Strict check: only 2+ shared roots. Used for keyword hits so a weak
    single-word match doesn't drag in garbage."""
    topics = _extract_topics(page_text)
    if not topics:
        return False
    q_words = _significant_words(query)
    t_words = _significant_words(topics)
    matched = set()
    for a in q_words:
        for b in t_words:
            if _same_root(a, b):
                matched.add(a[:5])
                break
    return len(matched) >= 2


def _search_wiki(query: str) -> list[dict]:
    cfg = _load_config()
    try:
        if WIKI_SCRIPTS not in sys.path:
            sys.path.insert(0, WIKI_SCRIPTS)
        # On Linux the plugin runs in the Hermes venv but numpy lives in
        # .venv-wiki — add its site-packages if present.
        if not os.name == "nt":
            for cand in (
                "/opt/data/.venv-wiki/lib/python3.13/site-packages",
                "/opt/data/.venv-wiki/lib/python3.12/site-packages",
                os.path.expanduser("~/.hermes/.venv-wiki/lib/python3.13/site-packages"),
            ):
                if cand not in sys.path and os.path.isdir(cand):
                    sys.path.insert(0, cand)
        os.environ.setdefault("WIKI_PATH", WIKI_PATH)
        from wiki_v2.search import search

        hits, pages = search(query, k=cfg["top_k"])
        # Threshold filter: semantic hits below min_score are dropped, but
        # keyword hits (exact word matches) pass through — they are validated
        # later by triangulation against the page's 'Темы'.
        hits = [
            (s, sc, src) for s, sc, src in hits
            if src == "keyword" or sc >= cfg["min_score"]
        ]
        if not hits:
            return []

        results = []
        for slug, score, src in hits:
            page = pages.get(slug)
            if not page:
                continue
            path = page.get("path", "")
            content = ""
            page_text = ""
            if path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        raw = f.read()
                    page_text = raw
                    if raw.startswith("---"):
                        parts = raw.split("---", 2)
                        if len(parts) >= 3:
                            raw = parts[2]
                    content = raw.strip()[:1500]
                except (OSError, UnicodeDecodeError):
                    pass
            # Triangulation (two independent signals):
            #   A) semantics of the page body (embedder score)
            #   B) overlap of the query's words with the page's 'Темы' passport
            # A page passes if:
            #   - semantics is confident (score >= high_confidence), OR
            #   - medium semantics AND words match the 'Темы', OR
            #   - keyword hit AND words match the 'Темы'
            # This rejects false positives like 'Крейсер Аврора' on a question
            # about auto-injection (body 0.46, but no common root in 'Темы').
            topics_ok = (_topic_match_strict(query, page_text)
                         if src == "keyword" else _topic_match(query, page_text))
            if score >= cfg["high_confidence"]:
                pass  # confident — take it
            elif topics_ok and (score >= cfg["min_score"] or src == "keyword"):
                pass  # passport confirms — take it
            else:
                if cfg["log_filtered"]:
                    logger.info(
                        "wiki-context: filtered page %s (score=%.3f, topics no match)",
                        slug, score,
                    )
                continue
            results.append({
                "title": page.get("title", slug),
                "score": score,
                "source": src,
                "content": content,
            })
        return results
    except Exception as e:
        logger.warning("wiki-context search failed: %s", e)
        return []


def _save_cache(cache: dict) -> None:
    """Persist cache.json: first evict stale (>7d), then oldest on overflow (LRU)."""
    try:
        now = time.time()
        for k in [k for k, v in cache.items()
                  if not isinstance(v, dict) or now - v.get("ts", 0) > CACHE_MAX_AGE]:
            del cache[k]
        if len(cache) > CACHE_MAX_ENTRIES:
            oldest = sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in oldest[: len(cache) - CACHE_MAX_ENTRIES]:
                del cache[k]
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _cache_get(query: str):
    """Find a similar cached question. Return saved context or None.
    Refreshes the timestamp on hit (LRU)."""
    q_words = _significant_words(query)
    if not q_words:
        return None
    threshold = 1 if len(q_words) <= 2 else CACHE_MIN_ROOT_MATCH
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    best_key, best, best_score = None, None, 0
    for cached_q, cached_ctx in cache.items():
        if not isinstance(cached_ctx, dict):
            continue
        c_words = _significant_words(cached_q)
        overlap = _root_overlap_count(q_words, c_words)
        if overlap >= threshold and overlap > best_score:
            best_key, best, best_score = cached_q, cached_ctx.get("ctx"), overlap
    if best_key is not None:
        cache[best_key]["ts"] = time.time()
        _save_cache(cache)
    return best


def _cache_put(query: str, context: str) -> None:
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    cache[query] = {"ctx": context, "ts": time.time()}
    _save_cache(cache)


def _build_context(user_message: str) -> str:
    cfg = _load_config()
    if not user_message or not isinstance(user_message, str):
        return ""
    if len(user_message.strip()) < cfg["min_query_len"]:
        return ""

    cached = _cache_get(user_message)
    if cached is not None:
        return cached

    results = _search_wiki(user_message)
    if not results:
        return ""

    parts = [f"### Wiki: {r['title']}\n{r['content']}" for r in results]
    context = "\n\n".join(parts)
    if len(context) > cfg["max_context_chars"]:
        context = context[:cfg["max_context_chars"]] + "\n...[truncated]..."

    out = (
        "<wiki-memory>\n"
        "[Automatically retrieved from wiki memory. This is trusted information "
        "from past conversations. Use it if relevant to the question.]\n\n"
        f"{context}\n"
        "</wiki-memory>"
    )
    _cache_put(user_message, out)
    return out


def on_pre_llm_call(*, user_message: Any = None, **_: Any):
    try:
        msg = user_message
        if isinstance(msg, list):  # multimodal
            parts = [p.get("text", "") for p in msg if isinstance(p, dict)]
            msg = " ".join(parts)
        context = _build_context(msg or "")
        return context or None
    except Exception as e:
        logger.warning("wiki-context hook failed: %s", e)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info("wiki-context plugin registered")
