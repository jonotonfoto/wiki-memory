"""Tests for wiki_v2.dashboard_data — JSONL-based data readers for /api/status."""
import json
import sys
import time
from pathlib import Path

import pytest

# Ensure the package is importable from sandbox root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME/WIKI_PATH into tmp_path."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    yield


@pytest.fixture
def wiki_dir(tmp_path, monkeypatch):
    """Return the wiki directory path and ensure config points there."""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    return wiki


# ── Test 1: read_metrics_file — inc sums, record last value ─────────────────

def test_read_metrics_file_inc_and_record(wiki_dir):
    """read_metrics_file: inc -> sum, record -> last value."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    base_ts = 1700000000
    lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "embed_api_calls_total", "value": 1}),
        json.dumps({"ts": base_ts + 1, "type": "inc", "name": "embed_api_calls_total", "value": 2}),
        json.dumps({"ts": base_ts + 2, "type": "inc", "name": "embed_api_calls_total", "value": 3}),
        json.dumps({"ts": base_ts + 3, "type": "record", "name": "embed_api_errors_total", "value": 5}),
        json.dumps({"ts": base_ts + 4, "type": "record", "name": "embed_api_errors_total", "value": 7}),
        json.dumps({"ts": base_ts + 5, "type": "inc", "name": "cache_hits_total", "value": 10}),
        json.dumps({"ts": base_ts + 6, "type": "inc", "name": "cache_misses_total", "value": 3}),
        json.dumps({"ts": base_ts + 7, "type": "inc", "name": "chat_api_calls_total", "value": 4}),
        json.dumps({"ts": base_ts + 8, "type": "inc", "name": "chat_api_errors_total", "value": 1}),
        json.dumps({"ts": base_ts + 9, "type": "inc", "name": "search_fallback_total", "value": 2}),
    ]
    metrics_file = wiki_dir / "wiki_metrics.jsonl"
    metrics_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = dd.read_metrics_file()

    # inc: embed_api_calls_total = 1+2+3 = 6
    assert result["embed_api_calls_total"] == 6
    # record: embed_api_errors_total = last = 7
    assert result["embed_api_errors_total"] == 7
    # inc: cache_hits_total = 10
    assert result["cache_hits_total"] == 10
    # inc: cache_misses_total = 3
    assert result["cache_misses_total"] == 3
    # inc: chat_api_calls_total = 4
    assert result["chat_api_calls_total"] == 4
    # inc: chat_api_errors_total = 1
    assert result["chat_api_errors_total"] == 1
    # inc: search_fallback_total = 2
    assert result["search_fallback_total"] == 2


# ── Test 2: read_events — returns list of search_event dicts ────────────────

def test_read_events(wiki_dir):
    """read_events: returns list of search_event dicts from JSONL."""
    import importlib

    import wiki_v2.dashboard_data as dd
    import wiki_v2.effectiveness as eff
    importlib.reload(eff)
    importlib.reload(dd)

    base_ts = 1700000000
    lines = [
        json.dumps({
            "ts": base_ts, "type": "search_event", "query": "test query 1",
            "hits": 5, "duration_ms": 45.0, "source": "semantic",
        }),
        json.dumps({
            "ts": base_ts + 10, "type": "search_event", "query": "test query 2",
            "hits": 0, "duration_ms": 30.0, "source": "keyword",
        }),
        json.dumps({
            "ts": base_ts + 20, "type": "other_type", "query": "ignored",
        }),
        "not json at all",
        "",
    ]
    events_file = wiki_dir / "wiki_search_events.jsonl"
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = dd.read_events()

    assert len(result) == 2
    assert result[0]["query"] == "test query 1"
    assert result[0]["hits"] == 5
    assert result[1]["query"] == "test query 2"
    assert result[1]["hits"] == 0


# ── Test 3: _build_api_status — all keys present, correct structure ─────────

def test_build_api_status_structure(wiki_dir):
    """_build_api_status: all required keys present, values are numbers."""
    import importlib

    import wiki_v2.dashboard_data as dd
    import wiki_v2.effectiveness as eff
    import wiki_v2.status as st
    importlib.reload(eff)
    importlib.reload(st)
    importlib.reload(dd)

    # Create metrics file
    base_ts = 1700000000
    metrics_lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "embed_api_calls_total", "value": 100}),
        json.dumps({"ts": base_ts + 1, "type": "inc", "name": "embed_api_errors_total", "value": 2}),
        json.dumps({"ts": base_ts + 2, "type": "inc", "name": "chat_api_calls_total", "value": 50}),
        json.dumps({"ts": base_ts + 3, "type": "inc", "name": "chat_api_errors_total", "value": 1}),
        json.dumps({"ts": base_ts + 4, "type": "inc", "name": "cache_hits_total", "value": 80}),
        json.dumps({"ts": base_ts + 5, "type": "inc", "name": "cache_misses_total", "value": 20}),
        json.dumps({"ts": base_ts + 6, "type": "inc", "name": "search_fallback_total", "value": 3}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")

    # Create events file
    events_lines = [
        json.dumps({
            "ts": base_ts + 10, "type": "search_event", "query": "q1",
            "hits": 5, "duration_ms": 45.0, "source": "semantic",
        }),
        json.dumps({
            "ts": base_ts + 20, "type": "search_event", "query": "q2",
            "hits": 3, "duration_ms": 30.0, "source": "keyword",
        }),
    ]
    (wiki_dir / "wiki_search_events.jsonl").write_text("\n".join(events_lines) + "\n", encoding="utf-8")

    result = dd._build_api_status()

    # Top-level keys
    assert "generated_at" in result
    assert isinstance(result["generated_at"], int)
    assert "health" in result
    assert "effectiveness" in result
    assert "database" in result
    assert "api" in result
    assert "search" in result
    assert "errors_recent" in result
    assert "lmstudio" in result

    # health
    h = result["health"]
    assert "api_state" in h
    assert "last_indexed_at" in h
    assert "db_size_mb" in h
    assert "disk_warning" in h
    assert "api_errors_24h" in h

    # effectiveness
    e = result["effectiveness"]
    assert "hit_rate" in e
    assert "coverage" in e
    assert "rating" in e
    assert isinstance(e["hit_rate"], float)
    assert isinstance(e["coverage"], float)

    # database
    d = result["database"]
    assert "pages" in d
    assert "sessions" in d
    assert "orphans" in d
    assert "facts" in d
    assert "new_sessions_7d" in d

    # api
    a = result["api"]
    assert "embed_calls" in a
    assert "embed_errors" in a
    assert "chat_calls" in a
    assert "chat_errors" in a
    assert "cache_hit_rate" in a
    assert "search_fallback" in a
    # cache_hit_rate should be 80/(80+20) = 0.8
    assert abs(a["cache_hit_rate"] - 0.8) < 0.01

    # search
    s = result["search"]
    assert "recent_queries" in s
    assert "total_events" in s
    assert s["total_events"] == 2
    assert len(s["recent_queries"]) == 2
    # recent_queries are reversed (last first)
    assert s["recent_queries"][0]["query"] == "q2"
    assert s["recent_queries"][1]["query"] == "q1"

    # errors_recent — should be [] (no log file)
    assert isinstance(result["errors_recent"], list)

    # lmstudio — will be unreachable in tests
    lm = result["lmstudio"]
    assert "reachable" in lm
    assert "models" in lm


# ── Test 4: fail-open — no files → {} / [] / {"reachable": False} ───────────

def test_fail_open_no_files(wiki_dir, monkeypatch):
    """When no data files exist, functions return safe defaults."""
    import importlib

    import wiki_v2.dashboard_data as dd
    import wiki_v2.effectiveness as eff
    import wiki_v2.status as st
    importlib.reload(eff)
    importlib.reload(st)
    importlib.reload(dd)

    # No metrics file, no events file, no log file
    assert dd.read_metrics_file() == {}
    assert dd.read_events() == []
    assert dd.read_log_errors() == []
    assert dd.read_oversized() == []

    # _build_api_status should not raise, should return dict with keys
    result = dd._build_api_status()
    assert isinstance(result, dict)
    assert "health" in result
    assert "effectiveness" in result
    assert "api" in result
    assert "search" in result
    assert "lmstudio" in result

    # lmstudio_status: mock urllib to simulate unreachable
    def mock_urlopen(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    importlib.reload(dd)
    lm = dd.lmstudio_status()
    assert lm == {"reachable": False, "models": []}


# ── Test 5: lmstudio_status — mock unavailable → {"reachable": False} ───────

def test_lmstudio_status_unreachable(wiki_dir, monkeypatch):
    """lmstudio_status when LM Studio is not running."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    # Mock urllib to simulate unreachable
    def mock_urlopen(*args, **kwargs):
        raise OSError("connection refused")
    monkeypatch.setattr("urllib.request.urlopen", mock_urlopen)
    importlib.reload(dd)

    result = dd.lmstudio_status()
    assert result["reachable"] is False
    assert result["models"] == []


