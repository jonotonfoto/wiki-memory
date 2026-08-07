# tests/test_extract.py
import json
from unittest.mock import patch
from wiki_v2.extract import extract_content, clean_json

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
