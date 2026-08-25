"""Tests for wiki_v2.status — health-check status() + CLI."""
import sys
from pathlib import Path

import pytest

# Ensure the package is importable from sandbox root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset in-memory counters before each test."""
    import wiki_v2.metrics as m
    with m._lock:
        m._counters.clear()
    yield
    with m._lock:
        m._counters.clear()


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME and WIKI_PATH into tmp_path (isolation).

    MUST set WIKI_PATH explicitly: it is pinned in the environment to the
    REAL wiki dir, so reload() alone would keep the real path and tests would
    write into the live .index_v2.db (corrupting it with page-N fixtures).
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WIKI_PATH", str(tmp_path / "wiki"))
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    yield


# ── Helper: create a DB with data ──────────────────────────────────────────

def _create_db_with_data(db_path, pages_count=391, embeds_count=388, sessions_count=10):
    """Create a real SQLite DB at the given path with given counts."""
    import sqlite3

    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    # Create tables (schema from index_db.py)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pages (
            slug TEXT PRIMARY KEY, title TEXT NOT NULL, section TEXT NOT NULL,
            path TEXT NOT NULL, content_hash TEXT NOT NULL,
            summary TEXT DEFAULT '', quality TEXT DEFAULT 'ok',
            full_text TEXT DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            slug TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'page',
            vector BLOB NOT NULL, embed_model_id TEXT DEFAULT '',
            PRIMARY KEY (slug, kind),
            FOREIGN KEY (slug) REFERENCES pages(slug) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, indexed_at REAL NOT NULL,
            page_slug TEXT DEFAULT '', content_hash TEXT DEFAULT ''
        );
    """)

    # Insert pages
    for i in range(pages_count):
        slug = f"page-{i}"
        conn.execute(
            "INSERT INTO pages (slug, title, section, path, content_hash, created, updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (slug, f"Title {i}", "section", f"/path/{i}", f"hash-{i}", 1000.0 + i, 1000.0 + i)
        )

    # Insert embeddings for first embeds_count pages
    for i in range(embeds_count):
        slug = f"page-{i}"
        conn.execute(
            "INSERT INTO embeddings (slug, kind, vector) VALUES (?,?,?)",
            (slug, "page", b"\x00" * 4096)
        )

    # Insert sessions
    for i in range(sessions_count):
        conn.execute(
            "INSERT INTO sessions (session_id, indexed_at) VALUES (?,?)",
            (f"session-{i}", 1700000000.0 + i)
        )

    conn.commit()
    conn.close()
    return db_path


# ── Test 1: БД (391 pages, 388 с embed) → pages=391, orphans=3 ───────────

def test_db_391_pages_388_embeds_returns_orphans_3(tmp_path, monkeypatch):
    """391 pages, 388 with embeddings → pages=391, orphans=3."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    _create_db_with_data(cfg.WIKI_PATH / ".index_v2.db", pages_count=391, embeds_count=388)

    from wiki_v2.status import status
    result = status()

    assert result["pages"] == 391
    assert result["orphans"] == 3
    assert "error" not in result


# ── Test 1.5: chunks/vectors из embeddings ────────────────────────────────

def test_status_reports_chunks_and_vectors(tmp_path, monkeypatch):
    """status() returns chunk/vector counts from the embeddings table."""
    import sqlite3
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    db_path = cfg.WIKI_PATH / ".index_v2.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS pages (
            slug TEXT PRIMARY KEY, title TEXT NOT NULL, section TEXT NOT NULL,
            path TEXT NOT NULL, content_hash TEXT NOT NULL,
            summary TEXT DEFAULT '', quality TEXT DEFAULT 'ok',
            full_text TEXT DEFAULT '', created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS embeddings (
            slug TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'page',
            vector BLOB NOT NULL, embed_model_id TEXT DEFAULT '',
            PRIMARY KEY (slug, kind),
            FOREIGN KEY (slug) REFERENCES pages(slug) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY, indexed_at REAL NOT NULL,
            page_slug TEXT DEFAULT '', content_hash TEXT DEFAULT ''
        );
    """)
    for slug in ("p1", "p2"):
        conn.execute(
            "INSERT INTO pages (slug,title,section,path,content_hash,created,updated) "
            "VALUES (?,?,?,?,?,?,?)",
            (slug, slug, "s", "/p", "h", 1.0, 1.0))
    kinds = ["page", "page", "chunk:0", "chunk:1", "page_chunk:0"]
    for i, kind in enumerate(kinds):
        slug = "p1" if i % 2 == 0 else "p2"
        conn.execute(
            "INSERT INTO embeddings (slug, kind, vector) VALUES (?,?,?)",
            (slug, kind, b"\x00" * 4096))
    conn.commit()
    conn.close()

    from wiki_v2.status import status
    result = status()

    assert result["chunks"] == 3  # chunk:0, chunk:1, page_chunk:0
    assert result["vectors"] == 5  # все embeddings
    assert "error" not in result


# ── Test 2: embed_errors=5 за 24ч → api_state 'offline' ───────────────────

def test_api_errors_5_gives_offline(tmp_path, monkeypatch):
    """5 embed errors in 24h → api_state 'offline'."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    # Write 5 fresh embed errors into the metrics file (status now reads the
    # JSONL file — the dashboard runs as a separate process, so in-memory
    # counters of this process are not visible there).
    import json, time
    metrics_path = cfg.WIKI_PATH / "wiki_metrics.jsonl"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    now = time.time()
    with open(metrics_path, "w", encoding="utf-8") as f:
        for _ in range(5):
            f.write(json.dumps({"ts": now, "type": "inc",
                                "name": "embed_api_errors_total", "value": 1}) + "\n")

    # Create a minimal DB so status() doesn't return error flag
    _create_db_with_data(cfg.WIKI_PATH / ".index_v2.db", pages_count=0, embeds_count=0, sessions_count=0)

    from wiki_v2.status import status
    result = status()

    assert result["api_state"] == "offline"
    assert result["api_errors_24h"] == 5


