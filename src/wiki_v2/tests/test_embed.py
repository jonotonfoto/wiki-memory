# tests/test_embed.py
import numpy as np
from wiki_v2.embed import top_k_cosine


def test_top_k_orders_by_similarity():
    q = np.array([1.0, 0.0, 0.0])
    store = {
        "close": np.array([0.9, 0.1, 0.0]),
        "far": np.array([0.0, 1.0, 0.0]),
        "closest": np.array([0.99, 0.01, 0.0]),
    }
    hits = top_k_cosine(q, store, k=2)
    assert [h[0] for h in hits] == ["closest", "close"]
    assert hits[0][1] > hits[1][1] > 0.8


def test_top_k_empty_store():
    assert top_k_cosine(np.array([1.0]), {}, k=5) == []


def test_top_k_threshold():
    q = np.array([1.0, 0.0])
    store = {"ortho": np.array([0.0, 1.0])}
    assert top_k_cosine(q, store, k=5, min_score=0.5) == []
