"""Tests for Wiki chunker (S2.5.8): split_text chunking."""
from wiki_v2.chunker import split_text, chunk_contextual


def test_short_text_single_chunk():
    """Короткий текст -> один чанк."""
    assert split_text("короткий текст") == ["короткий текст"]


def test_long_text_multiple_chunks():
    """Длинный текст -> несколько чанков по смыслу."""
    txt = "Абзац про философию Выготского и зону ближайшего развития.\n\n" * 40
    chunks = split_text(txt)
    assert len(chunks) > 1
    # каждый чанк не больше ~chunk_size
    assert all(len(c) <= 510 for c in chunks)


def test_empty_text_fail_open():
    """Пустой текст -> [''], не падает."""
    assert split_text("") == [""]
    assert split_text(None) == [""]


def test_chunks_preserve_content():
    """Содержимое сохраняется (конкатенация чанков ~ исходный текст)."""
    txt = "Предложение одно. Предложение два. " * 100
    chunks = split_text(txt, chunk_size=500)
    joined = " ".join(chunks)
    # из-за overlap joined длиннее, но содержит все слова
    for word in ("Предложение", "одно", "два"):
        assert word in joined


def test_chunk_contextual_wraps_with_title():
    """Contextual Retrieval: чанк обёрнут контекстом страницы."""
    txt = "Текст про выготского и психологию развития. " * 50
    chunks = chunk_contextual(txt, "Психология", ["выготский", "психология"])
    assert len(chunks) > 1
    assert all(c.startswith('Это часть страницы "Психология"') for c in chunks)
    assert "выготский" in chunks[0]


def test_chunk_contextual_fail_open_empty():
    """Пустой текст -> [], не падает."""
    assert chunk_contextual("", "Т") == []

