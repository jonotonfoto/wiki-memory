"""Tests for wiki_v2.dashboard — serve() HTTP server (этап 1.1).

Server запускается как ОТДЕЛЬНЫЙ процесс через `python -m wiki_v2.dashboard
--serve` (тот же путь, что критерий Ф1) и завершается через terminate() в
finally. Это надёжно и не блокирует pytest-процесс (serve_forever живёт в
сабпроцессе).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

# Ensure the package is importable from scripts root
SCRIPTS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SCRIPTS_ROOT))

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
    """Point HERMES_HOME into tmp_path."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg

    cfg.reload()
    yield


# ── Helpers ─────────────────────────────────────────────────────────────────


def _start_server(port: int, host: str = "127.0.0.1") -> subprocess.Popen:
    """Launch the dashboard server as a separate subprocess.

    Returns the Popen handle; the caller must call terminate() when done.
    """
    env = dict(os.environ)
    env["HERMES_HOME"] = str(Path(os.environ.get("HERMES_HOME", SCRIPTS_ROOT)))
    env["DASHBOARD_PORT"] = str(port)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "wiki_v2.dashboard",
            "--serve",
        ],
        cwd=str(SCRIPTS_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait until the server is responsive
    for _ in range(50):  # up to 5 s
        if proc.poll() is not None:
            out, err = proc.communicate(timeout=2)
            raise RuntimeError(
                f"Server subprocess exited early: code {proc.returncode}\n"
                f"STDOUT: {out.decode(errors='replace')[-800:]}\n"
                f"STDERR: {err.decode(errors='replace')[-800:]}"
            )
        try:
            urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=1)
            return proc
        except Exception:
            time.sleep(0.1)
    proc.terminate()
    raise RuntimeError(f"Server did not start on port {port} within 5 s")


def _get(path: str, port: int, host: str = "127.0.0.1") -> tuple[int, bytes]:
    """Perform a GET and return (status_code, body_bytes)."""
    url = f"http://{host}:{port}{path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, resp.read()


# ── Test 1: GET /api/status → 200 + валидный JSON ──────────────────────────


def test_api_status_returns_200_json():
    """GET /api/status returns HTTP 200 with valid JSON body."""
    port = 19121
    proc = _start_server(port)
    try:
        status, body = _get("/api/status", port)
        assert status == 200
        data = json.loads(body)
        # Полная структура из dashboard_data (не заглушка этапа 1.1)
        assert "generated_at" in data
        assert isinstance(data["generated_at"], int)
        assert "health" in data
        assert "effectiveness" in data
        assert "database" in data
        assert "api" in data
        assert "search" in data
        assert "lmstudio" in data
    finally:
        proc.terminate()


# ── Test 2: GET / → 200 + HTML содержит "<html" ────────────────────────────


def test_root_returns_200_html():
    """GET / returns HTTP 200 with HTML containing '<html'."""
    port = 19122
    proc = _start_server(port)
    try:
        status, body = _get("/", port)
        assert status == 200
        text = body.decode("utf-8")
        assert "<html" in text
        assert "Wiki Memory v3" in text
    finally:
        proc.terminate()


# ── Test 3: fail-open — сервер не падает без файлов/БД ─────────────────────


def test_fail_open_no_db(tmp_path, monkeypatch):
    """Server returns 200 even when no DB / events files exist."""
    # tmp_path is empty — no wiki files created
    port = 19123
    proc = _start_server(port)
    try:
        status, body = _get("/api/status", port)
        assert status == 200
        data = json.loads(body)
        # Should not contain a traceback or Exception
        assert "Traceback" not in json.dumps(data)
        assert "Exception" not in json.dumps(data)
    finally:
        proc.terminate()


# ── Test 4: Content-Type заголовки ─────────────────────────────────────────


def test_content_types():
    """/api/status → application/json, / → text/html."""
    port = 19124
    proc = _start_server(port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.headers.get("Content-Type") == "application/json"

        req2 = urllib.request.Request(f"http://127.0.0.1:{port}/")
        with urllib.request.urlopen(req2, timeout=5) as resp:
            assert "text/html" in resp.headers.get("Content-Type", "")
    finally:
        proc.terminate()


# ── Test 5: Content-Length корректен ───────────────────────────────────────


def test_content_length():
    """Content-Length header matches body length."""
    port = 19125
    proc = _start_server(port)
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            declared = int(resp.headers.get("Content-Length", 0))
            assert declared == len(body)
    finally:
        proc.terminate()
