"""Tests for the extraction page-limit feature (2026-08-25).

Covers:
- indexer.MAX_SESSIONS_PER_RUN reads env WIKI_MAX_SESSIONS_PER_RUN
  (default 5, garbage → 5, 0 → clamped to 1).
- dashboard_control.start_extraction(limit=...) pushes the clamped value
  into the child env (overrides "full"; invalid values ignored).

All tests are fully isolated: tmp HERMES_HOME, subprocess.Popen mocked —
no real extraction is ever started.
"""
import importlib
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _tmp_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    monkeypatch.delenv("WIKI_MAX_SESSIONS_PER_RUN", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    yield
    import wiki_v2.config as cfg
    cfg.reload()


class _FakeProc:
    """Minimal Popen stand-in: alive, pid fixed, no side effects."""

    def __init__(self):
        self.pid = 4242
        self.returncode = None

    def poll(self):
        return None


@pytest.fixture
def popen_spy(monkeypatch):
    from wiki_v2 import dashboard_control as dc
    captured: dict = {}
    monkeypatch.setattr(dc.subprocess, "Popen", lambda cmd, **kw: (captured.update(env=kw["env"]) or _FakeProc()))
    monkeypatch.setattr(dc, "indexer_python", lambda: sys.executable)
    return captured


def _reset_status():
    from wiki_v2 import dashboard_control as dc
    dc._status.update({"running": False, "pid": None, "proc": None})


# ── indexer env parsing ──────────────────────────────────────────────────────

def test_indexer_limit_default_5(monkeypatch):
    monkeypatch.delenv("WIKI_MAX_SESSIONS_PER_RUN", raising=False)
    from wiki_v2 import indexer
    importlib.reload(indexer)
    assert indexer.MAX_SESSIONS_PER_RUN == 5


def test_indexer_limit_from_env(monkeypatch):
    monkeypatch.setenv("WIKI_MAX_SESSIONS_PER_RUN", "12")
    from wiki_v2 import indexer
    importlib.reload(indexer)
    assert indexer.MAX_SESSIONS_PER_RUN == 12


def test_indexer_limit_garbage_falls_back(monkeypatch):
    monkeypatch.setenv("WIKI_MAX_SESSIONS_PER_RUN", "garbage")
    from wiki_v2 import indexer
    importlib.reload(indexer)
    assert indexer.MAX_SESSIONS_PER_RUN == 5


def test_indexer_limit_zero_clamped_to_1(monkeypatch):
    monkeypatch.setenv("WIKI_MAX_SESSIONS_PER_RUN", "0")
    from wiki_v2 import indexer
    importlib.reload(indexer)
    assert indexer.MAX_SESSIONS_PER_RUN == 1


# ── start_extraction(limit=...) env wiring ───────────────────────────────────

def test_start_limit_pushed_to_env(popen_spy):
    from wiki_v2 import dashboard_control as dc
    r = dc.start_extraction("normal", limit=7)
    _reset_status()
    assert r.get("ok") is True
    assert popen_spy["env"].get("WIKI_MAX_SESSIONS_PER_RUN") == "7"


def test_start_no_limit_leaves_env(popen_spy):
    from wiki_v2 import dashboard_control as dc
    r = dc.start_extraction("normal")
    _reset_status()
    assert r.get("ok") is True
    assert "WIKI_MAX_SESSIONS_PER_RUN" not in popen_spy["env"]


def test_start_full_mode_high_cap(popen_spy):
    from wiki_v2 import dashboard_control as dc
    r = dc.start_extraction("full")
    _reset_status()
    assert r.get("ok") is True
    assert popen_spy["env"].get("WIKI_MAX_SESSIONS_PER_RUN") == "100000"


def test_start_full_with_limit_overrides(popen_spy):
    from wiki_v2 import dashboard_control as dc
    r = dc.start_extraction("full", limit=3)
    _reset_status()
    assert r.get("ok") is True
    assert popen_spy["env"].get("WIKI_MAX_SESSIONS_PER_RUN") == "3"


def test_start_invalid_limit_ignored(popen_spy):
    from wiki_v2 import dashboard_control as dc
    r = dc.start_extraction("normal", limit="garbage")
    _reset_status()
    assert r.get("ok") is True
    assert "WIKI_MAX_SESSIONS_PER_RUN" not in popen_spy["env"]


def test_start_zero_limit_clamped(popen_spy):
    from wiki_v2 import dashboard_control as dc
    r = dc.start_extraction("normal", limit=0)
    _reset_status()
    assert r.get("ok") is True
    assert popen_spy["env"].get("WIKI_MAX_SESSIONS_PER_RUN") == "1"