# ── Test 6: read_log_errors — filters WARNING/ERROR lines ────────────────────

def test_read_log_errors(wiki_dir):
    """read_log_errors: returns WARNING/ERROR lines from log file."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    logs_dir = wiki_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "wiki_v2.log"

    lines = [
        "2026-08-15 10:00:00 INFO Normal operation\n",
        "2026-08-15 10:01:00 WARNING Something went wrong\n",
        "2026-08-15 10:02:00 ERROR Critical failure\n",
        "2026-08-15 10:03:00 INFO Back to normal\n",
        "2026-08-15 10:04:00 WARNING Another warning\n",
        "2026-08-15 10:05:00 DEBUG Debug info\n",
    ]
    log_file.write_text("".join(lines), encoding="utf-8")

    result = dd.read_log_errors()
    assert len(result) == 3
    assert "WARNING Something went wrong" in result[0]
    assert "ERROR Critical failure" in result[1]
    assert "WARNING Another warning" in result[2]


# ── Test 7: read_oversized — parses JSON lines ──────────────────────────────

def test_read_oversized(wiki_dir):
    """read_oversized: parses JSON lines, falls back to raw string."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    oversized_file = wiki_dir / "oversized_sessions.log"
    lines = [
        json.dumps({"session_id": "abc123", "reason": "too large", "size": 50000}) + "\n",
        "this is not json\n",
        json.dumps({"session_id": "def456", "reason": "timeout", "size": 30000}) + "\n",
    ]
    oversized_file.write_text("".join(lines), encoding="utf-8")

    result = dd.read_oversized()
    assert len(result) == 3
    assert result[0]["session_id"] == "abc123"
    assert result[0]["reason"] == "too large"
    assert result[1]["raw"] == "this is not json"
    assert result[2]["session_id"] == "def456"


