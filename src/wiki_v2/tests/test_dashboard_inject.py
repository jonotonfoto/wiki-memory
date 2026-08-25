"""Tests for wiki_v2.dashboard._last_inject and the "Инжект wiki" accordion.

Covers: missing file -> {}, empty file -> {}, single record -> last, multiple
records -> last one wins, malformed lines skipped. And render_dashboard()
includes the accordion (details/summary) with the last query/inject, plus the
"empty" placeholder when there is no inject.
"""
import json
import re
import sys
from pathlib import Path

import pytest

# Ensure the package is importable from project scripts root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME/WIKI_PATH into tmp_path (isolated, no real data)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    yield


@pytest.fixture
def wiki_dir(tmp_path, monkeypatch):
    """Return the wiki dir and ensure config points there."""
    wiki = tmp_path / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    return wiki


def _write(wiki_dir, lines):
    p = wiki_dir / "wiki_injects.jsonl"
    p.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return p


def test_last_inject_missing_file(wiki_dir):
    from wiki_v2.dashboard_sections import _last_inject
    assert _last_inject() == {}


def test_last_inject_empty_file(wiki_dir):
    _write(wiki_dir, [])
    from wiki_v2.dashboard_sections import _last_inject
    assert _last_inject() == {}


def test_last_inject_single(wiki_dir):
    _write(wiki_dir, [
        json.dumps({"ts": 1, "query": "q1", "hits": 3, "inject": "<wiki-memory>a</wiki-memory>"})
    ])
    from wiki_v2.dashboard_sections import _last_inject
    d = _last_inject()
    assert d.get("query") == "q1"
    assert d.get("hits") == 3
    assert "<wiki-memory>" in d.get("inject", "")


def test_last_inject_last_wins(wiki_dir):
    _write(wiki_dir, [
        json.dumps({"ts": 1, "query": "first", "inject": "A"}),
        json.dumps({"ts": 2, "query": "second", "inject": "B"}),
    ])
    from wiki_v2.dashboard_sections import _last_inject
    d = _last_inject()
    assert d.get("query") == "second"
    assert d.get("inject") == "B"


def test_last_inject_skips_bad_lines(wiki_dir):
    _write(wiki_dir, ["not json", "also bad"])
    from wiki_v2.dashboard_sections import _last_inject
    assert _last_inject() == {}


def test_last_inject_mixed_keeps_valid(wiki_dir):
    _write(wiki_dir, [
        "bad line",
        json.dumps({"ts": 5, "query": "ok", "hits": 1, "inject": "X"}),
    ])
    from wiki_v2.dashboard_sections import _last_inject
    d = _last_inject()
    assert d.get("query") == "ok"


def test_render_has_accordion_empty(wiki_dir):
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard(range_="1w")
    assert "Инжект wiki" in html
    assert "<details" in html and "<summary" in html
    assert "Ещё нет инжектов" in html


def test_render_shows_last_inject(wiki_dir):
    _write(wiki_dir, [
        json.dumps({"ts": 1, "query": "мой запрос", "hits": 2,
                    "inject": "<wiki-memory>страница улитка</wiki-memory>"})
    ])
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard(range_="1w")
    assert "Инжект wiki" in html
    assert "мой запрос" in html
    # hits counter is bilingual (.bi spans) since 2026-08-25: label and
    # number are separate nodes — assert order and value instead of a
    # literal "хитов: 2" substring.
    assert re.search(r"хитов.*?:\s*2<", html, re.S)
    assert "страница улитка" in html or "&lt;wiki-memory&gt;" in html


def test_render_has_inject_auto_refresh(wiki_dir):
    """Секция «Инжект wiki» должна обновляться через поллинг /api/injects."""
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard(range_="1w")
    assert 'id="inject-content"' in html
    assert "refreshInject" in html
    assert "setInterval(refreshInject, 5000)" in html
    assert "fetch('/api/injects')" in html
    assert "Запрос пользователя" in html
    assert "Попало в память" in html
