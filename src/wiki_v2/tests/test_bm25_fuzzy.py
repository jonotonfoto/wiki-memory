# tests/test_bm25_fuzzy.py — fuzzy-fallback в _bm25_rank: коррекция опечаток запроса
# («киркоров» → «керкоров»), 2026-08-25. Границы по длине: <5 не исправляем.
import wiki_v2.search as search_mod


def _rank(query, pages):
    return search_mod._bm25_rank(query, pages)


# ---- опечатка в фамилии: точного стемма нет, fuzzy находит ----

def test_bm25_fuzzy_typo_surname():
    pages = {
        "a": {"slug": "a", "title": "Мультфильм", "full_text": "Есть певец Филипп Керкоров и идея мультфильма."},
        "b": {"slug": "b", "title": "Другое", "full_text": "Заметки про индексацию и эмбеддинги."},
    }
    assert _rank("киркоров", pages)[0] == "a"


def test_bm25_fuzzy_both_words_typo():
    # «филип киркоров» против «Филипп Керкоров» — оба токена с опечаткой
    pages = {
        "a": {"slug": "a", "title": "Задача", "full_text": "Есть певец Филипп Керкоров, у него идея мультфильма."},
        "b": {"slug": "b", "title": "Привет", "full_text": "Просто приветствие без содержания."},
    }
    assert _rank("филип киркоров", pages)[0] == "a"


# ---- короткие слова НЕ исправляются (ложные совпадения «код»/«год») ----

def test_bm25_fuzzy_short_words_not_corrected():
    pages = {
        "a": {"slug": "a", "title": "Прочее", "full_text": "Наступил новый год и праздники."},
    }
    assert _rank("код", pages) == []


# ---- мусор без ближних кандидатов → пусто (fail-open поведение) ----

def test_bm25_fuzzy_garbage_no_match():
    pages = {
        "a": {"slug": "a", "title": "Страница", "full_text": "Про эмбеддинги и поиск."},
    }
    assert _rank("ыыыыыыыы", pages) == []


# ---- точное совпадение в словаре не проходит через fuzzy ----

def test_bm25_exact_beats_fuzzy():
    # запрос «керкоров» точно есть в корпусе → вес 1.0, страница выше
    # страницы с опечатанным ДОКУМЕНТНЫМ токеном (коррекция — только
    # на стороне запроса)
    pages = {
        "exact": {"slug": "exact", "title": "Точная", "full_text": "Концерт Керкоров анонсирован."},
        "fuzzy": {"slug": "fuzzy", "title": "Опечатка", "full_text": "Концерт Киркоров отменён."},
    }
    assert _rank("керкоров", pages)[0] == "exact"


# ---- юнит: веса терминов ----

def test_fuzzy_term_weights_mapping():
    df = {"керкор": 3, "филипп": 2}
    w = search_mod._fuzzy_term_weights({"киркор", "филип"}, df)
    assert w["керкор"] == search_mod.FUZZY_TERM_WEIGHT
    assert w["филипп"] == search_mod.FUZZY_TERM_WEIGHT


def test_fuzzy_term_weights_exact_and_no_candidate():
    df = {"поиск": 5}
    w = search_mod._fuzzy_term_weights({"поиск", "ыыыыыыы"}, df)
    assert w == {"поиск": 1.0, "ыыыыыыы": 1.0}  # нет кандидата → лишних ключей нет


# ---- левенштейн: базовые случаи ----

def test_levenshtein_basic():
    lv = search_mod._levenshtein
    assert lv("abc", "abc") == 0
    assert lv("керкоров", "киркоров") == 1
    assert lv("филип", "филипп") == 1
    assert lv("abc", "xyz") == 3


# ---- kill-switch: WIKI_FUZZY_BM25=0 отключает коррекцию ----

def test_fuzzy_disabled_env(monkeypatch):
    monkeypatch.setattr(search_mod, "FUZZY_BM25_ENABLED", False)
    pages = {
        "a": {"slug": "a", "title": "Мультфильм", "full_text": "Есть певец Филипп Керкоров."},
    }
    assert _rank("киркоров", pages) == []
