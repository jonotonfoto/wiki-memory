"""Tests for wiki_v2.metrics — inc / record / snapshot / rotation / corruption."""
import json
import sys
from pathlib import Path

import pytest

# Ensure the package is importable from sandbox root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset in-memory counters and handlers before each test."""
    import wiki_v2.metrics as m
    with m._lock:
        m._counters.clear()
    yield
    # After test, also clear again to avoid cross-test pollution
    with m._lock:
        m._counters.clear()


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME (and thus metrics file) into tmp_path."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Force config to re-resolve paths from env
    import wiki_v2.config as cfg
    cfg.reload()
    yield


# ── Test 1: inc('x') 3 раза → snapshot показывает 3 ─────────────────────

def test_inc_three_times(snapshot_metrics):
    """inc increments counter and writes JSONL."""
    from wiki_v2.metrics import inc, snapshot

    inc("x")
    inc("x")
    inc("x")

    snap = snapshot()
    assert snap["x"] == 3


# ── Test 2: record числовое значение сохраняется ────────────────────────

def test_record_stores_value(snapshot_metrics):
    """record sets the counter to an arbitrary float."""
    from wiki_v2.metrics import record, snapshot

    record("latency_ms", 42.5)
    snap = snapshot()
    assert snap["latency_ms"] == 42.5


# ── Test 3: ротация (маленький лимит) работает ──────────────────────────

def test_rotation_small_limit(snapshot_metrics):
    """When file exceeds MAX_LINES, rotation keeps only last N lines."""
    from wiki_v2 import metrics as m

    # Temporarily lower the limit for testing
    original = m.MAX_LINES
    m.MAX_LINES = 5

    try:
        path = m._metrics_path()
        # Write 8 lines (exceeds limit of 5)
        for i in range(8):
            m.inc("counter")

        assert path.exists()
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        # After rotation, should have at most MAX_LINES entries
        assert len(lines) <= 5 + 1  # +1 for the last write that triggered rotation
    finally:
        m.MAX_LINES = original


# ── Test 4: битый jsonl не роняет inc ───────────────────────────────────

def test_corrupted_jsonl_does_not_crash(snapshot_metrics):
    """If the JSONL file contains garbage, inc() still works."""
    from wiki_v2 import metrics as m

    path = m._metrics_path()
    # Pre-write corrupted content
    with open(path, "w", encoding="utf-8") as f:
        f.write("THIS IS NOT VALID JSON\n{{{broken\n")

    # inc should not raise
    m.inc("safe_counter")

    snap = m.snapshot()
    assert snap["safe_counter"] == 1


# ── Test 5: файл создаётся если нет ─────────────────────────────────────

def test_file_created_if_missing(snapshot_metrics):
    """metrics.jsonl is created on first inc/record call."""
    from wiki_v2 import metrics as m

    path = m._metrics_path()
    assert not path.exists()

    m.inc("new_counter")

    assert path.exists()
    with open(path, "r", encoding="utf-8") as f:
        first_line = f.readline()
    obj = json.loads(first_line)
    assert obj["type"] == "inc"
    assert obj["name"] == "new_counter"


# ── Test 6: snapshot возвращает dict ────────────────────────────────────

def test_snapshot_returns_dict(snapshot_metrics):
    """snapshot() returns a plain dict (not a custom object)."""
    from wiki_v2.metrics import inc, snapshot

    inc("a")
    result = snapshot()

    assert isinstance(result, dict)
    assert "a" in result


# ── Helper fixture: capture metrics file path for tests ─────────────────

@pytest.fixture
def snapshot_metrics(tmp_path, monkeypatch):
    """Ensure _metrics_path resolves to tmp_path/wiki/."""
    import wiki_v2.config as cfg
    import wiki_v2.metrics as m

    # Reload config so HERMES_HOME points to tmp_path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    cfg.reload()

    # Verify _metrics_path returns the expected location
    path = m._metrics_path()
    assert "wiki" in str(path) or str(tmp_path) in str(path)

    yield path
