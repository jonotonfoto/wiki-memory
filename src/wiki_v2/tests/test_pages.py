# tests/test_pages.py
from wiki_v2.pages import (
    find_merge_target,
    find_semantic_merge_target,
    merge_content,
    parse_page,
    render_page,
)


def test_render_and_parse_roundtrip(tmp_path):
    content = {
        "summary": "Тестовое саммари страницы для проверки рендера.",
        "key_topics": ["тема"], "decisions": ["решение"],
        "facts": ["факт"], "links": [], "entities": ["nvidia"],
        "concepts": [], "quality": "ok",
    }
    md = render_page("Тест", content, date_str="2026-08-03",
                     sources=["sess-1"])
    parsed = parse_page(md)
    assert parsed["title"] == "Тест"
    assert "факт" in parsed["facts"]
    assert parsed["sources"] == ["sess-1"]


def test_merge_dedupes_and_appends():
    old = {"facts": ["a", "b"], "decisions": ["d1"], "key_topics": ["t1"],
           "entities": [], "concepts": [], "links": []}
    new = {"facts": ["b", "c"], "decisions": ["d2"], "key_topics": ["t1", "t2"],
           "entities": ["x"], "concepts": [], "links": []}
    merged = merge_content(old, new)
    assert merged["facts"] == ["a", "b", "c"]
    assert merged["decisions"] == ["d1", "d2"]
    assert merged["key_topics"] == ["t1", "t2"]
    assert merged["entities"] == ["x"]


def test_find_merge_target_by_topic_overlap():
    candidates = [
        {"slug": "oil", "title": "Цены на нефть", "key_topics": ["нефть", "brent"]},
        {"slug": "kids", "title": "Дети в СПб", "key_topics": ["дети"]},
    ]
    new_topics = ["нефть", "цены"]
    target = find_merge_target(new_topics, candidates, threshold=0.34)
    assert target == "oil"


def test_find_merge_target_none_when_no_overlap():
    candidates = [{"slug": "a", "title": "A", "key_topics": ["x", "y"]}]
    assert find_merge_target(["zzz"], candidates, threshold=0.34) is None


def test_find_merge_target_by_title_and_topics():
    """3.2: одинаковый title + пересекающиеся теги → merge (title как бонус)."""
    candidates = [
        {"slug": "b-1", "title": "Прочитай бриф", "key_topics": ["дашборд", "чарты"]},
        {"slug": "other", "title": "Другое", "key_topics": ["другое"]},
    ]
    # теги пересекаются (дашборд в обоих) + title совпадает → merge в b-1
    target = find_merge_target(["дашборд", "график"], candidates, threshold=0.2, new_title="прочитай бриф")
    assert target == "b-1"


def test_find_merge_target_title_only_no_merge():
    """3.2: одинаковый title, НО разные темы (теги не пересеклись) → НЕ сливать.

    Это кейс 4 страниц «Прочитай бриф про dashboard_charts/status/dashboard_ts/config»:
    одинаковый префикс title, но разные модули — их нельзя схлопывать в одну.
    """
    candidates = [
        {"slug": "charts", "title": "Прочитай бриф", "key_topics": ["dashboard_charts"]},
    ]
    # новые теги про совсем другой модуль (config) — title совпал бы, но темы разные
    target = find_merge_target(["config", "load_env_file"], candidates, threshold=0.2, new_title="Прочитай бриф")
    assert target is None, "одинаковый title при разных темах НЕ должен сливать"


def test_find_merge_target_title_no_match_then_tags():
    """3.2: new_title не совпал → возвращается результат по тегам (старая логика)."""
    candidates = [
        {"slug": "oil", "title": "Цены на нефть", "key_topics": ["нефть", "brent"]},
        {"slug": "kids", "title": "Дети в СПб", "key_topics": ["дети"]},
    ]
    # new_title ни с чем не совпал, но теги (нефть) пересекаются → oil
    target = find_merge_target(["нефть", "цены"], candidates, threshold=0.34, new_title="Нет такого")
    assert target == "oil"


def test_find_merge_target_backwards_compatible():
    """3.2: вызов БЕЗ new_title возвращает то же, что и раньше (по тегам)."""
    candidates = [
        {"slug": "oil", "title": "Цены на нефть", "key_topics": ["нефть", "brent"]},
    ]
    assert find_merge_target(["нефть", "brent"], candidates, threshold=0.34) == "oil"
    assert find_merge_target(["zzz"], candidates, threshold=0.34) is None


def test_semantic_merge_matches_similar():
    """S2.5.14: cosine>порог → сливается в похожую страницу."""
    import numpy as np
    vec = np.array([1.0, 0.0, 0.0])
    vecs = {"similar": np.array([0.99, 0.01, 0.0]), "other": np.array([0.1, 0.9, 0.0])}
    assert find_semantic_merge_target(vec, vecs) == "similar"


def test_semantic_merge_none_for_unrelated():
    """S2.5.14: cosine<порог → не сливается (None)."""
    import numpy as np
    vec = np.array([0.0, 1.0, 0.0])
    vecs = {"a": np.array([1.0, 0.0, 0.0])}  # cosine ~0
    assert find_semantic_merge_target(vec, vecs) is None


def test_semantic_merge_none_vector():
    """S2.5.14: None-вектор → None (fail-open, не сливаем)."""
    assert find_semantic_merge_target(None, {"a": "x"}) is None