# ── Test 7b: read_oversized — stale self-expiry ─────────────────────────────

def test_read_oversized_expires_handled(wiki_dir):
    """read_oversized: entries whose session has a row in .index_v2.db
    (page OR skip-mark) are dropped, duplicates collapse, pending stay."""
    import importlib
    import sqlite3

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    conn = sqlite3.connect(str(wiki_dir / ".index_v2.db"))
    conn.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, indexed_at REAL,"
        " page_slug TEXT, content_hash TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('handled1', 0, 'some-slug', 'abc')")
    conn.execute("INSERT INTO sessions VALUES ('handled2', 0, '', '')")
    conn.commit()
    conn.close()

    oversized_file = wiki_dir / "oversized_sessions.log"
    lines = [
        "2026-08-16 22:45:57 | session=handled1 | msgs=731 | title='a'\n",
        "2026-08-16 22:45:58 | session=pending1 | msgs=2500 | title='b'\n",
        "2026-08-16 22:45:59 | session=handled2 | msgs=900 | title='skip-marked'\n",
        "2026-08-16 22:46:00 | session=handled1 | msgs=731 | title='dup'\n",
        "2026-08-16 22:46:01 | session=pending1 | msgs=2500 | title='b2'\n",
        "garbage line without session\n",
    ]
    oversized_file.write_text("".join(lines), encoding="utf-8")

    result = dd.read_oversized()
    assert len(result) == 2
    assert result[0]["raw"].startswith("2026-08-16 22:45:58 | session=pending1")
    assert result[1] == {"raw": "garbage line without session"}


