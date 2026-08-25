# tests/test_plugin_degraded.py — этап 1.5c: wiki-context plugin skip-search on degraded API
"""Tests for stage 1.5c (wiki-context plugin): when API is degraded, the plugin
skips search entirely (no embed/search call).

Implementation under test (plugins/wiki-context/__init__.py _search_wiki):
    from wiki_v2.nvidia_client import api_state
    from wiki_v2.search import search
    if api_state() == "degraded":
        logger.info("wiki-context: API degraded — пропуск поиска")
        return []   # <-- skip search (which would call embed)
    hits, pages = search(query, k=cfg["top_k"])

Scenarios:
  1. test_degraded_skips_search: api_state()=='degraded' -> _search_wiki returns [],
     search() NOT called (patched wiki_v2.search.search to assert not called).
  2. test_normal_still_searches: api_state()=='normal' -> _search_wiki calls search()
     (patched wiki_v2.search.search to return ([], {})), assert called once.
"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import wiki_v2.nvidia_client as nc


# Путь к плагину wiki-context (импортировать через spec, т.к. имя с hyphen).
_PLUGIN_PY = (Path(__file__).resolve().parents[3] / "plugins"
              / "wiki-context" / "__init__.py")

# Реальная папка со скриптами wiki_v2 — чтобы `from wiki_v2... import` работал.
_WIKI_SCRIPTS = str(Path(__file__).resolve().parents[2])


def _load_plugin(monkeypatch, tmp_path):
    """Load the wiki-context plugin via importlib.spec_from_file_location.

    Sets WIKI_SCRIPTS (real wiki_v2 dir) + WIKI_PATH (temp) so the plugin's
    module-level path resolution doesn't touch the real wiki on disk.
    """
    monkeypatch.setenv("WIKI_SCRIPTS", _WIKI_SCRIPTS)
    monkeypatch.setenv("WIKI_PATH", str(tmp_path / "wiki"))
    spec = importlib.util.spec_from_file_location("wiki_context_test", _PLUGIN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Reset circuit-breaker state between tests — prevents degraded leakage."""
    nc._errors_consecutive = 0
    nc._state = "normal"
    yield
    nc._errors_consecutive = 0
    nc._state = "normal"


def test_degraded_skips_search(tmp_path, monkeypatch):
    """Stage 1.5c: API degraded -> plugin skips search, returns [], no embed."""
    plugin = _load_plugin(monkeypatch, tmp_path)

    # Put breaker in degraded mode (realistic: 3 consecutive errors).
    nc._state = "degraded"
    nc._errors_consecutive = 3

    # Patch search in the source module so the `from wiki_v2.search import search`
    # binding inside _search_wiki picks up the mock. Assert it is NOT called.
    with patch("wiki_v2.search.search") as mock_search:
        result = plugin._search_wiki("как починить немотрон на сервере")

    assert result == [], "degraded must return empty list"
    mock_search.assert_not_called(), "degraded must skip search() (no embed call)"


def test_normal_still_searches(tmp_path, monkeypatch):
    """Stage 1.5c: API normal -> plugin calls search() (one call)."""
    plugin = _load_plugin(monkeypatch, tmp_path)

    # Breaker is normal (autouse fixture guarantees this).
    assert nc.api_state() == "normal"

    # Patch search to return empty results; assert it IS called exactly once.
    with patch("wiki_v2.search.search", return_value=([], {})) as mock_search:
        result = plugin._search_wiki("как починить немотрон на сервере")

    mock_search.assert_called_once()
    # search() returns ([], {}) -> plugin's hits list stays empty -> [].
    assert result == []
