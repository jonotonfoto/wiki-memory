"""Tests for wiki_v2.dashboard_ts — SQLite time series from jsonl."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure the package is importable from sandbox root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME/WIKI_PATH into tmp_path."""
    # ⚠️ WIKI_PATH задан в СИСТЕМНОМ env (Windows) — cfg.reload() берёт его,
    # а не HERMES_HOME. Поэтому сбрасываем ОБА явно.
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    from wiki_v2.dashboard_ts import _db_path, init_db
    # Remove existing DB file to ensure clean state
    db_path = _db_path()
    if db_path.exists():
        try:
            db_path.unlink()
        except OSError:
            # If we can't delete it (might be locked), just continue - tests will use this path
            pass
    init_db()  # Ensure the DB and tables exist
    yield


# ── Test 1: init_db creates file and tables ─────────────────────────────────

def test_init_db_creates_db_and_tables(tmp_path):
    """init_db() creates dashboard_metrics.db with required tables."""
    from wiki_v2.dashboard_ts import init_db, _db_path

    # Fixture (_tmp_wiki) already called init_db() — just verify tables exist.
    db_path = _db_path()

    # Check tables exist
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        # Get list of tables
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        expected = {"ts_metrics", "ts_metrics_1min", "ts_metrics_1hour", "ts_metrics_1day"}
        assert expected.issubset(tables), f"Missing tables. Expected {expected}, got {tables}"

        # Check UNIQUE constraint on ts_metrics
        cursor.execute("SELECT sql FROM sqlite_master WHERE name='ts_metrics'")
        create_sql = cursor.fetchone()[0]
        assert "UNIQUE(metric_name, ts)" in create_sql, "UNIQUE constraint missing"


# ── Test 2: ingest_jsonl processes valid lines ───────────────────────────────

def test_ingest_jsonl_processes_lines(tmp_path):
    """ingest_jsonl reads valid JSONL and inserts into ts_metrics."""
    from wiki_v2.dashboard_ts import _db_path, ingest_jsonl

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = wiki_dir / "wiki_metrics.jsonl"
    events_file = wiki_dir / "wiki_search_events.jsonl"

    # Write sample metrics lines
    base_ts = 1700000000
    metrics_lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "test_counter", "value": 1}),
        json.dumps({"ts": base_ts + 10, "type": "record", "name": "gauge", "value": 42.5}),
        json.dumps({"ts": base_ts + 20, "type": "inc", "name": "test_counter", "value": 1, "tags": {"env": "test"}}),
    ]
    metrics_file.write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")

    # Write sample events lines
    events_lines = [
        json.dumps({
            "ts": base_ts + 30,
            "type": "search_event",
            "query": "test query",
            "hits": 7,
            "top_slug": "test-page",
            "top_score": 0.9,
            "context_chars": 1200,
            "duration_ms": 45,
            "source": "semantic",
            "session_id": "test-session",
        }),
        json.dumps({
            "ts": base_ts + 40,
            "type": "search_event",
            "query": "another query",
            "hits": 3,
            "top_slug": "other-page",
            "top_score": 0.7,
            "context_chars": 800,
            "duration_ms": 30,
            "source": "keyword",
            "session_id": "test-session-2",
        }),
    ]
    events_file.write_text("\n".join(events_lines) + "\n", encoding="utf-8")

    # Run ingest
    processed = ingest_jsonl(metrics_file, events_file)
    assert processed == 5, f"Expected 5 lines processed, got {processed}"

    # Verify data in ts_metrics
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT metric_name, ts, value, tags FROM ts_metrics ORDER BY ts")
        rows = cursor.fetchall()
        assert len(rows) == 5, f"Expected 5 rows in ts_metrics, got {len(rows)}"

        # Check first row (inc)
        assert rows[0]["metric_name"] == "test_counter"
        assert rows[0]["ts"] == base_ts
        assert rows[0]["value"] == 1.0
        tags = json.loads(rows[0]["tags"]) if rows[0]["tags"] else {}
        assert tags == {}

        # Check second row (record)
        assert rows[1]["metric_name"] == "gauge"
        assert rows[1]["ts"] == base_ts + 10
        assert rows[1]["value"] == 42.5

        # Check third row (inc with tags)
        assert rows[2]["metric_name"] == "test_counter"
        assert rows[2]["ts"] == base_ts + 20
        assert rows[2]["value"] == 1.0
        tags = json.loads(rows[2]["tags"]) if rows[2]["tags"] else {}
        assert tags == {"env": "test"}

        # Check first event (search_event semantic)
        assert rows[3]["metric_name"] == "search.semantic.hits"
        assert rows[3]["ts"] == base_ts + 30
        assert rows[3]["value"] == 7.0
        tags = json.loads(rows[3]["tags"]) if rows[3]["tags"] else {}
        assert tags["query"] == "test query"
        assert tags["duration_ms"] == 45
        assert tags["top_score"] == 0.9

        # Check second event (search_event keyword)
        assert rows[4]["metric_name"] == "search.keyword.hits"
        assert rows[4]["ts"] == base_ts + 40
        assert rows[4]["value"] == 3.0
        tags = json.loads(rows[4]["tags"]) if rows[4]["tags"] else {}
        assert tags["query"] == "another query"
        assert tags["duration_ms"] == 30
        assert tags["top_score"] == 0.7


# ── Test 3: query_ts returns data for time range ────────────────────────────

