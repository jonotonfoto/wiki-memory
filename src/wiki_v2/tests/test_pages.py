# tests/test_pages.py
import os
from wiki_v2.pages import render_page, parse_page, merge_content, find_merge_target


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
