"""Подэтап 4б: embed_chunks фильтрует мусорные чанки (is_junk_chunk), сохраняя исходные индексы."""
import numpy as np
import pytest

from wiki_v2.indexer import embed_chunks


@pytest.fixture
def _patch_embed(monkeypatch):
    """Заглушка embed(): возвращает вектор размерности 4 для каждого текста."""
    calls = {"n": 0}

    def _fake_embed(texts, input_type="passage"):
        calls["n"] = len(texts)
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in texts]

    monkeypatch.setattr("wiki_v2.indexer.embed", _fake_embed)
    return calls


def _chunks():
    # 0: полезный (>=6 слов) | 1: мусор (<6 слов) | 2: полезный | 3: мусор (<6 слов)
    return [
        "Это полезный чанк с достаточным количеством слов для эмбеддинга",
        "мало слов",
        "Ещё один полезный чанк который обязательно должен получить вектор",
        "тоже мусор",
    ]


def test_junk_chunks_not_embedded(_patch_embed):
    res = embed_chunks("T", _chunks())
    # Только индексы 0 и 2 (полезные) получили векторы, мусорные (1,3) — нет.
    assert "chunk:0" in res
    assert "chunk:2" in res
    assert "chunk:1" not in res
    assert "chunk:3" not in res
    # embed вызван только для 2 не-мусорных чанков.
    assert _patch_embed["n"] == 2


def test_junk_chunks_preserve_original_index(_patch_embed):
    res = embed_chunks("T", _chunks())
    # kind сохраняет ИСХОДНЫЙ индекс чанка (0,2), не сжатый (0,1).
    assert set(res.keys()) == {"chunk:0", "chunk:2"}


def test_all_junk_returns_empty(_patch_embed):
    res = embed_chunks("T", ["мало", "тоже"])
    assert res == {}
    assert _patch_embed["n"] == 0


def test_empty_chunks_returns_empty(_patch_embed):
    assert embed_chunks("T", []) == {}