# ── Test 3: БД нет → dict с error флагом, НЕ исключение ───────────────────

def test_no_db_returns_error_flag(tmp_path, monkeypatch):
    """No DB file → dict with error flag, NOT an exception."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    # Ensure STATE_DB path doesn't exist
    db_path = cfg.WIKI_PATH / ".index_v2.db"
    if db_path.exists():
        db_path.unlink()

    from wiki_v2.status import status
    result = status()

    assert isinstance(result, dict)
    assert result.get("error") is True
    assert "api_state" in result


# ── Test 4: pages=0, sessions=0 → нули ────────────────────────────────────

def test_empty_db_returns_zeros(tmp_path, monkeypatch):
    """Empty DB (0 pages, 0 sessions) → all counts are 0."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    _create_db_with_data(cfg.WIKI_PATH / ".index_v2.db", pages_count=0, embeds_count=0, sessions_count=0)

    from wiki_v2.status import status
    result = status()

    assert result["pages"] == 0
    assert result["sessions"] == 0
    assert result["orphans"] == 0
    assert "error" not in result


# ── Test 5: нет метрик → api_state 'unknown', api_errors 0 ────────────────

def test_no_metrics_gives_unknown_state(tmp_path, monkeypatch):
    """No metrics at all → api_state 'unknown', api_errors 0."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    # Ensure no embed_api_errors_total in counters
    import wiki_v2.metrics as m
    with m._lock:
        m._counters.clear()

    # Create a minimal DB
    _create_db_with_data(cfg.WIKI_PATH / ".index_v2.db", pages_count=0, embeds_count=0, sessions_count=0)

    from wiki_v2.status import status
    result = status()

    assert result["api_state"] == "unknown"
    assert result["api_errors_24h"] == 0


# ── Test 6: status() возвращает dict со всеми ключами ─────────────────────

def test_status_returns_dict_with_all_keys(tmp_path, monkeypatch):
    """status() always returns a dict with all expected keys."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    _create_db_with_data(cfg.WIKI_PATH / ".index_v2.db", pages_count=10, embeds_count=10, sessions_count=5)

    from wiki_v2.status import status
    result = status()

    expected_keys = {
        "api_state", "last_indexed_at", "api_errors_24h",
        "pages", "sessions", "orphans",
        "db_size_mb", "disk_free_gb", "disk_warning",
    }
    assert isinstance(result, dict)
    assert expected_keys.issubset(set(result.keys()))

    # Type checks
    assert isinstance(result["api_state"], str)
    assert isinstance(result["api_errors_24h"], int)
    assert isinstance(result["pages"], int)
    assert isinstance(result["sessions"], int)
    assert isinstance(result["orphans"], int)
    assert isinstance(result["db_size_mb"], float)
    assert isinstance(result["disk_free_gb"], float)
    assert isinstance(result["disk_warning"], bool)


# ── Test 7: CLI output is valid lines ─────────────────────────────────────

def test_cli_prints_lines(tmp_path, monkeypatch, capsys):
    """CLI prints key: value lines."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    cfg.reload()

    _create_db_with_data(cfg.WIKI_PATH / ".index_v2.db", pages_count=1, embeds_count=1, sessions_count=1)

    from wiki_v2.status import _cli
    _cli()

    captured = capsys.readouterr()
    lines = [l for l in captured.out.strip().split("\n") if l]
    assert len(lines) >= 9  # at least all keys
    for line in lines:
        assert ":" in line  # key: value format
