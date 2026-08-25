"""Tests for wiki_v2.dashboard_memory — закладка «Поиск по памяти».

Фаза D плана exec-plans/PLAN-memory-search-tab.md: адаптер memory_preview
(структура/валидация/fail-open/отсутствие записей), роут GET /api/memory-search
(живой сервер на ephemeral-порту, как в test_dashboard_live), интеграция
render_dashboard (табы, #page-memory, CSS/JS).
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_ROOT))


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _tmp_hermes_home(tmp_path, monkeypatch):
    """Изоляция от живой системы: HERMES_HOME → tmp."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg

    cfg.reload()
    yield


@pytest.fixture()
def dm():
    import wiki_v2.dashboard_memory as mod

    return mod


class _FakePlugin:
    """Мок-плагин вместо wiki-context (monkeypatch через _plugin_mod)."""

    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else {}
        self.exc = exc
        self.calls = []

    def build_preview(self, q):
        self.calls.append(q)
        if self.exc is not None:
            raise self.exc
        return self.result


def _patch_plugin(monkeypatch, dm, fake):
    monkeypatch.setattr(dm, "_plugin_mod", fake)


def _sample_preview(q="тест"):
    return {
        "query": q,
        "gate": {"decision": "show", "tokens": 5, "corpus_hits": 3},
        "hits": [
            {"slug": "a", "title": "A", "score": 0.9,
             "source": "semantic", "path": "a.md"}
        ],
        "card": [{"slug": "b", "title": "B", "path": "b.md", "tags": ["x"]}],
        "main": {
            "slug": "a",
            "title": "A",
            "path": "a.md",
            "chunks": [{"idx": 0, "score": 0.8, "text": "текст чанка"}],
            "chunk_reason": "",
        },
        "inject": "<wiki-memory>A…</wiki-memory>",
        "meta": {
            "duration_ms": 1.5,
            "top_k": 6,
            "api_state": "ok",
            "degraded": False,
            "warnings": [],
        },
    }


# ── memory_preview: структура и валидация ───────────────────────────────────


def test_preview_passthrough_structure(monkeypatch, dm):
    fake = _FakePlugin(result=_sample_preview())
    _patch_plugin(monkeypatch, dm, fake)
    out = dm.memory_preview("тест")
    assert out["gate"]["decision"] == "show"
    assert out["inject"].startswith("<wiki-memory>")
    assert out["hits"][0]["slug"] == "a"
    assert out["main"]["chunks"][0]["score"] == 0.8
    assert out["meta"]["top_k"] == 6
    assert fake.calls == ["тест"]


def test_preview_empty_q_is_skip_without_search(monkeypatch, dm):
    fake = _FakePlugin()
    _patch_plugin(monkeypatch, dm, fake)
    for q in ("", "   ", "\t\n"):
        out = dm.memory_preview(q)
        assert out["gate"]["decision"] == "skip"
        assert out["gate"]["reason"] == "empty"
        assert out["inject"] == ""
        assert out["hits"] == []
    # плагин не вызывается вовсе
    assert fake.calls == []


def test_preview_query_stripped_and_truncated(monkeypatch, dm):
    fake = _FakePlugin(result={"gate": {"decision": "skip"}})
    _patch_plugin(monkeypatch, dm, fake)
    long_q = "  " + "ж" * 600 + "  "
    dm.memory_preview(long_q)
    assert len(fake.calls) == 1
    assert fake.calls[0] == "ж" * dm.MAX_QUERY_LEN


def test_preview_q_len_boundary_kept(monkeypatch, dm):
    fake = _FakePlugin(result={"gate": {"decision": "skip"}})
    _patch_plugin(monkeypatch, dm, fake)
    q = "а" * dm.MAX_QUERY_LEN
    dm.memory_preview(q)
    assert fake.calls[0] == q


# ── memory_preview: fail-open ────────────────────────────────────────────────


def test_preview_plugin_unavailable_is_fail_open(monkeypatch, dm):
    def _boom():
        raise RuntimeError("wiki-context plugin unavailable (not found)")

    monkeypatch.setattr(dm, "_load_plugin", _boom)
    out = dm.memory_preview("тест")
    assert "error" in out
    assert "plugin-unavailable" in out["error"]
    assert out["query"] == "тест"


