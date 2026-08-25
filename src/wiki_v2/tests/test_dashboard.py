"""Tests for wiki_v2.dashboard — render_dashboard() + main() + serve() + JS polling."""
import json
import os
import sys
from pathlib import Path

import pytest

# Серверный тест ниже выключается в CI (см. test_dashboard_live.py).
_CI_SKIP_SERVER = os.environ.get("WIKI_CI_SKIP_SERVER_TESTS") == "1"

# Ensure the package is importable from scripts root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_metrics():
    """Reset in-memory counters before each test."""
    import wiki_v2.metrics as m
    with m._lock:
        m._counters.clear()
    yield
    with m._lock:
        m._counters.clear()


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME/WIKI_PATH into tmp_path."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    monkeypatch.delenv("WIKI_STATE_DB", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()
    yield


# ── Test 1: HTML содержит основные секции ───────────────────────────────────

def test_html_contains_all_sections(tmp_path, monkeypatch):
    """render_dashboard() returns HTML with core dashboard sections."""
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard()

    assert isinstance(html, str)
    assert len(html) > 500

    # Section headings / components
    assert "Компоненты" in html
    assert "Эффективность" in html
    assert "Графики" in html
    assert "База" in html
    assert "API" in html
    assert "Ошибки (24ч)" in html
    assert "Экстракция" in html

    # Chart IDs
    assert 'id="dash-inject-relevance"' in html
    assert 'id="dash-extraction"' in html
    assert 'id="dash-embed-combined"' in html
    assert 'id="dash-chat-calls"' not in html

    # Basic HTML structure
    assert "<!DOCTYPE html>" in html
    assert "<html" in html
    assert "</html>" in html


# ── Test 2: hit_rate=0.7 как JSON в <script> ───────────────────────────────

def test_hit_rate_0_7_as_json(tmp_path, monkeypatch):
    """When hit_rate is 0.7, it appears as JSON in <script>."""
    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    events_file = wiki_dir / "wiki_search_events.jsonl"

    base_ts = 1700000000.0
    lines = []
    for i in range(10):
        obj = {
            "ts": base_ts + i,
            "type": "search_event",
            "query": "test query for hit rate calculation",
            "hits": 1 if i < 7 else 0,
            "top_slug": "test-page",
            "top_score": 0.8,
            "context_chars": 500,
            "duration_ms": 45.0,
            "source": "semantic",
            "session_id": "test-session",
        }
        lines.append(json.dumps(obj, ensure_ascii=False))
    events_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()

    import importlib
    import wiki_v2.dashboard as dsh
    importlib.reload(dsh)

    html = dsh.render_dashboard()

    assert "<script>" in html
    assert "dashboardData" in html

    import re
    match = re.search(r'var dashboardData = ({.*?});', html, re.DOTALL)
    assert match is not None, "dashboardData JSON not found in <script>"

    data = json.loads(match.group(1))
    assert "effectiveness" in data
    assert abs(data["effectiveness"]["hit_rate"] - 0.7) < 0.01


# ── Test 3: пустой ввод → 0 ────────────────────────────────────────────────

def test_empty_input_gives_zeros(tmp_path, monkeypatch):
    """Empty / no data → sections show zeroes."""
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard()

    assert isinstance(html, str)
    assert "Компоненты" in html
    assert "Эффективность" in html
    assert "0.0%" in html

    assert "Traceback" not in html
    assert "Exception" not in html


# ── Test 4: <script> в query заэкранирован ─────────────────────────────────

def test_script_in_query_is_escaped(tmp_path, monkeypatch):
    """A query containing <script> tag is HTML-escaped."""
    from wiki_v2.dashboard import render_dashboard
    malicious_query = '<script>alert("xss")</script>'
    html = render_dashboard(query=malicious_query)

    assert '<script>alert("xss")</script>' not in html
    assert "&lt;script&gt;" in html or "&lt;" in html


# ── Test 5: main() создаёт файл ────────────────────────────────────────────

