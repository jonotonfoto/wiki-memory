# tests/test_nvidia_client.py
import json
from unittest.mock import MagicMock, patch

import pytest

import wiki_v2.nvidia_client as nc
from wiki_v2.nvidia_client import chat_completion, load_api_key


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Сброс модульного состояния breaker (иначе degraded из resilience-тестов
    ломает эти)."""
    nc._errors_consecutive = 0
    nc._state = "normal"
    yield
    nc._errors_consecutive = 0
    nc._state = "normal"


@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset in-memory counters before each test."""
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


def test_load_api_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-123")
    assert load_api_key() == "test-key-123"


def test_load_api_key_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OTHER=x\nNVIDIA_API_KEY="file-key-456"\n')
    assert load_api_key(env_file=str(env)) == "file-key-456"


def test_chat_completion_success(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    fake = MagicMock()
    fake.json.return_value = {"choices": [{"message": {"content": "hello"}}]}
    fake.raise_for_status = lambda: None
    with patch("wiki_v2.nvidia_client._SESSION.post", return_value=fake) as p:
        out = chat_completion("sys", "user", model="m", max_tokens=10)
    assert out == "hello"
    assert p.call_count == 1


def test_chat_completion_retries_then_none(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    import requests as rq
    with patch("wiki_v2.nvidia_client._SESSION.post",
               side_effect=rq.RequestException("boom")) as p, \
         patch("wiki_v2.nvidia_client.time.sleep"):
        out = chat_completion("s", "u", model="m", max_retries=2)
    assert out is None
    assert p.call_count == 3  # initial + 2 retries


def test_embed_success_increments_calls_total(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    fake = MagicMock()
    fake.json.return_value = {"data": [{"index": 0, "embedding": [0.1] * 8}]}
    fake.raise_for_status = lambda: None
    with patch("wiki_v2.nvidia_client._SESSION.post", return_value=fake) as p:
        # First call
        out1 = nc.embed(["x"], input_type="query")
        assert out1 == [[0.1] * 8]
        # Second call
        out2 = nc.embed(["y"], input_type="query")
        assert out2 == [[0.1] * 8]
    assert p.call_count == 2
    from wiki_v2 import metrics as m
    snap = m.snapshot()
    assert snap["embed_api_calls_total"] == 2
    # Verify JSONL contains the increment
    metrics_path = m._metrics_path()
    with open(metrics_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    # Find the line with embed_api_calls_total
    found = False
    for line in lines:
        data = json.loads(line.strip())
        if data.get("name") == "embed_api_calls_total":
            found = True
            break
    assert found, "embed_api_calls_total not found in metrics JSONL"


def test_embed_error_increments_errors_total(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    import requests as rq
    with patch("wiki_v2.nvidia_client._SESSION.post",
               side_effect=rq.RequestException("boom")) as p:
        out = nc.embed(["x"], input_type="query", max_retries=0)
        assert out is None
    assert p.call_count == 1
    from wiki_v2 import metrics as m
    snap = m.snapshot()
    assert snap["embed_api_errors_total"] == 1


def test_chat_success_increments_calls_total(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    fake = MagicMock()
    fake.json.return_value = {"choices": [{"message": {"content": "ok"}}]}
    fake.raise_for_status = lambda: None
    with patch("wiki_v2.nvidia_client._SESSION.post", return_value=fake) as p:
        out = nc.chat_completion("sys", "user", model="m", max_tokens=5)
        assert out == "ok"
    assert p.call_count == 1
    from wiki_v2 import metrics as m
    snap = m.snapshot()
    assert snap["chat_api_calls_total"] == 1


def test_chat_error_increments_errors_total(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    import requests as rq
    with patch("wiki_v2.nvidia_client._SESSION.post",
               side_effect=rq.RequestException("boom")) as p, \
         patch("wiki_v2.nvidia_client.time.sleep"):
        out = nc.chat_completion("sys", "user", model="m", max_tokens=5, max_retries=0)
        assert out is None
    assert p.call_count == 1  # initial + 0 retries
    from wiki_v2 import metrics as m
    snap = m.snapshot()
    assert snap["chat_api_errors_total"] == 1


def test_search_records_duration_ms(monkeypatch, tmp_path):
    # Use the pattern from test_search.py: monkeypatch the INDEX_DB in the search module
    import wiki_v2.search as search_mod
    from wiki_v2.index_db import IndexDB

    # Prepare a temporary index database
    db_path = str(tmp_path / "test.index_v2.db")
    monkeypatch.setattr(search_mod, "INDEX_DB", db_path)

    # Seed the database with at least one page so that search does not return early
    db = IndexDB(db_path)
    db.upsert_page(
        slug="test-slug",
        title="Test Title",
        section="test",
        path="test/path.md",
        content_hash="dummyhash",
        summary="Test Summary",
        quality="ok",
        full_text="Test full text",
    )
    db.close()

    # Now run the search and verify that duration is recorded
    import time
    t0 = time.time()
    results = search_mod.search("тестовый запрос длинный")
    t1 = time.time()

    # The function should have recorded the duration
    from wiki_v2 import metrics as m
    snap = m.snapshot()
    duration = snap.get("search_duration_ms", 0)
    # We expect duration to be a number and non-negative
    assert isinstance(duration, (int, float))
    assert duration >= 0
    # Additionally, we can check that the metric was recorded (i.e., key exists)
    assert "search_duration_ms" in snap


def test_chat_completion_reasoning_empty_no_retry(monkeypatch):
    """empty_reasoning_is_error=True: content пуст, reasoning непустой → None без retry."""
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    fake = MagicMock()
    fake.json.return_value = {
        "choices": [{"message": {"content": "", "reasoning": "некоторый поток рассуждений"}}]
    }
    fake.raise_for_status = lambda: None
    with patch("wiki_v2.nvidia_client._SESSION.post", return_value=fake) as p, \
         patch("wiki_v2.nvidia_client.time.sleep"):
        out = chat_completion("s", "u", model="m", max_tokens=5, max_retries=2, empty_reasoning_is_error=True)
    assert out is None
    assert p.call_count == 1


def test_chat_completion_reasoning_empty_default_still_returns_reasoning(monkeypatch):
    """empty_reasoning_is_error=False (по умолчанию): reasoning подставляется в content."""
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    fake = MagicMock()
    fake.json.return_value = {
        "choices": [{"message": {"content": "", "reasoning": "некоторый поток рассуждений"}}]
    }
    fake.raise_for_status = lambda: None
    with patch("wiki_v2.nvidia_client._SESSION.post", return_value=fake) as p, \
         patch("wiki_v2.nvidia_client.time.sleep"):
        out = chat_completion("s", "u", model="m", max_tokens=5, max_retries=2)
    assert out == "некоторый поток рассуждений"
    assert p.call_count == 1