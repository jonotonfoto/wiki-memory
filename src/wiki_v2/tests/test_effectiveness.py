"""Tests for wiki_v2.effectiveness."""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wiki_v2.effectiveness import coverage, hit_rate, usage


@pytest.fixture(autouse=True)
def _patch_events_path(tmp_path):
    """Point _events_path to a temp directory so tests are isolated."""
    events_dir = tmp_path / "wiki"
    events_dir.mkdir(parents=True, exist_ok=True)

    def fake_path():
        return events_dir / "wiki_search_events.jsonl"

    with mock.patch("wiki_v2.effectiveness._events_path", fake_path):
        yield fake_path


@pytest.fixture(autouse=True)
def _patch_index_db(tmp_path):
    """Point _index_db_path to a temp directory so tests are isolated."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    def fake_path():
        return wiki_dir / ".index_v2.db"

    with mock.patch("wiki_v2.effectiveness._index_db_path", fake_path):
        yield fake_path


# ── hit_rate tests ───────────────────────────────────────────────────────

def test_hit_rate_7_of_10(tmp_path, _patch_events_path):
    """7 out of 10 events have hits>0 → hit_rate == 0.7."""
    path = _patch_events_path()
    lines = []
    for i in range(10):
        obj = {
            "ts": 1000.0 + i,
            "type": "search_event",
            "query": f"query number {i:04d} that is long enough to pass the filter",
            "hits": 1 if i < 7 else 0,
            "top_slug": f"slug-{i}",
            "top_score": 0.5,
            "context_chars": 100,
            "duration_ms": 10.0,
            "source": "semantic",
            "session_id": "20260814_000000",
        }
        lines.append(json.dumps(obj))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert hit_rate() == 0.7


def test_hit_rate_empty_file(tmp_path, _patch_events_path):
    """Empty file → 0.0."""
    path = _patch_events_path()
    path.write_text("", encoding="utf-8")
    assert hit_rate() == 0.0


def test_hit_rate_no_file(tmp_path, _patch_events_path):
    """Missing file → 0.0."""
    path = _patch_events_path()
    if path.exists():
        path.unlink()
    assert hit_rate() == 0.0


def test_hit_rate_all_no_hits(tmp_path, _patch_events_path):
    """All events have 0 hits → 0.0."""
    path = _patch_events_path()
    obj = {
        "ts": 1000.0,
        "type": "search_event",
        "query": "query number 0000 that is long enough to pass the filter",
        "hits": 0,
        "top_slug": "",
        "top_score": 0.0,
        "context_chars": 0,
        "duration_ms": 0.0,
        "source": "semantic",
        "session_id": "20260814_000000",
    }
    path.write_text(json.dumps(obj) + "\n", encoding="utf-8")
    assert hit_rate() == 0.0


# ── coverage tests ───────────────────────────────────────────────────────

def test_coverage_217_of_228(tmp_path, _patch_index_db):
    """217 out of 228 sessions have content_hash → ~0.95."""
    import sqlite3

    db_path = _patch_index_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            indexed_at REAL NOT NULL,
            page_slug TEXT DEFAULT '',
            content_hash TEXT DEFAULT ''
        )
    """)

    # 217 sessions with content_hash
    for i in range(217):
        conn.execute(
            "INSERT INTO sessions (session_id, indexed_at, content_hash) VALUES (?, ?, ?)",
            (f"session-{i}", 1000.0 + i, f"hash-{i}"),
        )

    # 11 sessions without content_hash
    for i in range(11):
        conn.execute(
            "INSERT INTO sessions (session_id, indexed_at, content_hash) VALUES (?, ?, ?)",
            (f"session-nohash-{i}", 2000.0 + i, ""),
        )

    conn.commit()
    conn.close()

    result = coverage()
    assert abs(result - 217 / 228) < 0.001


def test_coverage_no_db(tmp_path, _patch_index_db):
    """No DB file → 0.0."""
    db_path = _patch_index_db()
    if db_path.exists():
        db_path.unlink()
    assert coverage() == 0.0


def test_coverage_empty_db(tmp_path, _patch_index_db):
    """DB exists but sessions table is empty → 0.0."""
    import sqlite3

    db_path = _patch_index_db()
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            indexed_at REAL NOT NULL,
            page_slug TEXT DEFAULT '',
            content_hash TEXT DEFAULT ''
        )
    """)
    conn.commit()
    conn.close()

    assert coverage() == 0.0


# ── usage tests ──────────────────────────────────────────────────────────

def test_usage_keyword_in_both():
    """Keyword 'vps' in both context and answer → 1.0."""
    context = "We use vps for embedding and vps is fast"
    answer = "The vps solution works well"
    assert usage("vps", context, answer) == 1.0


def test_usage_keyword_missing_from_context():
    """Keyword 'vps' not in context → 0.0."""
    context = "We use local embeddings"
    answer = "The vps solution works well"
    assert usage("vps", context, answer) == 0.0


def test_usage_keyword_missing_from_answer():
    """Keyword 'vps' not in answer → 0.0."""
    context = "We use vps for embedding"
    answer = "The local solution works well"
    assert usage("vps", context, answer) == 0.0


def test_usage_empty_context():
    """Empty context → 0.0."""
    assert usage("vps", "", "vps answer") == 0.0


def test_usage_empty_answer():
    """Empty answer → 0.0."""
    assert usage("vps", "vps context", "") == 0.0


def test_usage_case_insensitive():
    """Keyword matching is case-insensitive."""
    context = "We use VPS for embedding"
    answer = "The vps solution works well"
    assert usage("vps", context, answer) == 1.0
