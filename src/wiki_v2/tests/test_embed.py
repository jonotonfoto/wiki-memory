# tests/test_embed.py
import numpy as np
import pytest
from wiki_v2.embed import top_k_cosine
from wiki_v2.config import EMBED_DIM

def test_top_k_orders_by_similarity():
    q = np.zeros(EMBED_DIM)
    q[0] = 1.0
    store = {
        "close": np.array([0.9, 0.1] + [0.0]*(EMBED_DIM-2)),
        "far": np.array([0.0, 1.0] + [0.0]*(EMBED_DIM-2)),
        "closest": np.array([0.99, 0.01] + [0.0]*(EMBED_DIM-2)),
    }
    hits = top_k_cosine(q, store, k=2)
    assert [h[0] for h in hits] == ["closest", "close"]
    assert hits[0][1] > hits[1][1] > 0.8

def test_top_k_empty_store():
    assert top_k_cosine(np.zeros(EMBED_DIM), {}, k=5) == []

def test_top_k_threshold():
    q = np.zeros(EMBED_DIM)
    q[0] = 1.0
    store = {"ortho": np.array([0.0, 1.0] + [0.0]*(EMBED_DIM-2))}
    assert top_k_cosine(q, store, k=5, min_score=0.5) == []

def test_top_k_dimension_mismatch():
    from wiki_v2.embed import top_k_cosine
    q = np.zeros(EMBED_DIM)
    store = {"wrong": np.array([1.0, 0.0])} # Too short
    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        top_k_cosine(q, store, k=5)

def test_top_k_none_vector():
    """S3.5: None-вектор пропускается (fail-open), не бросает."""
    q = np.zeros(EMBED_DIM)
    q[0] = 1.0
    store = {
        "valid": np.array([0.9, 0.1] + [0.0]*(EMBED_DIM-2)),
        "none_vec": None,
    }
    hits = top_k_cosine(q, store, k=5)
    assert "none_vec" not in [h[0] for h in hits]
    assert [h[0] for h in hits] == ["valid"]
