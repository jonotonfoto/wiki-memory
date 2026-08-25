# tests/test_extract.py
import json
from unittest.mock import patch

from wiki_v2.extract import (
    clean_json,
    extract_content,
    validate_extract,
    _reset_llm_budget,
)

GOOD = json.dumps({
    "summary": "Пользователь чинил подключение немотрона через команду /model.",
    "key_topics": ["немотрон", "/model"],
    "decisions": ["удалить NVIDIA_BASE_URL"],
    "facts": ["правильный эндпоинт integrate.api.nvidia.com/v1"],
    "links": [], "entities": ["nvidia", "nemotron"], "concepts": ["алиас"]
}, ensure_ascii=False)

def test_clean_json_strips_markdown_fence():
    fenced = "```json\n{\"a\": 1}\n```"
    assert clean_json(fenced) == {"a": 1}

def test_clean_json_invalid_returns_none():
    assert clean_json("not json at all") is None

def test_extract_good_first_try():
    with patch("wiki_v2.extract.chat_completion", return_value=GOOD):
        out = extract_content("title", "conversation text here")
    assert out["summary"].startswith("Пользователь чинил")
    assert out["quality"] == "ok"

def test_extract_garbage_then_retry_ok():
    garbage = json.dumps({"summary": "П о л ь з в а т е", "key_topics": [],
                          "decisions": [], "facts": [], "links": [],
                          "entities": [], "concepts": []}, ensure_ascii=False)
    with patch("wiki_v2.extract.chat_completion",
               side_effect=[garbage, GOOD]) as m:
        out = extract_content("t", "text" * 100)
    assert out["quality"] == "ok"
    assert m.call_count == 2

def test_extract_all_garbage_falls_back():
    with patch("wiki_v2.extract.chat_completion", return_value="мусор"):
        out = extract_content("Мой заголовок", "👤: привет как дела\n🤖: нормально")
    assert out["quality"] == "fallback"
    assert "Мой заголовок" in out["summary"]

# --- S3.3 Tests for validate_extract ---

def test_validate_extract_valid():
    data = {
        "summary": "Valid summary of a conversation about configuring VPS",
        "key_topics": ["topic1", "topic2"],
        "decisions": ["decision1"],
        "facts": ["fact1"],
        "links": [],
        "entities": ["entity1"],
        "concepts": ["concept1"]
    }
    assert validate_extract(data) is True

def test_validate_extract_invalid_types():
    # summary not str
    assert validate_extract({"summary": 123}) is False
    # key_topics too long (> 20)
    assert validate_extract({
        "summary": "A sufficiently long summary that is not garbage",
        "key_topics": ["t"] * 21,
        "decisions": [], "facts": [], "links": [], "entities": [], "concepts": []
    }) is False
    # field not list
    assert validate_extract({"summary": "A sufficiently long summary that is not garbage", "key_topics": "not a list"}) is False
    # non-dict input
    assert validate_extract(["not", "a", "dict"]) is False

def test_validate_extract_quality():
    base = {
        "summary": "A sufficiently long summary that is not garbage", "key_topics": [], "decisions": [], 
        "facts": [], "links": [], "entities": [], "concepts": []
    }
    # quality ok
    assert validate_extract({**base, "quality": "ok"}) is True
    # quality fallback
    assert validate_extract({**base, "quality": "fallback"}) is True
    # invalid quality
    assert validate_extract({**base, "quality": "bad"}) is False


# --- S4.7: triplets do not break validate_extract ---

def test_validate_extract_with_triplets():
    """validate_extract должен возвращать True при наличии triplets (не в _FIELDS)."""
    data = {
        "summary": "A sufficiently long summary that is not garbage",
        "key_topics": [], "decisions": [], "facts": [], "links": [],
        "entities": [], "concepts": [],
        "triplets": [
            {"subject": "s1", "predicate": "p1", "object": "o1"},
            {"subject": "s2", "predicate": "p2", "object": "o2"}
        ]
    }
    assert validate_extract(data) is True


