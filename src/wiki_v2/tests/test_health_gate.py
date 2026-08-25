"""Этап 7 — health-gate embed API перед прогоном индексатора.

Проверяет:
- `nvidia_client.embed_api_available()` — True только при реальном успехе embed;
- `indexer.main()` пропускает прогон (без страниц vecs=0), когда embed API недоступен;
- `indexer.main()` продолжает, когда embed API доступен.
"""
from wiki_v2 import indexer as idx
from wiki_v2.nvidia_client import embed_api_available


class _FakeLock:
    def __init__(self, *a, **k):
        self._ok = True

    def acquire(self):
        return self._ok

    def release(self):
        return None


class _FakeDB:
    def __init__(self, *a, **k):
        self.closed = False

    def close(self):
        self.closed = True


def _patch_main(monkeypatch):
    monkeypatch.setattr(idx, "IndexLock", _FakeLock)
    monkeypatch.setattr(idx, "IndexDB", lambda *a, **k: _FakeDB())
    monkeypatch.setattr(idx, "cleanup_pending", lambda *a, **k: None)


# ── embed_api_available ─────────────────────────────────────────────

def test_embed_api_available_success(monkeypatch):
    monkeypatch.setattr("wiki_v2.nvidia_client.embed", lambda *a, **k: [[0.1, 0.2]])
    assert embed_api_available() is True


def test_embed_api_available_none(monkeypatch):
    monkeypatch.setattr("wiki_v2.nvidia_client.embed", lambda *a, **k: None)
    assert embed_api_available() is False


def test_embed_api_available_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr("wiki_v2.nvidia_client.embed", boom)
    assert embed_api_available() is False


# ── indexer.main health-gate ────────────────────────────────────────

def test_main_skips_when_embed_down(monkeypatch):
    _patch_main(monkeypatch)
    monkeypatch.setattr(idx, "embed_api_available", lambda: False)

    def never(*a, **k):
        raise AssertionError("_resolve_sessions не должен вызываться при недоступном embed")
    monkeypatch.setattr(idx, "_resolve_sessions", never)

    result = idx.main(session_id="sess-x")
    assert result == 0


def test_main_proceeds_when_embed_ok(monkeypatch):
    _patch_main(monkeypatch)
    monkeypatch.setattr(idx, "embed_api_available", lambda: True)
    monkeypatch.setattr(idx, "_resolve_sessions", lambda db, sid=None: [])
    result = idx.main(session_id="sess-x")
    assert result == 0