# embed.py
"""Cosine similarity search over embedding dict."""
import numpy as np


def _norm(v):
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def top_k_cosine(query: np.ndarray, store: dict, k: int = 5, min_score: float = 0.0):
    """Return [(slug, score), ...] sorted desc. store: {slug: np.ndarray}."""
    if not store:
        return []
    qn = _norm(query.astype(np.float64))
    scored = []
    for slug, vec in store.items():
        vn = _norm(vec.astype(np.float64))
        score = float(np.dot(qn, vn))
        if score >= min_score:
            scored.append((slug, score))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]