def test_validate_extract_with_empty_triplets():
    """validate_extract с пустым triplets — не ломается."""
    data = {
        "summary": "A sufficiently long summary that is not garbage",
        "key_topics": [], "decisions": [], "facts": [], "links": [],
        "entities": [], "concepts": [],
        "triplets": []
    }
    assert validate_extract(data) is True


def test_validate_extract_with_tuple_triplets():
    """validate_extract с triplets в формате кортежей — не ломается."""
    data = {
        "summary": "A sufficiently long summary that is not garbage",
        "key_topics": [], "decisions": [], "facts": [], "links": [],
        "entities": [], "concepts": [],
        "triplets": [["s1", "p1", "o1"], ["s2", "p2", "o2"]]
    }
    assert validate_extract(data) is True


# --- LLM Budget Tests (1.1) ---


def test_extract_budget_exhausted_falls_back():
    _reset_llm_budget()
    import wiki_v2.extract as ex
    ex._llm_calls = ex.EXTRACT_MAX_LLM_CALLS  # бюджет исчерпан
    with patch("wiki_v2.extract.chat_completion") as m:
        out = extract_content("Загол", "👤: текст")
    assert out["quality"] == "fallback"
    m.assert_not_called()  # LLM НЕ вызывался (сразу fallback)


def test_extract_counters_llm_calls():
    _reset_llm_budget()
    import wiki_v2.extract as ex
    # 2 вызова: первый мусор, второй GOOD → успех (temp 0.3, 0.1)
    with patch("wiki_v2.extract.chat_completion",
               side_effect=["мусор", GOOD]) as m:
        out = extract_content("t", "text" * 100)
    assert out["quality"] == "ok"
    assert m.call_count == 2
    assert ex._llm_calls == 2


def test_extract_reasoning_empty_returns_fallback():
    _reset_llm_budget()
    # chat_completion вернёт None (reasoning-empty при empty_reasoning_is_error=True)
    with patch("wiki_v2.extract.chat_completion", return_value=None) as m:
        out = extract_content("Заголовок", "👤: привет")
    assert out["quality"] == "fallback"
    # не более 2 вызовов (temp 0.3, 0.1) — без ретраев в самом extract_content
    assert m.call_count <= 2
    assert "Заголовок" in out["summary"]


# --- MAP Budget Tests (1.3) ---


def test_map_stops_when_budget_exhausted():
    import wiki_v2.extract as ex
    ex._reset_llm_budget()
    chunks = ["чанк 1", "чанк 2", "чанк 3", "чанк 4", "чанк 5"]
    with patch("wiki_v2.extract._llm_budget_exhausted", return_value=True):
        with patch("wiki_v2.extract.chat_completion") as m:
            out = ex.map_chunk_tags("Заголовок", chunks)
    # MAP не должен вызывать LLM (бюджет исчерпан) — чанки получают []
    m.assert_not_called()
    assert list(out.values()) == [[], [], [], [], []]


def test_map_counts_llm_calls_across_chunks():
    import wiki_v2.extract as ex
    ex._reset_llm_budget()
    chunks = ["чанк 1", "чанк 2", "чанк 3", "чанк 4", "чанк 5", "чанк 6", "чанк 7", "чанк 8"]
    # GOOD — валидный JSON-ответ для extract_content
    with patch("wiki_v2.extract.chat_completion", return_value=GOOD) as m:
        out = ex.map_chunk_tags("Заголовок", chunks)
    # Бюджет 6 → не больше 6 LLM-вызовов, сколько бы чанков ни было
    assert m.call_count <= ex.EXTRACT_MAX_LLM_CALLS


def test_map_chunk_one_returns_empty_on_budget_exhausted():
    import wiki_v2.extract as ex
    ex._reset_llm_budget()
    ex._llm_calls = ex.EXTRACT_MAX_LLM_CALLS
    with patch("wiki_v2.extract.extract_chunk_tags") as ec:
        result = ex._map_chunk_one("Заголовок", "текст чанка")
    assert result == []
    ec.assert_not_called()
