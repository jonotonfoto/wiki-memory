"""S4.6 — Ebbinghaus forgetting curve (forgetting_factor).

Tests for search.forgetting_factor: fresh→1.0, 30 days→~0.5,
60 days→~0.25, no date→1.0, disabled→1.0.
"""

import pytest
import time

from wiki_v2 import config
from wiki_v2.search import forgetting_factor


class TestForgettingFactor:
    """Unit tests for the forgetting-factor decay curve."""

    def test_fresh_factor(self):
        """Свежий факт (age=0) → factor ≈ 1.0."""
        now = time.time()
        result = forgetting_factor(updated=now, confidence=0.5, now=now)
        assert result == pytest.approx(1.0, abs=0.01), f"got {result}"

    def test_half_life(self):
        """30 дней (half_life=30) → factor ≈ 0.5."""
        now = time.time()
        updated = now - 30 * 86400
        result = forgetting_factor(updated=updated, confidence=0.5, now=now)
        assert result == pytest.approx(0.5, abs=0.01), f"got {result}"

    def test_two_half_lives(self):
        """60 дней (2×half_life) → factor ≈ 0.25."""
        now = time.time()
        updated = now - 60 * 86400
        result = forgetting_factor(updated=updated, confidence=0.5, now=now)
        assert result == pytest.approx(0.25, abs=0.01), f"got {result}"

    def test_no_date_none(self):
        """updated=None → 1.0 (fail-open)."""
        now = time.time()
        result = forgetting_factor(updated=None, confidence=0.5, now=now)
        assert result == pytest.approx(1.0, abs=0.01), f"got {result}"

    def test_no_date_non_numeric(self):
        """updated='не число' → 1.0 (fail-open)."""
        now = time.time()
        result = forgetting_factor(updated="не число", confidence=0.5, now=now)
        assert result == pytest.approx(1.0, abs=0.01), f"got {result}"

    def test_disabled(self, monkeypatch):
        """WIKI_FORGET_ENABLED=False → всегда 1.0."""
        monkeypatch.setattr(config, "WIKI_FORGET_ENABLED", False)
        now = time.time()
        updated = now - 60 * 86400
        result = forgetting_factor(updated=updated, confidence=0.5, now=now)
        assert result == pytest.approx(1.0, abs=0.01), f"got {result}"
