"""S4.1 — Tests for clamp_confidence and default_fact_confidence."""


from wiki_v2.extract import clamp_confidence
from wiki_v2.index_db import default_fact_confidence

# ── clamp_confidence: valid values ────────────────────────────────────────────

class TestClampConfidenceValid:
    def test_clamp_08(self):
        assert clamp_confidence(0.8) == 0.8

    def test_clamp_10(self):
        assert clamp_confidence(1.0) == 1.0

    def test_clamp_00(self):
        assert clamp_confidence(0.0) == 0.0

    def test_clamp_05(self):
        assert clamp_confidence(0.5) == 0.5


# ── clamp_confidence: boundary clamping ───────────────────────────────────────

class TestClampConfidenceBounds:
    def test_clamp_over_1(self):
        """> 1.0 → clamped to 1.0"""
        assert clamp_confidence(1.5) == 1.0

    def test_clamp_under_0(self):
        """< 0.0 → clamped to 0.0"""
        assert clamp_confidence(-0.5) == 0.0


# ── clamp_confidence: None / non-number → default (0.5) ───────────────────────

class TestClampConfidenceDefault:
    def test_clamp_none(self):
        """None → default confidence"""
        assert clamp_confidence(None) == 0.5

    def test_clamp_non_number_string(self):
        """Non-numeric string → default confidence"""
        assert clamp_confidence("не число") == 0.5


# ── default_fact_confidence: list[str] → [default]*n ─────────────────────────

class TestDefaultFactConfidenceStrList:
    def test_two_strings(self):
        """list[str] → [0.5, 0.5]"""
        result = default_fact_confidence(["a", "b"])
        assert result == [0.5, 0.5]

    def test_single_string(self):
        """single string → [0.5]"""
        result = default_fact_confidence(["only fact"])
        assert result == [0.5]


# ── default_fact_confidence: list[dict] with text/confidence ──────────────────

class TestDefaultFactConfidenceDictList:
    def test_two_dicts(self):
        """list[dict] → clamped confidences extracted"""
        facts = [
            {"text": "x", "confidence": 0.9},
            {"text": "y", "confidence": 0.3},
        ]
        result = default_fact_confidence(facts)
        assert result == [0.9, 0.3]

    def test_dicts_clamped(self):
        """dicts with out-of-range confidence → clamped"""
        facts = [
            {"text": "over", "confidence": 2.0},
            {"text": "under", "confidence": -1.0},
        ]
        result = default_fact_confidence(facts)
        assert result == [1.0, 0.0]

    def test_dicts_none_confidence(self):
        """dict with None confidence → default"""
        facts = [{"text": "no conf", "confidence": None}]
        result = default_fact_confidence(facts)
        assert result == [0.5]


# ── default_fact_confidence: empty / None → [] ───────────────────────────────

class TestDefaultFactConfidenceEmpty:
    def test_empty_list(self):
        """[] → []"""
        assert default_fact_confidence([]) == []

    def test_none_input(self):
        """None → []"""
        assert default_fact_confidence(None) == []
