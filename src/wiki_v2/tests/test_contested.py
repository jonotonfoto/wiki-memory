# tests/test_contested.py — S2.5.6 merge_content contradictions + render_page contested flag
from wiki_v2.pages import merge_content, render_page


def test_merge_detects_contradiction():
    """merge_content детектирует противоречие: старый «LM Studio работает» vs новый «LM Studio НЕ работает»."""
    old = {
        "facts": ["LM Studio работает стабильно"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    new = {
        "facts": ["LM Studio НЕ работает с некоторыми моделями"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    merged = merge_content(old, new)
    assert merged["contested"] is True
    assert len(merged["contradictions"]) == 1
    # contradictions содержит оба факта
    assert "LM Studio работает" in merged["contradictions"][0]
    assert "LM Studio НЕ работает" in merged["contradictions"][0]


def test_merge_no_contradiction_same_fact():
    """merge_content без противоречия (тот же факт) → contested=False, contradictions=[]."""
    old = {
        "facts": ["Hermes Agent работает"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    new = {
        "facts": ["Hermes Agent работает"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    merged = merge_content(old, new)
    assert merged["contested"] is False
    assert merged["contradictions"] == []


def test_merge_no_contradiction_different_topic():
    """Разные темы — не противоречие (разный fact_key)."""
    old = {
        "facts": ["LM Studio работает стабильно"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    new = {
        "facts": ["OpenRouter быстрый API"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    merged = merge_content(old, new)
    assert merged["contested"] is False
    assert merged["contradictions"] == []


def test_merge_multiple_contradictions():
    """Несколько противоречий в разных фактах."""
    old = {
        "facts": ["LM Studio работает", "Claude быстрый"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    new = {
        "facts": ["LM Studio НЕ работает", "Claude медленный"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
    }
    merged = merge_content(old, new)
    assert merged["contested"] is True
    assert len(merged["contradictions"]) == 2


def test_render_page_contested_true(tmp_path):
    """render_page с contested=True → в frontmatter есть 'contested: true' и секция '## Противоречия'."""
    content = {
        "summary": "Тестовое саммари",
        "facts": ["факт"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
        "contested": True,
        "contradictions": ["старый: LM Studio работает | новый: LM Studio НЕ работает"],
        "quality": "ok",
    }
    md = render_page("Тест", content, date_str="2026-08-13", sources=["sess-1"])
    assert "contested: true" in md
    assert "## Противоречия" in md
    assert "LM Studio работает" in md
    assert "LM Studio НЕ работает" in md


def test_render_page_contested_false():
    """render_page с contested=False → нет 'contested' строки и секции."""
    content = {
        "summary": "Тестовое саммари",
        "facts": ["факт"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
        "contested": False,
        "contradictions": [],
        "quality": "ok",
    }
    md = render_page("Тест", content, date_str="2026-08-13", sources=["sess-1"])
    assert "contested: true" not in md
    assert "## Противоречия" not in md


def test_render_page_contested_missing_keys():
    """render_page без contested/contradictions ключей — не падает."""
    content = {
        "summary": "Тестовое саммари",
        "facts": ["факт"],
        "decisions": [], "key_topics": [], "links": [],
        "entities": [], "concepts": [],
        "quality": "ok",
    }
    md = render_page("Тест", content, date_str="2026-08-13", sources=["sess-1"])
    assert "contested: true" not in md
    assert "## Противоречия" not in md
