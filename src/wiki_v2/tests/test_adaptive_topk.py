"""Tests for S4.8 adaptive_top_k."""

from wiki_v2 import config
from wiki_v2.search import adaptive_top_k


def test_short_query():
    """Короткий запрос (< 15 символов) → min(base_k, 2)."""
    short = 'короткий'  # len=8
    assert adaptive_top_k(short, base_k=5) == 2


def test_medium_query():
    """Средний запрос (15–29 символов) → base_k."""
    medium = 'x' * 20  # len=20
    assert adaptive_top_k(medium, base_k=5) == 5


def test_long_query():
    """Длинный запрос (>= 30 символов) → min(base_k*2, WIKI_MAX_TOP_K)."""
    long_q = 'x' * 30  # len=30
    assert adaptive_top_k(long_q, base_k=5) == 10


def test_long_query_capped():
    """Длинный запрос с большим base_k — кэп WIKI_MAX_TOP_K."""
    long_q = 'x' * 30  # len=30
    assert adaptive_top_k(long_q, base_k=8) == 10


def test_disabled(monkeypatch):
    """Когда адаптация отключена → всегда base_k, независимо от длины."""
    monkeypatch.setattr(config, 'WIKI_ADAPTIVE_TOP_K_ENABLED', False)
    long_q = 'x' * 30
    assert adaptive_top_k(long_q, base_k=5) == 5
