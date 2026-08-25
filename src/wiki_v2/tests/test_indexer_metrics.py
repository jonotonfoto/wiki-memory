"""Tests for indexer metrics extraction quality."""

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


def test_extract_valid_incremented():
    """_inc_extract('ok') increments extract_valid_total."""
    from wiki_v2.indexer import _inc_extract
    from wiki_v2.metrics import snapshot

    _inc_extract("ok")
    snap = snapshot()
    assert snap["extract_valid_total"] == 1


def test_extract_fallback_incremented():
    """_inc_extract('fallback') increments extract_fallback_total."""
    from wiki_v2.indexer import _inc_extract
    from wiki_v2.metrics import snapshot

    _inc_extract("fallback")
    snap = snapshot()
    assert snap["extract_fallback_total"] == 1


def test_extract_valid_incremented_on_create(monkeypatch, tmp_path):
    """Integration: process_session CREATE branch increments extract_valid_total."""

    # Create a temporary IndexDB in tmp_path

    from wiki_v2.index_db import IndexDB
    from wiki_v2.indexer import process_session
    from wiki_v2.metrics import snapshot
    tmp_dir = Path(tmp_path)
    index_db_path = tmp_dir / ".index_v2.db"
    db = IndexDB(str(index_db_path))

    # Mock session dict
    session = {
        "id": "test-session-1",
        "title": "Test Session",
        "started_at": 0,
    }

    # We need to ensure that the session goes through the CREATE branch (no existing page)
    # We can mock db.get_page_slug_for_session to return None and find_merge_target to return None.
    # But we don't want to modify the live code. Instead, we can rely on the fact that the database is empty.
    # However, process_session also uses extract_content, etc. We'll mock those to avoid heavy dependencies.
    # Instead, we'll test the helper directly as above, and for integration we can trust that the calls are placed.
    # But let's do a simple integration by mocking the dependencies to return predictable values.

    # We'll monkeypatch the extract_content to return a dict with quality "ok".
    # ⚠️ indexer импортирует extract_content НАПРЯМУЮ (from wiki_v2.extract import ...),
    # поэтому мокать надо wiki_v2.indexer.extract_content (не wiki_v2.extract.extract_content).
    import wiki_v2.indexer as idx_mod
    monkeypatch.setattr(idx_mod, "extract_content", lambda title, text: {
        "summary": "test summary",
        "key_topics": [],
        "entities": [],
        "concepts": [],
        "links": [],
        "quality": "ok",
    })
    monkeypatch.setattr(idx_mod, "map_chunk_tags", lambda title, chunks: [])
    monkeypatch.setattr(idx_mod, "reduce_chunk_tags", lambda title, tags: [])

    # Mock session_raw_text to return a short text (avoid state.db roundtrip)
    monkeypatch.setattr(idx_mod, "session_raw_text", lambda sid: "short text")

    # Mock embed_multivector / embed_chunks to avoid real API calls (NVIDIA 500)
    monkeypatch.setattr(idx_mod, "embed_multivector", lambda title, summary, topics: {})
    monkeypatch.setattr(idx_mod, "embed_chunks", lambda title, chunks: {})
    # Avoid chunking path for long sessions
    monkeypatch.setattr(idx_mod, "split_text", lambda text: [])

    # Also we need to mock find_merge_target to return None (so we go to CREATE)
    import wiki_v2.pages
    monkeypatch.setattr(wiki_v2.pages, "find_merge_target", lambda topics, candidates: None)

    # ⚠️ indexer копирует STATE_DB при импорте (STATE_DB = str(config.STATE_DB), строка 32).
    # cfg.reload() в _tmp_wiki не обновляет indexer.STATE_DB → get_session_text читал бы ЖИВОЙ state.db.
    # Поэтому мокаем indexer.STATE_DB на tmp_path/state.db.
    monkeypatch.setattr(idx_mod, "STATE_DB", str(tmp_path / "state.db"))

    # Mock db.upsert_page, db.update_page_hash, etc. to avoid touching the real database?
    # But we are using a temporary database, so we can let it write.
    # However, we also need to mock the atomic_write and file writing to avoid creating files in the tmp directory.
    # We'll let it create files in the tmp directory under WIKI_PATH/entities.
    # We need to set WIKI_PATH in config to tmp_dir.
    from wiki_v2 import config
    monkeypatch.setattr(config, "WIKI_PATH", tmp_dir)
    # Also need to set STATE_DB? It's derived from config.STATE_DB which uses HERMES_HOME.
    # We already set HERMES_HOME to tmp_path, so STATE_DB will be tmp_path/state.db.
    # We need to initialize the state database with the session.
    # Let's create a minimal state.db with the session and a message.
    import sqlite3
    state_db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(state_db_path))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at INTEGER)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp INTEGER)")
    conn.execute("INSERT INTO sessions (id, title, started_at) VALUES (?, ?, ?)",
                 (session["id"], session["title"], session["started_at"]))
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                 (session["id"], "user", "Hello world", 0))
    conn.commit()
    conn.close()

    # Now call process_session
    slug = process_session(db, session)
    assert slug is not None

    # Check metrics
    snap = snapshot()
    # We expect one extract_valid_total increment
    assert snap["extract_valid_total"] == 1
    # extract_fallback_total should be 0
    assert snap.get("extract_fallback_total", 0) == 0