def test_main_creates_file(tmp_path, monkeypatch, caplog):
    """main() writes wiki_dashboard.html to the wiki directory."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("WIKI_PATH", raising=False)
    import wiki_v2.config as cfg
    cfg.reload()

    from wiki_v2.dashboard import main
    main()

    output_path = tmp_path / "wiki" / "wiki_dashboard.html"
    assert output_path.exists(), f"Dashboard file not created at {output_path}"

    content = output_path.read_text(encoding="utf-8")
    assert len(content) > 500
    assert "<!DOCTYPE html>" in content
    assert "Компоненты" in content


# ── Test 6: render_dashboard содержит data-dash атрибуты ────────────────────

def test_render_dashboard_has_data_dash(tmp_path, monkeypatch):
    """render_dashboard HTML contains data-dash attributes for live metrics."""
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard()

    assert 'data-dash="pages"' in html
    assert 'data-dash="sessions"' in html
    assert 'data-dash="orphans"' in html
    assert 'data-dash="hit_rate"' in html
    assert 'data-dash="coverage"' in html
    assert 'data-dash="embed_calls"' in html
    assert 'data-dash="embed_errors"' in html


# ── Test 7: render_dashboard содержит JS-поллинг ────────────────────────────

def test_render_dashboard_has_js_polling(tmp_path, monkeypatch):
    """render_dashboard HTML contains setInterval polling for /api/status."""
    from wiki_v2.dashboard import render_dashboard
    html = render_dashboard()

    assert "setInterval" in html
    assert "fetch('/api/status')" in html
    assert "5000" in html
    assert ".catch" in html


# ── Test 8: serve() отвечает полными данными ────────────────────────────────

@pytest.mark.skipif(_CI_SKIP_SERVER, reason="server integration disabled in CI")
def test_serve_uses_full_api_status(tmp_path, monkeypatch):
    """serve() /api/status returns FULL data from dashboard_data (not stub)."""
    import subprocess
    import os
    import urllib.request
    import time

    wiki_dir = tmp_path / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    base_ts = 1700000000
    metrics_lines = [
        json.dumps({"ts": base_ts, "type": "inc", "name": "embed_api_calls_total", "value": 100}),
        json.dumps({"ts": base_ts + 1, "type": "inc", "name": "embed_api_errors_total", "value": 2}),
        json.dumps({"ts": base_ts + 4, "type": "inc", "name": "cache_hits_total", "value": 80}),
        json.dumps({"ts": base_ts + 5, "type": "inc", "name": "cache_misses_total", "value": 20}),
    ]
    (wiki_dir / "wiki_metrics.jsonl").write_text("\n".join(metrics_lines) + "\n", encoding="utf-8")
    (wiki_dir / "wiki_search_events.jsonl").write_text("", encoding="utf-8")

    scripts_root = Path(__file__).resolve().parent.parent.parent
    env = dict(os.environ)
    env["HERMES_HOME"] = str(tmp_path)
    env["DASHBOARD_PORT"] = "19142"
    proc = subprocess.Popen(
        [sys.executable, "-m", "wiki_v2.dashboard", "--serve"],
        cwd=str(scripts_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        data = None
        # Холодный Windows-раннер CI: до 60с на старт (питфолл 2026-08-25).
        for _ in range(600):
            if proc.poll() is not None:
                out, err = proc.communicate(timeout=2)
                raise RuntimeError(f"server exited {proc.returncode}: {err.decode(errors='replace')[-500:]}")
            try:
                resp = urllib.request.urlopen("http://127.0.0.1:19142/api/status", timeout=1)
                data = json.loads(resp.read().decode())
                break
            except Exception:
                time.sleep(0.1)
        assert data is not None, "server did not respond"
        assert "search" in data
        assert "recent_queries" in data["search"]
        assert "database" in data
        assert "pages" in data["database"]
        assert data["api"]["embed_calls"] == 100
        assert abs(data["api"]["cache_hit_rate"] - 0.8) < 0.01
    finally:
        proc.terminate()
