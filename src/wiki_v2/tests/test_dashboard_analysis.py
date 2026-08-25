"""Tests for wiki_v2.dashboard_analysis — effectiveness, trends, indexing,
cache_stats, and problems() zones (not_indexed, not_extracted, oversized).

junk_chunks и merge_fallback НЕ тестируются (по решению пользователя).
Все функции изолированы от реальной БД через monkeypatch/tmp_path.
"""
import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest

# Ensure the package is importable from scripts root
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
    # Create wiki dir so IndexDB / sqlite3 can open files without "unable to open"
    (tmp_path / "wiki").mkdir(parents=True, exist_ok=True)
    import wiki_v2.config as cfg
    cfg.reload()
    yield


# ── Test 1: effectiveness() — корректные значения и рейтинг ─────────────────

def test_effectiveness_returns_correct_values(tmp_path, monkeypatch):
    """effectiveness() returns hit_rate, coverage, and correct rating."""
    import wiki_v2.dashboard_analysis as da

    # Patch on da module (dashboard_analysis imports hit_rate/coverage directly
    # from .effectiveness, so patching eff module won't affect da).
    monkeypatch.setattr(da, "hit_rate", lambda: 0.75)
    monkeypatch.setattr(da, "coverage", lambda: 0.60)

    result = da.effectiveness()

    assert result["hit_rate"] == 0.75
    assert result["coverage"] == 0.60
    assert result["rating"] == "Хорошо"


def test_effectiveness_empty_data(tmp_path, monkeypatch):
    """effectiveness() with 0/0 → rating 'Нет данных'."""
    import wiki_v2.dashboard_analysis as da

    monkeypatch.setattr(da, "hit_rate", lambda: 0.0)
    monkeypatch.setattr(da, "coverage", lambda: 0.0)

    result = da.effectiveness()

    assert result["hit_rate"] == 0.0
    assert result["coverage"] == 0.0
    assert result["rating"] == "Нет данных"


# ── Test 2: trends() — структура и пустой ввод ─────────────────────────────

def test_trends_empty_events(tmp_path, monkeypatch):
    """trends() with no events → 14 daily entries with zeroes."""
    import wiki_v2.dashboard_analysis as da

    monkeypatch.setattr(da, "read_events", lambda: [])

    result = da.trends(days=14)

    assert "hit_rate_daily" in result
    assert "latency_daily" in result
    assert len(result["hit_rate_daily"]) == 14
    assert len(result["latency_daily"]) == 14

    # Each entry has date, hit_rate/avg_latency, total
    for entry in result["hit_rate_daily"]:
        assert "date" in entry
        assert "hit_rate" in entry
        assert "total" in entry
        assert entry["hit_rate"] == 0.0
        assert entry["total"] == 0

    for entry in result["latency_daily"]:
        assert "date" in entry
        assert "avg_latency" in entry


# ── Test 3: indexing() — структура и данные ────────────────────────────────

def test_indexing_structure(tmp_path, monkeypatch):
    """indexing() returns last_indexed_at and recent_queries."""
    import wiki_v2.dashboard_analysis as da

    monkeypatch.setattr(da, "status", lambda: {"last_indexed_at": 123.0})
    monkeypatch.setattr(da, "read_events", lambda: [{"q": f"query_{i}"} for i in range(20)])

    result = da.indexing()

    assert result["last_indexed_at"] == 123.0
    assert len(result["recent_queries"]) == 10
    assert result["recent_queries"][-1]["q"] == "query_19"


def test_indexing_no_last_indexed(tmp_path, monkeypatch):
    """indexing() with no last_indexed_at → None."""
    import wiki_v2.dashboard_analysis as da

    monkeypatch.setattr(da, "status", dict)
    monkeypatch.setattr(da, "read_events", lambda: [])

    result = da.indexing()

    assert result["last_indexed_at"] is None
    assert result["recent_queries"] == []


# ── Test 4: cache_stats() — чистая функция ─────────────────────────────────

def test_cache_stats_normal():
    """cache_stats(7 hits, 3 misses) → hit_rate=0.7."""
    import wiki_v2.dashboard_analysis as da

    m = {"cache_hits_total": 7, "cache_misses_total": 3}
    result = da.cache_stats(m)

    assert result["hits"] == 7
    assert result["misses"] == 3
    assert result["cache_hit_rate"] == 0.7


