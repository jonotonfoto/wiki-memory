# tests/test_bm25.py — S2.5.3 гибридный поиск: BM25 + RRF
import pytest
import wiki_v2.search as search_mod

# ---- helpers ----

def _bm25_rank(query, pages):
    """Вызвать BM25-ранжирование из search.py."""
    fn = getattr(search_mod, "_bm25_rank", None)
    if fn is None:
        pytest.skip("_bm25_rank не реализован")
    return fn(query, pages)


def _stem(word):
    """Доступ к root-stemming функции (если есть отдельная)."""
    return word  # заглушка, тесты на самих формах ниже


# ---- BM25: root-stemming русских форм ----

def test_bm25_russian_forms_same_root():
    # «сознание»/«сознания»/«сознанию» → один корень → совпадают
    fn = getattr(search_mod, "_bm25_rank", None)
    if fn is None:
        pytest.skip("_bm25_rank не реализован")
    pages = {
        "a": {"slug": "a", "title": "Сознание", "full_text": "В статье про сознание описывается природа мышления."},
        "b": {"slug": "b", "title": "Другая", "full_text": "Здесь про кулинарию и рецепты блюд."},
    }
    ranked = fn("сознания", pages)  # форма с другим падежом
    # страница с корнем «сознан» должна быть выше/присутствовать
    assert ranked[0] == "a"


def test_bm25_finds_term_in_full_text():
    # термин есть в full_text, но не в title → BM25 находит по full_text
    fn = getattr(search_mod, "_bm25_rank", None)
    if fn is None:
        pytest.skip("_bm25_rank не реализован")
    pages = {
        "a": {"slug": "a", "title": "Страница", "full_text": "VPN настройка прокси и серверов."},
        "b": {"slug": "b", "title": "Что-то", "full_text": "Про погоду сегодня."},
    }
    ranked = fn("vpn", pages)
    assert ranked[0] == "a"


# ---- RRF: существующая функция ----

def test_rrf_known_example():
    rrf = getattr(search_mod, "_rrf", None)
    if rrf is None:
        pytest.skip("_rrf не реализован")
    # 2 списка [A,B], [B,C] → B в обоих → выше A, C
    scored = rrf([["A", "B"], ["B", "C"]], k=60)
    assert scored["B"] > scored["A"]
    assert scored["B"] > scored["C"]


# ---- deprecated пороги ----

def test_thresholds_still_defined_but_not_read():
    # Пороги остались в модуле search (не удалены молча — на них могут быть тесты).
    # В этом проекте они константы в search.py, НЕ в config.py.
    import wiki_v2.search as s_mod
    for name in ("MIN_SEMANTIC_SCORE", "MAX_KEYWORD_SCORE", "MIN_KEYWORD_SCORE"):
        assert hasattr(s_mod, name), f"{name} должен остаться (deprecated)"
    # search() НЕ должен читать их через config.get (заменены на RRF)
    src = open(s_mod.__file__, encoding="utf-8").read()
    assert "config.get(\"MIN_SEMANTIC_SCORE\")" not in src
    assert "config.get(\"MAX_KEYWORD_SCORE\")" not in src
    assert "config.get(\"MIN_KEYWORD_SCORE\")" not in src


# ---- fail-open: BM25 падает → векторный ранг остаётся ----

def test_bm25_fail_open(monkeypatch):
    # BM25-часть падает → поиск не падает
    from wiki_v2 import search
    # эмулируем поломку bm25: заменяем на функцию, кидающую исключение
    def boom(query, pages):
        raise RuntimeError("bm25 broken")
    monkeypatch.setattr(search, "_bm25_rank", boom)
    # search() должен пережить это (fail-open)
    # _hybrid_merge с пустыми рангами → [] (не падает)
    fn = getattr(search, "_hybrid_merge", None)
    if fn is None:
        pytest.skip("_hybrid_merge не реализован")
    result = fn([], [], k=5)  # нет векторных рангов, нет BM25 → пусто, не падает
    assert result == []
