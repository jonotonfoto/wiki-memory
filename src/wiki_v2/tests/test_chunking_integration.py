"""Tests for Wiki chunking integration (S2.5.8c-e, S2.5.9, S2.5.10): chunk tags, embeds, search, map-reduce, smart read."""
import os
import tempfile
from unittest.mock import patch

from wiki_v2.extract import extract_chunk_tags, map_chunk_tags, reduce_chunk_tags
from wiki_v2.indexer import embed_chunks
from wiki_v2.search import synthesize


def test_extract_chunk_tags_returns_topics():
    """2.5.8c: чанк получает СВОИ теги (key_topics) через extract_content."""
    fake = {
        "summary": "Про психологию", "key_topics": ["выготский", "психология"],
        "decisions": [], "facts": [], "links": [], "entities": [], "concepts": [],
    }
    with patch("wiki_v2.extract.extract_content", return_value=fake):
        tags = extract_chunk_tags("Психология", "чанк про выготского")
    assert "выготский" in tags
    assert "психология" in tags


def test_extract_chunk_tags_fail_open():
    """2.5.8c: на ошибке extract -> [], не падает."""
    with patch("wiki_v2.extract.extract_content", side_effect=Exception("boom")):
        assert extract_chunk_tags("T", "текст") == []


def test_embed_chunks_returns_chunk_kinds():
    """2.5.8d: эмбеддинги на чанки, kind='chunk:N'."""
    fake_vecs = [[0.1] * 16, [0.2] * 16]
    with patch("wiki_v2.indexer.embed", return_value=fake_vecs):
        # Чанки должны быть >= 6 слов, чтобы не фильтроваться is_junk_chunk
        result = embed_chunks("Психология", ["это первый полезный чанк текста тут", "это второй полезный чанк текста тут"])
    assert "chunk:0" in result
    assert "chunk:1" in result


def test_embed_chunks_fail_open():
    """2.5.8d: на ошибке embed -> {}, не падает."""
    with patch("wiki_v2.indexer.embed", side_effect=Exception("boom")):
        assert embed_chunks("T", ["чанк"]) == {}


def test_embed_chunks_empty():
    """2.5.8d: пустые чанки -> {}."""
    assert embed_chunks("T", []) == {}


def test_map_chunk_tags_per_chunk():
    """2.5.9a (MAP): каждый чанк даёт СВОИ теги."""
    with patch("wiki_v2.extract.extract_chunk_tags",
               side_effect=lambda t, c: ["выготский"] if "выготский" in c else ["психология"]):
        result = map_chunk_tags("Психология", ["про выготского", "про развитие"])
    assert 0 in result
    assert 1 in result


def test_map_chunk_tags_fail_open():
    """2.5.9a (MAP): на ошибке чанк -> [], не падает."""
    with patch("wiki_v2.extract.extract_chunk_tags", side_effect=Exception("boom")):
        result = map_chunk_tags("T", ["чанк1"])
    assert result[0] == []


def test_reduce_chunk_tags_dedup():
    """2.5.9b (REDUCE): LLM сливает теги, убирает дубли."""
    with patch("wiki_v2.extract.chat_completion",
               return_value='["выготский", "психология"]'):
        result = reduce_chunk_tags("Психология", {0: ["выготский"], 1: ["выготский", "психология"]})
    assert result == ["выготский", "психология"]


def test_reduce_chunk_tags_fail_open():
    """2.5.9b (REDUCE): на None от LLM -> плоское объединение (не хуже)."""
    with patch("wiki_v2.extract.chat_completion", return_value=None):
        result = reduce_chunk_tags("Психология", {0: ["выготский"], 1: ["психология"]})
    assert "выготский" in result
    assert "психология" in result


def test_synthesize_reads_relevant_chunk_not_full_page():
    """2.5.10: синтез читает только релевантный чанк, не всю страницу."""
    # страница 6KB, релевантная тема
    tmp = tempfile.mktemp(suffix=".md")
    with open(tmp, "w") as f:
        f.write("Релевантная тема про выготского и психологию развития. " * 200)
    pages = {"slug": {"path": tmp, "title": "Психология"}}
    captured = {}
    def fake_chat(system, user, **kw):
        captured["len"] = len(user)
        return "ответ"
    with patch("wiki_v2.search.chat_completion", fake_chat):
        synthesize("выготский психология", ["slug"], pages)
    # prompt должен быть заметно меньше, чем вся страница (~6000)
    assert captured["len"] < 4000
    os.remove(tmp)


def test_synthesize_fail_open_no_relevant():
    """2.5.10: нет релевантного чанка -> начало файла (не пусто)."""
    tmp = tempfile.mktemp(suffix=".md")
    with open(tmp, "w") as f:
        f.write("Совершенно другая тема, не про запрос. " * 100)
    pages = {"slug": {"path": tmp, "title": "Страница"}}
    captured = {}
    def fake_chat(system, user, **kw):
        captured["len"] = len(user)
        return "ответ"
    with patch("wiki_v2.search.chat_completion", fake_chat):
        synthesize("выготский психология", ["slug"], pages)
    # не пустой промпт (fallback на начало файла)
    assert captured["len"] > 100
    os.remove(tmp)


