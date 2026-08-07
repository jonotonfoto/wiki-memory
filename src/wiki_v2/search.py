"""Wiki search v2: embeddings + keywords, LLM synthesis only on hits."""
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiki_v2 import config
from wiki_v2.embed import top_k_cosine
from wiki_v2.index_db import IndexDB
from wiki_v2.nvidia_client import chat_completion, embed

WIKI_PATH = str(config.WIKI_PATH)
INDEX_DB = os.path.join(WIKI_PATH, ".index_v2.db")
MIN_SEMANTIC_SCORE = 0.40
# Keyword-результаты никогда не выше этого (semantic выигрывает)
MAX_KEYWORD_SCORE = 0.35
# Минимальный keyword-скор, иначе мусор («расскажи что-нибудь»)
MIN_KEYWORD_SCORE = 0.30
# Короткие запросы («привет», «ок») не ищем — только мусор находят
MIN_QUERY_LEN = 15
TOP_K = 5


def _normalize(text):
    text = text.lower()
    # Только ё→е (и мягкий/твёрдый знаки) — буква «э» НЕ заменяется,
    # иначе «это» превращается в «ето» и ломает русский поиск
    for old, new in {"ё": "е", "ъ": "", "ь": ""}.items():
        text = text.replace(old, new)
    return text


def keyword_hits(query: str, pages: list, k: int = 5):
    words = {w for w in re.findall(r"\w{3,}", _normalize(query))}
    scored = []
    for p in pages:
        hay = _normalize(f"{p['title']} {p['summary']}")
        # Добавляем содержимое страницы — факты и решения внутри неё
        path = p.get("path", "")
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    hay += " " + _normalize(f.read())
            except (OSError, UnicodeDecodeError):
                pass
        score = sum(1 for w in words if w in hay)
        if score:
            scored.append((p["slug"], score / max(len(words), 1)))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]

def search(query: str, k: int = TOP_K):
    """Return (hits, pages_by_slug). hits: [(slug, score, source)]."""
    # Короткий запрос — не ищем (только мусор находит)
    if not query or len(query.strip()) < MIN_QUERY_LEN:
        return [], {}

    db = IndexDB(INDEX_DB)
    pages = {p["slug"]: p for p in db.all_pages()}
    if not pages:
        db.close()
        return [], {}

    hits = {}

    # Tier 1: semantic
    vecs = embed([query], input_type="query")
    if vecs:
        for slug, score in top_k_cosine(np.array(vecs[0], dtype=np.float32), db.get_all_embeddings(),
                                        k=k, min_score=MIN_SEMANTIC_SCORE):
            hits[slug] = (score, "semantic")

    # Tier 2: keywords (merge, keyword score capped so semantic wins ties)
    for slug, score in keyword_hits(query, list(pages.values()), k=k):
        capped = min(score * 0.5, MAX_KEYWORD_SCORE)
        if capped < MIN_KEYWORD_SCORE:
            continue  # слишком слабое совпадение — мусор
        if slug not in hits:
            hits[slug] = (capped, "keyword")

    db.close()
    ranked = sorted(hits.items(), key=lambda x: -x[1][0])[:k]
    return [(s, sc, src) for s, (sc, src) in ranked], pages


def synthesize(query: str, slugs: list, pages: dict) -> str:
    texts = []
    for slug in slugs:
        path = pages[slug]["path"]
        if os.path.exists(path):
            with open(path) as f:
                texts.append(f"--- Страница: {pages[slug]['title']} ---\n{f.read()}")
    if not texts:
        return ""
    prompt = (f"Вопрос пользователя: {query}\n\n"
              f"Вот страницы из базы знаний:\n{''.join(texts)}\n\n"
              "Найди релевантную информацию и дай подробный ответ на русском языке. "
              "Опирайся только на факты из страниц.")
    return chat_completion(
        "Ты помощник, ищущий информацию в базе знаний. Отвечай подробно и по существу.",
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