def test_cache_stats_zero_total():
    """cache_stats(0 hits, 0 misses) → hit_rate=0.0 (not NaN)."""
    import wiki_v2.dashboard_analysis as da

    m = {"cache_hits_total": 0, "cache_misses_total": 0}
    result = da.cache_stats(m)

    assert result["hits"] == 0
    assert result["misses"] == 0
    assert result["cache_hit_rate"] == 0.0


def test_cache_stats_embed_fallback_key():
    """cache_stats falls back to embed_cache_hits_total."""
    import wiki_v2.dashboard_analysis as da

    m = {"embed_cache_hits_total": 5, "cache_misses_total": 5}
    result = da.cache_stats(m)

    assert result["hits"] == 5
    assert result["misses"] == 5
    assert result["cache_hit_rate"] == 0.5


def test_cache_stats_missing_keys():
    """cache_stats with missing keys → 0/0 → 0.0."""
    import wiki_v2.dashboard_analysis as da

    m = {}
    result = da.cache_stats(m)

    assert result["hits"] == 0
    assert result["misses"] == 0
    assert result["cache_hit_rate"] == 0.0


# ── Test 5: problems() — not_indexed ────────────────────────────────────────

def test_problems_not_indexed(tmp_path, monkeypatch):
    """problems()['not_indexed'] returns correct count from mock."""
    import wiki_v2.dashboard_analysis as da

    mock_rows = [{"id": "session_a"}, {"id": "session_b"}]

    # Mock both IndexDB (to avoid real DB open) and get_unindexed_sessions
    mock_db = object()
    monkeypatch.setattr(da, "IndexDB", lambda path: mock_db)
    monkeypatch.setattr(
        da, "get_unindexed_sessions",
        lambda db, limit=200, include_indexed=False: mock_rows,
    )

    result = da.problems()

    assert "not_indexed" in result
    assert result["not_indexed"]["count"] == 2
    assert result["not_indexed"]["key"] == "not_indexed"
    assert result["not_indexed"]["working"] is True
    assert result["not_indexed"]["label"] == "Не индексировано"
    assert result["not_indexed"]["source"] == "get_unindexed_sessions"
    assert result["not_indexed"]["detail"] == "2"


# ── Test 6: problems() — not_extracted ──────────────────────────────────────

def test_problems_not_extracted(tmp_path, monkeypatch):
    """problems()['not_extracted'] counts pages with quality='fallback'."""
    import wiki_v2.config as cfg
    cfg.reload()  # Ensure WIKI_PATH is tmp_path/wiki

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    # Create .index_v2.db with pages table and fallback entry
    db_path = wiki_dir / ".index_v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE pages ("
        "  slug TEXT PRIMARY KEY, "
        "  quality TEXT, "
        "  full_text TEXT"
        ")"
    )
    conn.execute(
        "INSERT INTO pages (slug, quality, full_text) VALUES ('fallback-page', 'fallback', NULL)"
    )
    conn.execute(
        "INSERT INTO pages (slug, quality, full_text) VALUES ('ok-page', 'ok', 'some text')"
    )
    conn.commit()
    conn.close()

    import wiki_v2.dashboard_analysis as da

    result = da.problems()

    assert "not_extracted" in result
    assert result["not_extracted"]["count"] == 1
    assert result["not_extracted"]["key"] == "not_extracted"
    assert result["not_extracted"]["working"] is True
    assert result["not_extracted"]["label"] == "Не извлечено (fallback)"
    assert result["not_extracted"]["source"] == "pages.quality=='fallback'"
    assert result["not_extracted"]["detail"] == "1"


# ── Test 7: problems() — oversized ─────────────────────────────────────────

def test_problems_oversized(tmp_path, monkeypatch):
    """problems()['oversized'] returns correct count from read_oversized."""
    import wiki_v2.dashboard_analysis as da

    monkeypatch.setattr(
        da, "read_oversized",
        lambda: [{"id": "big1"}, {"id": "big2"}, {"id": "big3"}],
    )

    result = da.problems()

    assert "oversized" in result
    assert result["oversized"]["count"] == 3
    assert result["oversized"]["key"] == "oversized"
    assert result["oversized"]["working"] is True
    assert result["oversized"]["label"] == "Отложено из-за длины"
    assert result["oversized"]["source"] == "oversized_sessions.log"
    assert result["oversized"]["detail"] == "3"


