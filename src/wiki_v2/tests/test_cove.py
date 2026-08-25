"""S4.3 — CoVe (Chain-of-Verification) tests for verify_fact and _normalize."""
from unittest.mock import patch

from wiki_v2.extract import _normalize, verify_fact


# Helper: enable CoVe by patching wiki_v2.config.WIKI_COVE_ENABLED
# (verify_fact does `from . import config` → resolves to wiki_v2.config)

def _enable_cove():
    """Enable WIKI_COVE_ENABLED in the config module for a test."""
    p = patch("wiki_v2.config.WIKI_COVE_ENABLED", True)
    p.start()
    return p


# ── 1. verify_fact disabled (default) ────────────────────────────────────────

def test_verify_fact_disabled():
    """WIKI_COVE_ENABLED=False по умолчанию → всегда 'Unknown'."""
    result = verify_fact("факт", [])
    assert result == "Unknown"


# ── 2. verify_fact empty / non-string ────────────────────────────────────────

def test_verify_fact_empty_string():
    """Пустая строка → 'Unknown'."""
    with patch("wiki_v2.extract.chat_completion") as mock:
        result = verify_fact("", [])
    assert result == "Unknown"
    mock.assert_not_called()


def test_verify_fact_none():
    """None → 'Unknown'."""
    with patch("wiki_v2.extract.chat_completion") as mock:
        result = verify_fact(None, [])
    assert result == "Unknown"
    mock.assert_not_called()


# ── 3. verify_fact enabled → TRUE ────────────────────────────────────────────

def test_verify_fact_enabled_true():
    """LLM возвращает 'TRUE' → код возвращает 'TRUE' (uppercase)."""
    stop = _enable_cove()
    try:
        with patch("wiki_v2.extract.chat_completion", return_value="TRUE"):
            result = verify_fact("факт", ["другой факт"])
        assert result == "TRUE"
    finally:
        stop.stop()


# ── 4. verify_fact enabled → FALSE ───────────────────────────────────────────

def test_verify_fact_enabled_false():
    """LLM возвращает 'FALSE' → код возвращает 'FALSE' (uppercase)."""
    stop = _enable_cove()
    try:
        with patch("wiki_v2.extract.chat_completion", return_value="FALSE"):
            result = verify_fact("факт", ["другой факт"])
        assert result == "FALSE"
    finally:
        stop.stop()


# ── 5. verify_fact enabled → unknown (non-matching) ─────────────────────────

def test_verify_fact_enabled_unknown():
    """LLM возвращает 'maybe' → 'Unknown' (не совпадает с TRUE/FALSE/UNKNOWN)."""
    stop = _enable_cove()
    try:
        with patch("wiki_v2.extract.chat_completion", return_value="maybe"):
            result = verify_fact("факт", ["другой факт"])
        assert result == "Unknown"
    finally:
        stop.stop()


# ── 6. verify_fact fail-open ─────────────────────────────────────────────────

def test_verify_fact_fail_open():
    """chat_completion бросает исключение → 'Unknown'."""
    stop = _enable_cove()
    try:
        with patch("wiki_v2.extract.chat_completion", side_effect=Exception("network error")):
            result = verify_fact("факт", ["другой факт"])
        assert result == "Unknown"
    finally:
        stop.stop()


# ── 7. _normalize with existing_facts ────────────────────────────────────────

def test_normalize_with_existing_facts():
    """_normalize с existing_facts добавляет ключ 'fact_verification'."""
    stop = _enable_cove()
    try:
        with patch("wiki_v2.extract.chat_completion", side_effect=["TRUE", "FALSE"]):
            data = {"facts": ["факт1", "факт2"]}
            result = _normalize(data, existing_facts=["существующий факт"])

        assert "fact_verification" in result
        assert result["fact_verification"] == ["TRUE", "FALSE"]
        # facts остаётся list[str]
        assert result["facts"] == ["факт1", "факт2"]
        # summary добавляется
        assert result["summary"] == ""
    finally:
        stop.stop()


def test_normalize_without_existing_facts():
    """_normalize без existing_facts НЕ добавляет 'fact_verification'."""
    data = {"facts": ["факт1"]}
    result = _normalize(data, existing_facts=None)

    assert "fact_verification" not in result
    assert result["facts"] == ["факт1"]


def test_normalize_with_empty_existing_facts():
    """_normalize с пустым existing_facts → нет fact_verification."""
    data = {"facts": ["факт1"]}
    result = _normalize(data, existing_facts=[])

    assert "fact_verification" not in result
    assert result["facts"] == ["факт1"]


def test_normalize_facts_as_dicts():
    """_normalize принимает facts в формате [{text, confidence}]."""
    data = {"facts": [{"text": "факт1", "confidence": 0.9}]}
    result = _normalize(data)

    assert result["facts"] == ["факт1"]
    assert result["fact_confidences"] == [0.9]
