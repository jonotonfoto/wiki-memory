# tests/test_extraction_error_handling.py
"""Регрессионные тесты глубинного разбора обработки ошибок экстракции (2026-08-24).

Покрывают:
1. MAP-retry: исключение чанка больше НЕ глотается молча — _map_chunk_one
   делает retry (раньше extract_chunk_tags глотал всё → retry был мёртвым кодом).
2. Reasoning-empty не отравляет circuit breaker (раньше 3 пустых content подряд
   открывали breaker → mass fast-fail → «экстракция встала» при живой сети).
3. Реальные сетевые ошибки по-прежнему открывают breaker (инвариант не сломан).
4. stop_extraction: таймаут мягкого стопа проваливается в форс-килл
   (раньше общий except возвращал ошибку и до kill дело не доходило).
"""

import pytest
from unittest.mock import MagicMock, patch


# ── 1. MAP retry жив ─────────────────────────────────────────────────────────

def test_extract_chunk_tags_default_swallows_exceptions():
    """По умолчанию (одиночные вызовы) исключение → [] (fail-open)."""
    from wiki_v2.extract import extract_chunk_tags
    with patch("wiki_v2.extract.extract_content", side_effect=RuntimeError("boom")):
        assert extract_chunk_tags("t", "c") == []


def test_extract_chunk_tags_raise_on_error_propagates():
    """raise_on_error=True пробрасывает исключение наверх (для MAP)."""
    from wiki_v2.extract import extract_chunk_tags
    with patch("wiki_v2.extract.extract_content", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            extract_chunk_tags("t", "c", raise_on_error=True)


def test_map_chunk_one_retries_on_transient_exception():
    """Первый вызов упал исключением → retry, второй дал теги → они возвращены."""
    import wiki_v2.extract as ex
    ex._reset_llm_budget()
    calls = []

    def flaky(title, chunk, raise_on_error=False):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient network")
        return ["тег1"]

    with patch("wiki_v2.extract.extract_chunk_tags", side_effect=flaky):
        out = ex._map_chunk_one("Заголовок", "чанк")
    assert out == ["тег1"]
    assert len(calls) == 2


def test_map_chunk_one_returns_empty_after_two_exceptions():
    """Оба попытки упали исключением → [] без выкидывания наверх."""
    import wiki_v2.extract as ex
    ex._reset_llm_budget()
    with patch("wiki_v2.extract.extract_chunk_tags",
               side_effect=RuntimeError("down"), ):
        out = ex._map_chunk_one("Заголовок", "чанк")
    assert out == []


# ── 2/3. Breaker: reasoning-empty ≠ сетевая ошибка ──────────────────────────

_RESP_REASONING_EMPTY = {"choices": [{"message": {"content": "", "reasoning": "мышление..."}}]}


def test_reasoning_empty_does_not_poison_breaker():
    """5 ответов с пустым content (reasoning непустой) НЕ открывают breaker."""
    from wiki_v2 import nvidia_client as nc
    nc._record_success()
    resp = MagicMock()
    resp.raise_for_status.return_value = None
    resp.json.return_value = _RESP_REASONING_EMPTY
    try:
        with patch.object(nc._SESSION, "post", return_value=resp):
            for _ in range(5):
                out = nc.chat_completion("s", "u", max_retries=0,
                                         empty_reasoning_is_error=True)
                assert out is None
        assert nc.api_state() == "normal"
        assert nc._errors_consecutive == 0
    finally:
        nc._record_success()


def test_real_network_errors_still_degrade_breaker():
    """Инвариант: 3 РЕАЛЬНЫЕ сетевые ошибки подряд → degraded (как раньше)."""
    from wiki_v2 import nvidia_client as nc
    nc._record_success()
    try:
        with patch.object(nc._SESSION, "post", side_effect=Exception("conn refused")):
            for _ in range(3):
                out = nc.chat_completion("s", "u", max_retries=0)
                assert out is None
        assert nc.api_state() == "degraded"
    finally:
        nc._record_success()


# ── 4. stop_extraction: таймаут мягкого стопа → форс-килл ────────────────────

def test_stop_extraction_timeout_falls_through_to_kill(tmp_path, monkeypatch):
    """proc.wait(timeout=120) TimeoutExpired → ok=True и форс-килл вызван.

    Раньше общий except возвращал {"ok": False} и kill/снятие флага пропускались.
    """
    import subprocess as sp
    import wiki_v2.config as cfg
    from wiki_v2 import dashboard_control as dc

    monkeypatch.setattr(cfg, "WIKI_PATH", tmp_path)

    class _FakeProc:
        def wait(self, timeout=None):
            raise sp.TimeoutExpired(cmd="wiki_v2.indexer", timeout=timeout)

    kills = []
    monkeypatch.setattr(dc, "_kill_pid", lambda p: kills.append(p))
    monkeypatch.setattr(dc, "_pid_alive", lambda p: True)
    monkeypatch.setattr(dc, "_status", {"proc": _FakeProc(), "pid": 4321})

    result = dc.stop_extraction()
    assert result.get("ok") is True, f"expected ok, got {result}"
    assert kills == [4321], f"форс-килл должен быть вызван с PID 4321, got {kills}"