def test_query_ts_returns_data(tmp_path, _tmp_wiki):
    """query_ts returns correct data for a given metric and time range."""
    from wiki_v2.dashboard_ts import ingest_jsonl, query_ts, summary_buckets, _db_path

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = wiki_dir / "wiki_metrics.jsonl"
    events_file = wiki_dir / "wiki_search_events.jsonl"

    # base_ts кратно 60 и 3600 — чтобы бакет-границы совпадали с range query
    base_ts = 1700002800  # divisible by both 60 and 3600
    metrics_lines = [
        json.dumps({"ts": base_ts, "type": "record", "name": "metric_a", "value": 10.0}),
        json.dumps({"ts": base_ts + 30, "type": "record", "name": "metric_a", "value": 20.0}),
        json.dumps({"ts": base_ts + 60, "type": "record", "name": "metric_a", "value": 30.0}),
        json.dumps({"ts": base_ts + 90, "type": "record", "name": "metric_b", "value": 100.0}),
    ]
    metrics_file.write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")

    # No events needed for this test
    events_file.write_text("", encoding="utf-8")

    ingest_jsonl(metrics_file, events_file)

    # We need to run summary_buckets first to populate the bucket tables
    summary_buckets()

    # Query metric_a from base_ts to base_ts+59 (one minute bucket only)
    result = query_ts("metric_a", base_ts, base_ts + 59, "minute")
    # We expect two points: at base_ts and base_ts+30 (both within the same minute bucket? Actually minute bucket is ts/60*60)
    # base_ts // 60 * 60 = base_ts (if base_ts is multiple of 60)
    # base_ts+30 // 60 * 60 = base_ts (same minute)
    # base_ts+60 // 60 * 60 = base_ts+60 (next minute)
    # So the minute bucket for base_ts and base_ts+30 is the same -> they should be aggregated into one point with average (10+20)/2 = 15
    # Let's check the value.
    assert len(result) == 1, f"Expected 1 row for minute bucket, got {len(result)}"
    assert result[0]["ts"] == base_ts  # the bucket start
    assert result[0]["value"] == 15.0  # average of 10 and 20

    # Query metric_a for hour bucket (larger bucket) from base_ts to base_ts+90
    result = query_ts("metric_a", base_ts, base_ts + 90, "hour")
    # All three points (10,20,30) are in the same hour bucket -> average (10+20+30)/3 = 20
    assert len(result) == 1
    assert result[0]["ts"] == base_ts  # hour bucket start
    assert result[0]["value"] == 20.0

    # Query metric_b (only one point)
    result = query_ts("metric_b", base_ts, base_ts + 90, "hour")
    assert len(result) == 1
    assert result[0]["ts"] == base_ts
    assert result[0]["value"] == 100.0


# ── Test 4: corrupt JSONL lines are skipped (fail-open) ──────────────────────

def test_ingest_jsonl_skips_corrupt_lines(tmp_path, _tmp_wiki):
    """ingest_jsonl skips malformed JSONL lines without raising."""
    from wiki_v2.dashboard_ts import _db_path, ingest_jsonl

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = wiki_dir / "wiki_metrics.jsonl"
    events_file = wiki_dir / "wiki_search_events.jsonl"

    base_ts = 1700000000
    lines = [
        json.dumps({"ts": base_ts, "type": "record", "name": "good_metric", "value": 5.5}),  # valid
        "this is not json",                                                               # invalid
        json.dumps({"ts": base_ts + 10, "type": "record", "name": "another_good", "value": 7.7}),  # valid
        "{ incomplete json",                                                              # invalid
        "",                                                                               # empty line
        json.dumps({"ts": base_ts + 20, "type": "inc", "name": "counter", "value": 1}),   # valid
    ]
    metrics_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    events_file.write_text("", encoding="utf-8")  # no events

    # Should not raise, and should process 3 valid lines
    processed = ingest_jsonl(metrics_file, events_file)
    assert processed == 3, f"Expected 3 valid lines processed, got {processed}"

    # Verify the three valid metrics were inserted
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT metric_name, value FROM ts_metrics ORDER BY ts")
        rows = cursor.fetchall()
        assert len(rows) == 3
        assert rows[0]["metric_name"] == "good_metric"
        assert rows[0]["value"] == 5.5
        assert rows[1]["metric_name"] == "another_good"
        assert rows[1]["value"] == 7.7
        assert rows[2]["metric_name"] == "counter"
        assert rows[2]["value"] == 1.0


# ── Test 5: UPSERT behavior (no duplicates for same metric_name and ts) ─────

def test_ingest_jsonl_upsert_no_duplicates(tmp_path):
    """Ingesting the same (metric_name, ts) twice results in a single row (UPSERT)."""
    from wiki_v2.dashboard_ts import _db_path, ingest_jsonl

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    metrics_file = wiki_dir / "wiki_metrics.jsonl"
    events_file = wiki_dir / "wiki_search_events.jsonl"

    base_ts = 1700000000
    # Same metric and ts, but different value (should upsert)
    line1 = json.dumps({"ts": base_ts, "type": "record", "name": "upsert_test", "value": 100.0})
    line2 = json.dumps({"ts": base_ts, "type": "record", "name": "upsert_test", "value": 200.0})  # same ts, different value

    metrics_file.write_text(line1 + "\n" + line2 + "\n", encoding="utf-8")
    events_file.write_text("", encoding="utf-8")

    processed = ingest_jsonl(metrics_file, events_file)
    assert processed == 2, f"Expected 2 lines processed, got {processed}"

    # Should have exactly one row for (upsert_test, base_ts) with the last value (200.0)
    db_path = _db_path()
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            "SELECT value FROM ts_metrics WHERE metric_name = ? AND ts = ?",
            ("upsert_test", base_ts)
        )
        rows = cursor.fetchall()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0]["value"] == 200.0, f"Expected value 200.0, got {rows[0]['value']}"


if __name__ == "__main__":
    # Allow running the test file directly for debugging
    pytest.main([__file__, "-v"])