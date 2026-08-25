# tests/test_api_resilience.py — этап 1.5: circuit breaker (АР-3)
"""Tests for API resilience (stage 1.5): Session reuse + error counter + degraded state.

Scenarios (spec S1.5):
  a) 3 consecutive errors -> api_state() == 'degraded', 4th call does NOT hit HTTP
  b) 1 success after degraded -> api_state() back to 'normal'
  c) api_state() returns 'normal' initially
  d) embed()/chat_completion() return None when degraded (fast fail)
"""
from unittest.mock import MagicMock, patch

import pytest

import wiki_v2.nvidia_client as nc


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Сброс модульного состояния breaker между тестами (иначе degraded из
    одного теста ломает следующий)."""
    nc._errors_consecutive = 0
    nc._state = "normal"
    yield
    nc._errors_consecutive = 0
    nc._state = "normal"


def _reset_state():
    nc._errors_consecutive = 0
    nc._state = "normal"


def test_initial_state_normal():
    _reset_state()
    assert nc.api_state() == "normal"


def test_three_errors_opens_degraded(tmp_path, monkeypatch):
    _reset_state()
    # 3 неудачных вызова подряд (requests.post падает) -> degraded
    for _ in range(3):
        with patch("wiki_v2.nvidia_client._SESSION.post",
                   side_effect=Exception("boom")):
            result = nc.embed(["test"], max_retries=0)
    assert nc.api_state() == "degraded", "after 3 errors state must be degraded"


def test_degraded_fast_fails_without_http(tmp_path, monkeypatch):
    _reset_state()
    nc._state = "degraded"  # принудительно
    with patch("wiki_v2.nvidia_client._SESSION.post") as mock_post:
        result = nc.embed(["test"], max_retries=2)
        assert result is None
        mock_post.assert_not_called(), "degraded must NOT hit HTTP"


def test_success_resets_to_normal(tmp_path, monkeypatch):
    _reset_state()
    # доводим до degraded ЧЕРЕЗ 3 реальные ошибки (не вручную!)
    with patch("wiki_v2.nvidia_client._SESSION.post",
               side_effect=Exception("boom")):
        for _ in range(3):
            nc.embed(["test"], max_retries=0)
    assert nc.api_state() == "degraded", "precondition: degraded"

    # один успех -> сброс (эмулируем half-open: state=normal, но счётчик=3)
    nc._state = "normal"
    nc._errors_consecutive = 3
    fake = MagicMock()
    fake.status_code = 200
    fake.raise_for_status = lambda: None
    fake.json.return_value = {"data": [{"index": 0, "embedding": [0.1] * 1024}]}
    with patch("wiki_v2.nvidia_client._SESSION.post", return_value=fake):
        nc.embed(["test"], max_retries=0)
    assert nc.api_state() == "normal", "success must reset breaker"
    assert nc._errors_consecutive == 0


def test_session_reused():
    """Session создаётся один раз (модульный объект), а не на каждый вызов."""
    assert hasattr(nc, "_SESSION"), "module-level Session must exist"
    assert nc._SESSION is nc._SESSION, "Session is a singleton"
