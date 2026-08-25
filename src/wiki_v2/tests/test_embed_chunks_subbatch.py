"""Суб-батчинг embed_chunks: большой набор чанков режется на порции EMBED_SUBBATCH.

Питфолл 2026-08-25: единый HTTP-запрос со всеми чанками длинной сессии ронял
llama-server на VPS в OOM-килл (MemoryMax=800M) и давал шторм 502/timeout.
"""

import numpy as np
import pytest

from wiki_v2.indexer import embed_chunks


def _good(n):
    return [f"Полезный чанк номер {i} с достаточным количеством слов" for i in range(n)]


@pytest.fixture
def _recording_embed(monkeypatch):
    """embed(): пишет размеры вызовов, возвращает вектор dim=4 на каждый текст."""
    calls = []

    def _fake_embed(texts, input_type="passage"):
        calls.append(list(texts))
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in texts]

    monkeypatch.setattr("wiki_v2.indexer.embed", _fake_embed)
    return calls


def test_subbatching_splits_calls(_recording_embed, monkeypatch):
    monkeypatch.setattr("wiki_v2.indexer.EMBED_SUBBATCH", 8)
    chunks = _good(20)
    res = embed_chunks("T", chunks)
    sizes = [len(c) for c in _recording_embed]
    assert sizes == [8, 8, 4]
    assert set(res.keys()) == {f"chunk:{i}" for i in range(20)}
    for v in res.values():
        assert v.dtype == np.float32


def test_single_subbatch_when_small(_recording_embed, monkeypatch):
    monkeypatch.setattr("wiki_v2.indexer.EMBED_SUBBATCH", 8)
    res = embed_chunks("T", _good(5))
    assert len(_recording_embed) == 1
    assert set(res.keys()) == {f"chunk:{i}" for i in range(5)}


def test_failed_subbatch_keeps_others(_recording_embed, monkeypatch):
    """Один суб-батч упал (None) → его чанки без вектора, остальные на месте."""
    monkeypatch.setattr("wiki_v2.indexer.EMBED_SUBBATCH", 2)

    def _flaky(texts, input_type="passage"):
        if "chunk-3" in texts[0]:
            return None  # второй суб-батч (индексы 2,3) недоступен
        return [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32) for _ in texts]

    monkeypatch.setattr("wiki_v2.indexer.embed", _flaky)
    chunks = [
        c.replace("номер", "= номер =") + f" маркер-{i}" for i, c in enumerate(_good(6))
    ]
    chunks[2] = chunks[2].replace("маркер-2", "chunk-3")
    chunks[3] = chunks[3].replace("маркер-3", "chunk-3")
    res = embed_chunks("T", chunks)
    assert "chunk:2" not in res
    assert "chunk:3" not in res
    assert set(res.keys()) == {"chunk:0", "chunk:1", "chunk:4", "chunk:5"}


def test_junk_filter_across_subbatches(_recording_embed, monkeypatch):
    monkeypatch.setattr("wiki_v2.indexer.EMBED_SUBBATCH", 2)
    chunks = [
        "Это полезный чанк с достаточным количеством слов",
        "мало слов",
        "Ещё полезный чанк который обязательно должен получить вектор",
    ]
    res = embed_chunks("T", chunks)
    assert set(res.keys()) == {"chunk:0", "chunk:2"}