# ── Test 8: problems() — junk_chunks и merge_fallback НЕ тестируются ────────

def test_problems_junk_chunks_not_tested(tmp_path, monkeypatch):
    """junk_chunks zone is NOT tested (per user decision)."""
    import wiki_v2.dashboard_analysis as da

    # Mock IndexDB so problems() doesn't fail on real DB
    monkeypatch.setattr(da, "IndexDB", lambda path: object())

    result = da.problems()

    # junk_chunks и merge_fallback могут быть в результате, но мы их
    # не проверяем — это зона, которую пользователь решил НЕ тестировать.
    # Этот тест подтверждает, что problems() не падает и возвращает dict.
    assert isinstance(result, dict)
    assert "not_indexed" in result
    assert "not_extracted" in result
    assert "oversized" in result


# ── Test 9: problems() — общая структура ────────────────────────────────────

def test_problems_structure(tmp_path, monkeypatch):
    """problems() returns dict with exactly 4 keys and correct entry shape.

    merge_fallback removed 2026-08-24: duplicate of not_extracted
    (same pages.quality='fallback' query).
    """
    import wiki_v2.dashboard_analysis as da

    mock_rows = [{"id": "s1"}]
    monkeypatch.setattr(da, "IndexDB", lambda path: object())
    monkeypatch.setattr(
        da, "get_unindexed_sessions",
        lambda db, limit=200, include_indexed=False: mock_rows,
    )
    monkeypatch.setattr(da, "read_oversized", lambda: [])

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    db_path = wiki_dir / ".index_v2.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE pages (slug TEXT PRIMARY KEY, quality TEXT, full_text TEXT)"
    )
    conn.commit()
    conn.close()

    result = da.problems()

    expected_keys = {"not_indexed", "not_extracted", "oversized", "junk_chunks"}
    assert set(result.keys()) == expected_keys

    # Each entry has the correct shape
    for key, entry in result.items():
        assert "key" in entry
        assert "label" in entry
        assert "source" in entry
        assert "working" in entry
        assert "detail" in entry
        if entry["working"]:
            assert "count" in entry
        else:
            assert entry["count"] is None


# ── Test 10: _ts_charts tests ───────────────────────────────────────────────

def test_ts_charts_keys(tmp_path, monkeypatch):
    """_ts_charts returns correct keys and SVG strings."""
    import wiki_v2.dashboard_analysis as da
    monkeypatch.setattr(da, "series_count", lambda *args, **kwargs: [])
    monkeypatch.setattr(da, "query_ts", lambda *args, **kwargs: [])
    monkeypatch.setattr(da, "read_events", lambda: [])
    monkeypatch.setattr(da, "read_injects", lambda: [])

    res = da._ts_charts("1w")
    assert set(res.keys()) == {"inject_relevance", "extraction", "embed_combined", "latency"}
    for k, v in res.items():
        assert isinstance(v, str)
        assert "<svg" in v


def test_ts_charts_inject_relevance_dots(tmp_path, monkeypatch):
    """_ts_charts inject_relevance renders circles when events and injects match."""
    import wiki_v2.dashboard_analysis as da
    now = time.time()
    monkeypatch.setattr(da, "read_events", lambda: [{"ts": now - 60, "top_score": 0.42}])
    monkeypatch.setattr(da, "read_injects", lambda: [{"ts": now - 59}])
    monkeypatch.setattr(da, "series_count", lambda *args, **kwargs: [])
    monkeypatch.setattr(da, "query_ts", lambda *args, **kwargs: [])

    res = da._ts_charts("1w")
    assert "<circle" in res["inject_relevance"]


def test_ts_charts_empty(tmp_path, monkeypatch):
    """_ts_charts with empty data returns 'Нет данных' for charts."""
    import wiki_v2.dashboard_analysis as da
    monkeypatch.setattr(da, "read_events", lambda: [])
    monkeypatch.setattr(da, "read_injects", lambda: [])
    monkeypatch.setattr(da, "series_count", lambda *args, **kwargs: [])
    monkeypatch.setattr(da, "query_ts", lambda *args, **kwargs: [])

    res = da._ts_charts("1w")
    assert "Нет данных" in res["inject_relevance"]
    assert "Нет данных" in res["extraction"]
    assert "Нет данных" in res["embed_combined"]