def test_preview_build_preview_raises_is_fail_open(monkeypatch, dm):
    _patch_plugin(monkeypatch, dm, _FakePlugin(exc=ValueError("boom")))
    out = dm.memory_preview("тест")
    assert "error" in out
    assert "ValueError" in out["error"]


def test_preview_non_dict_result_is_bad_preview(monkeypatch, dm):
    _patch_plugin(monkeypatch, dm, _FakePlugin(result=["not", "a", "dict"]))
    out = dm.memory_preview("тест")
    assert out["error"] == "bad-preview"


# ── read-only гарантия адаптера ──────────────────────────────────────────────


def test_preview_writes_nothing_to_disk(monkeypatch, tmp_path, dm):
    _patch_plugin(monkeypatch, dm, _FakePlugin(result=_sample_preview()))
    dm.memory_preview("тест про индексацию")
    found = [str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")
             if p.name in ("cache.json", "wiki_injects.jsonl")]
    assert found == []


def test_load_plugin_missing_everywhere_raises_clean(monkeypatch, tmp_path, dm):
    monkeypatch.setattr(dm, "_plugin_mod", None)
    monkeypatch.setattr(
        dm, "_plugin_candidates",
        lambda: [str(tmp_path / "missing" / "__init__.py")])
    with pytest.raises(RuntimeError) as ei:
        dm._load_plugin()
    assert "unavailable" in str(ei.value)
    assert dm._plugin_mod is None


# ── Роут GET /api/memory-search (живой сервер, ephemeral-порт) ──────────────


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _start_server(port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["DASHBOARD_PORT"] = str(port)
    proc = subprocess.Popen(
        [sys.executable, "-m", "wiki_v2.dashboard", "--serve"],
        cwd=str(SCRIPTS_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    for _ in range(50):
        if proc.poll() is not None:
            proc.communicate(timeout=2)
            raise RuntimeError(
                f"server exited early: {proc.returncode}"
            )
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=1)
            return proc
        except Exception:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError("server did not start")


def test_route_memory_search_returns_json():
    port = _free_port()
    proc = _start_server(port)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/memory-search?q=%D1%82%D0%B5%D1%81%D1%82")
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200
            assert resp.headers.get("Content-Type") == "application/json"
            assert "no-store" in resp.headers.get("Cache-Control", "")
            data = json.loads(resp.read())
        assert isinstance(data, dict)
        assert data.get("query") == "тест"
    finally:
        proc.terminate()


def test_route_memory_search_empty_q_skips():
    port = _free_port()
    proc = _start_server(port)
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/memory-search?q=", timeout=10) as resp:
            data = json.loads(resp.read())
        assert data["gate"]["decision"] == "skip"
        assert data["inject"] == ""
    finally:
        proc.terminate()


def test_post_memory_search_not_found():
    port = _free_port()
    proc = _start_server(port)
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/memory-search?q=x", data=b"{}",
            method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            raised = False
        except urllib.error.HTTPError as e:
            raised = e.code == 404
        assert raised, "POST должен быть запрещён (GET-only)"
    finally:
        proc.terminate()


# ── Интеграция страницы: табы + контейнеры + CSS/JS ─────────────────────────


def test_render_dashboard_contains_tabs_and_memory_page():
    from wiki_v2.dashboard_page import render_dashboard

    html = render_dashboard()
    assert 'id="tab-console"' in html
    assert 'id="tab-memory"' in html
    assert 'id="page-console"' in html
    assert 'id="page-memory" hidden' in html
    assert "memorySearchSubmit" in html
    assert ".memory-search-form" in html
    # память скрыта по умолчанию, пульт видим раньше памяти в DOM
    assert html.index('id="page-console"') < html.index('id="page-memory"')


def test_render_dashboard_memory_section_markup():
    from wiki_v2.dashboard_page import render_dashboard

    html = render_dashboard()
    assert 'id="memory-q"' in html
    assert 'id="memory-results"' in html
    assert "showMemory()" in html
    assert "showConsole()" in html
    # восстановление последнего запроса после перезагрузки страницы
    assert "wiki3_memq" in html
    # автообновление не перезагружает вкладку памяти (guard в __reloadKeepScroll)
    assert "getElementById('page-memory')" in html
