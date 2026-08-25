"""Tests for wiki_v2.events."""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

# Ensure the package is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wiki_v2.events import log_event


@pytest.fixture(autouse=True)
def _patch_events_path(tmp_path):
    """Point _events_path to a temp directory so tests are isolated."""
    events_dir = tmp_path / "wiki"
    events_dir.mkdir(parents=True, exist_ok=True)

    def fake_path():
        return events_dir / "wiki_search_events.jsonl"

    with mock.patch("wiki_v2.events._events_path", fake_path):
        yield fake_path


# ── Test: log_event writes and can be read back ──────────────────────────

def test_log_event_writes_and_reads_back(tmp_path, _patch_events_path):
    """log_event writes a JSON line that can be read back."""
    log_event(
        query="hermes agent config",
        hits=3,
        top_slug="hermes-config",
        top_score=0.82,
        context_chars=1200,
        duration_ms=45.0,
        source="semantic",
        session_id="20260814_123456",
    )

    path = _patch_events_path()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    obj = json.loads(lines[0])
    assert obj["query"] == "hermes agent config"
    assert obj["hits"] == 3
    assert obj["top_slug"] == "hermes-config"
    assert obj["top_score"] == 0.82
    assert obj["context_chars"] == 1200
    assert obj["duration_ms"] == 45.0
    assert obj["source"] == "semantic"
    assert obj["session_id"] == "20260814_123456"
    assert obj["type"] == "search_event"
    assert "ts" in obj


# ── Test: empty / short query is NOT written ─────────────────────────────

def test_empty_query_not_logged(tmp_path, _patch_events_path):
    """Empty query does not write anything."""
    log_event(query="", hits=1)
    path = _patch_events_path()
    assert not path.exists() or path.read_text().strip() == ""


def test_short_query_not_logged(tmp_path, _patch_events_path):
    """Query shorter than MIN_QUERY_LEN (3) is skipped."""
    log_event(query="ab", hits=1)
    path = _patch_events_path()
    assert not path.exists() or path.read_text().strip() == ""


# ── Test: rotation with a small limit ────────────────────────────────────

def test_rotation_with_small_limit(tmp_path, _patch_events_path):
    """When lines exceed MAX_LINES, the file is rotated to keep only last MAX_LINES."""
    # Patch MAX_LINES to a small value for testing
    import wiki_v2.events as events_mod

    original_max = events_mod.MAX_LINES
    events_mod.MAX_LINES = 5

    try:
        path = _patch_events_path()
        # Write 8 events
        for i in range(8):
            log_event(
                query=f"query number {i:04d} that is long enough to pass the filter",
                hits=1,
                top_slug=f"slug-{i}",
                top_score=0.5,
                source="semantic",
            )

        # Rotation happens BEFORE write, so file stabilises at MAX_LINES+1
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 6  # MAX_LINES + 1

        # The last 6 events should be present (indices 2-7)
        for i in range(6):
            obj = json.loads(lines[i])
            assert obj["query"] == f"query number {i + 2:04d} that is long enough to pass the filter"
    finally:
        events_mod.MAX_LINES = original_max


# ── Test: corrupted / broken JSONL does not crash ────────────────────────

def test_corrupted_jsonl_does_not_crash(tmp_path, _patch_events_path):
    """A file with a broken JSON line does not prevent subsequent writes."""
    path = _patch_events_path()

    # Pre-write a corrupted line
    path.write_text("this is not valid json\n", encoding="utf-8")

    # log_event should not raise
    log_event(
        query="hermes agent config",
        hits=2,
        top_slug="hermes-config",
        top_score=0.7,
        source="semantic",
    )

    # The file should now have the broken line + the new valid line
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "this is not valid json" in lines[0]

    obj = json.loads(lines[1])
    assert obj["query"] == "hermes agent config"
    assert obj["hits"] == 2


def test_log_event_with_gate_decision(tmp_path, _patch_events_path):
    """log_event writes gate_decision correctly."""
    log_event(
        query="relevance gate design",
        hits=2,
        top_slug="gate-page",
        top_score=0.91,
        source="semantic",
        gate_decision="show",
    )
    path = _patch_events_path()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["gate_decision"] == "show"
    assert obj["hits"] == 2


def test_plugin_logs_search_event(tmp_path, _patch_events_path):
    """Plugin search execution logs an event via log_event."""
    import sys
    from pathlib import Path
    plugin_dir = str(Path(__file__).resolve().parent.parent.parent.parent / "plugins" / "wiki-context")
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)
    import __init__ as plugin

    fake_hits = [("test-slug", 0.85, "semantic")]
    fake_pages = {
        "test-slug": {
            "title": "Test Page",
            "path": "",
        }
    }
    plugin._cache_get = lambda q: None
    plugin._gate_decision = lambda q: "show"
    with mock.patch("wiki_v2.search.search", return_value=(fake_hits, fake_pages)):
        res = plugin._build_context('тестовый запрос для проверки логирования событий поиска')

    path = _patch_events_path()
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    obj = json.loads(lines[-1])
    assert obj["query"] == "тестовый запрос для проверки логирования событий поиска"
    assert obj["hits"] == 1
    assert obj["gate_decision"] == "show"
