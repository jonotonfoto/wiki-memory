from __future__ import annotations

import pytest
from wiki_v2.dashboard_health import (
    health_snapshot,
    _collect_embeddings,
    _collect_watchdog,
    _collect_indexer,
    _collect_extractor,
    _collect_errors_24h,
    night_strip_events,
)


def test_health_snapshot_structure() -> None:
    snap = health_snapshot()
    assert isinstance(snap, dict)
    assert "overall" in snap
    assert "components" in snap
    assert "errors_24h" in snap
    assert "embeddings" in snap["components"]
    assert "indexer" in snap["components"]
    assert "extractor" in snap["components"]
    assert "watchdog" in snap["components"]


def test_collectors_fail_open(monkeypatch, tmp_path) -> None:
    # Hermetic: hide any real watchdog pidfile on this machine
    monkeypatch.setenv("WIKI_EMBED_MONITOR_PID", str(tmp_path / "no_such.pid"))
    monkeypatch.setattr("wiki_v2.dashboard_health.embed_endpoint", lambda: (_ for _ in ()).throw(RuntimeError("fail")))
    emb = _collect_embeddings()
    assert emb["status"] == "unknown"

    watch = _collect_watchdog()
    assert watch["status"] in ("unknown", "warn")

    idx = _collect_indexer()
    assert idx["status"] in ("ok", "warn", "error", "unknown")

    ext = _collect_extractor()
    assert ext["status"] in ("ok", "error", "unknown")

    errs = _collect_errors_24h()
    assert "chat_api_errors_24h" in errs or errs["status"] == "unknown"


def test_night_strip_events_fail_open(tmp_path, monkeypatch) -> None:
    # Point WIKI_PATH to a temporary path with no logs/files to ensure fail-open returns []
    monkeypatch.setattr("wiki_v2.config.WIKI_PATH", tmp_path)
    # Hermetic: hide any real watchdog pidfile (mtime would add an event)
    monkeypatch.setenv("WIKI_EMBED_MONITOR_PID", str(tmp_path / "no_such.pid"))
    events = night_strip_events()
    assert isinstance(events, list)
    assert len(events) == 0