def test_read_oversized_failopen_bad_db(wiki_dir):
    """read_oversized: index DB without sessions table -> entries as-is."""
    import importlib
    import sqlite3

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    conn = sqlite3.connect(str(wiki_dir / ".index_v2.db"))
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()

    (wiki_dir / "oversized_sessions.log").write_text(
        "2026-08-16 22:45:57 | session=p1 | msgs=10 | title='x'\n", encoding="utf-8"
    )
    assert len(dd.read_oversized()) == 1


def test_read_oversized_json_entries_filtered(wiki_dir):
    """read_oversized: JSON entries with session_id filtered the same way."""
    import importlib
    import sqlite3

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    conn = sqlite3.connect(str(wiki_dir / ".index_v2.db"))
    conn.execute(
        "CREATE TABLE sessions (session_id TEXT PRIMARY KEY, indexed_at REAL,"
        " page_slug TEXT, content_hash TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES ('abc123', 0, 'slug', 'h')")
    conn.commit()
    conn.close()

    oversized_file = wiki_dir / "oversized_sessions.log"
    lines = [
        json.dumps({"session_id": "abc123", "reason": "too large"}) + "\n",
        json.dumps({"session_id": "def456", "reason": "timeout"}) + "\n",
    ]
    oversized_file.write_text("".join(lines), encoding="utf-8")

    result = dd.read_oversized()
    assert len(result) == 1
    assert result[0]["session_id"] == "def456"


# ── Test 8: read_metrics_file — corrupt lines skipped ───────────────────────

def test_read_metrics_file_corrupt_lines(wiki_dir):
    """read_metrics_file: skips malformed JSONL lines."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    base_ts = 1700000000
    lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "counter", "value": 5}),
        "not json",
        json.dumps({"ts": base_ts + 1, "type": "inc", "name": "counter", "value": 3}),
        "{ broken json",
        "",
        json.dumps({"ts": base_ts + 2, "type": "record", "name": "gauge", "value": 42.0}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = dd.read_metrics_file()
    assert result["counter"] == 8  # 5 + 3
    assert result["gauge"] == 42.0


# ── Test 9: _build_api_status — errors_recent populated ─────────────────────

def test_build_api_status_with_errors(wiki_dir):
    """_build_api_status: errors_recent populated from log file."""
    import importlib

    import wiki_v2.dashboard_data as dd
    import wiki_v2.effectiveness as eff
    import wiki_v2.status as st
    importlib.reload(eff)
    importlib.reload(st)
    importlib.reload(dd)

    # Create minimal metrics and events
    base_ts = 1700000000
    metrics_lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "embed_api_calls_total", "value": 10}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")
    (wiki_dir / "wiki_search_events.jsonl").write_text("", encoding="utf-8")

    # Create log file with errors
    logs_dir = wiki_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / "wiki_v2.log"
    log_file.write_text(
        "2026-08-15 10:00:00 ERROR test error line\n"
        "2026-08-15 10:01:00 WARNING test warning line\n",
        encoding="utf-8"
    )

    result = dd._build_api_status()
    assert len(result["errors_recent"]) == 2
    assert "ERROR test error line" in result["errors_recent"][0]
    assert "WARNING test warning line" in result["errors_recent"][1]


# ── Test 10: read_ts — fail-open returns [] ─────────────────────────────────

def test_read_ts_fail_open(wiki_dir):
    """read_ts: returns [] when DB doesn't exist or query fails."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    # No dashboard_metrics.db exists
    result = dd.read_ts("nonexistent", 0, 1000, "minute")
    assert result == []


# ── Test 11: _effectiveness_rating — correct ratings ────────────────────────

def test_effectiveness_rating():
    """_effectiveness_rating: correct rating for various hr/cov combos."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    # score = (hr*0.6 + cov*0.4) * 100
    # hr=1.0, cov=1.0 -> score=100 -> Отлично
    assert dd._effectiveness_rating(1.0, 1.0) == "Отлично"
    # hr=0.8, cov=0.8 -> score=80 -> Отлично
    assert dd._effectiveness_rating(0.8, 0.8) == "Отлично"
    # hr=0.6, cov=0.6 -> score=60 -> Хорошо
    assert dd._effectiveness_rating(0.6, 0.6) == "Хорошо"
    # hr=0.4, cov=0.4 -> score=40 -> Средне
    assert dd._effectiveness_rating(0.4, 0.4) == "Средне"
    # hr=0.2, cov=0.2 -> score=20 -> Низкая
    assert dd._effectiveness_rating(0.2, 0.2) == "Низкая"
    # hr=0.0, cov=0.0 -> score=0 -> Нет данных
    assert dd._effectiveness_rating(0.0, 0.0) == "Нет данных"


# ── Test 12: cached_metrics — TTL cache works ───────────────────────────────

def test_cached_metrics_ttl(wiki_dir, monkeypatch):
    """cached_metrics: returns cached value within TTL, re-reads after TTL."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    # Clear cache
    dd._cache.clear()

    base_ts = 1700000000
    lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "counter_a", "value": 10}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # First call — reads from file
    result1 = dd.cached_metrics()
    assert result1["counter_a"] == 10

    # Second call within TTL — should return cached (same dict object)
    result2 = dd.cached_metrics()
    assert result2 is result1  # same dict object (cached)
    assert result2["counter_a"] == 10

    # Mutate the file and verify cache still holds old value
    lines2 = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "counter_a", "value": 999}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(lines2) + "\n", encoding="utf-8")

    result3 = dd.cached_metrics()
    assert result3 is result1  # still cached
    assert result3["counter_a"] == 10  # old value

    # Now mock time.time to simulate TTL expiry
    original_time = time.time
    call_count = [0]

    def mock_time():
        call_count[0] += 1
        if call_count[0] <= 2:
            return original_time()  # first two calls: within TTL
        return original_time() + 11  # third call: past TTL

    monkeypatch.setattr(time, 'time', mock_time)
    importlib.reload(dd)

    # After TTL expiry, should re-read file
    result4 = dd.cached_metrics()
    assert result4["counter_a"] == 999  # new value from file


# ── Test 13: cached_metrics — empty file returns {} ─────────────────────────

def test_cached_metrics_empty_file(wiki_dir):
    """cached_metrics: returns {} when metrics file is missing."""
    import importlib

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    dd._cache.clear()

    # No metrics file
    result = dd.cached_metrics()
    assert result == {}


# ── Test 14: cached_metrics — thread safety (basic) ─────────────────────────

def test_cached_metrics_thread_safety(wiki_dir):
    """cached_metrics: concurrent calls don't cause errors."""
    import importlib
    import threading

    import wiki_v2.dashboard_data as dd
    importlib.reload(dd)

    dd._cache.clear()

    base_ts = 1700000000
    lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "counter", "value": 42}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    results = []
    errors = []

    def worker():
        try:
            for _ in range(10):
                r = dd.cached_metrics()
                results.append(r.get("counter"))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    assert all(v == 42 for v in results), f"Unexpected values: {results}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
