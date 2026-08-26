# tests/test_chat_fallback.py — fallback-цепочка chat-моделей (2026-08-26)
"""NVIDIA закрывает chat-модели без предупреждения (410 Gone), а стейл
NVIDIA_CHAT_MODEL в окружении перебивает yaml (setdefault). Чат должен при
404/410 переключаться на резерв из NVIDIA_CHAT_MODEL_FALLBACK, а не падать.
"""
import os

import pytest
import requests

from wiki_v2 import nvidia_client as nc


class _FakeResp:
    def __init__(self, status, content="привет"):
        self.status_code = status
        self._content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} Error", response=self)

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _noop_metrics(*a, **k):
    return None


def test_endpoints_exports_fallback_chain(monkeypatch):
    """endpoints.apply всегда кладёт в env цепочку из yaml (chat.model)."""
    env = {}
    monkeypatch.setattr(nc, "CHAT_FALLBACK_MODELS", [])
    from wiki_v2 import endpoints
    # не перебиваем уже заданные ключи — проверяем, что наш ключ появился
    endpoints.apply(env)
    assert env.get("NVIDIA_CHAT_MODEL_FALLBACK")


def test_fallback_on_410_uses_next_model(monkeypatch):
    """Первая модель 410 → запрос уходит на резервную и возвращается ответ."""
    seen = []

    class FakeSession:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            seen.append(json["model"])
            if json["model"].endswith("super-49b-v1"):
                return _FakeResp(410)
            return _FakeResp(200, '{"title": "x"}')

    monkeypatch.setattr(nc, "_SESSION", FakeSession())
    monkeypatch.setattr(nc, "_fast_fail", lambda: False)
    monkeypatch.setattr(nc, "_chat_rate_blocked", lambda: False)
    monkeypatch.setattr(nc, "_chat_throttle", lambda: None)
    monkeypatch.setattr(nc, "_record_success", _noop_metrics)
    monkeypatch.setattr(nc, "_record_fail", _noop_metrics)
    monkeypatch.setattr(nc, "CHAT_FALLBACK_MODELS",
                        ["meta/llama-3.2-11b-vision-instruct"])

    out = nc.chat_completion("s", "u",
                             model="nvidia/llama-3.3-nemotron-super-49b-v1")
    assert out and '"title"' in out
    assert len(seen) == 2
    assert seen[0].endswith("super-49b-v1")
    assert seen[-1] == "meta/llama-3.2-11b-vision-instruct"


def test_all_models_dead_returns_none(monkeypatch):
    class FakePost:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            return _FakeResp(410)

    monkeypatch.setattr(nc, "_SESSION", FakePost())
    monkeypatch.setattr(nc, "_fast_fail", lambda: False)
    monkeypatch.setattr(nc, "_chat_rate_blocked", lambda: False)
    monkeypatch.setattr(nc, "_chat_throttle", lambda: None)
    monkeypatch.setattr(nc, "_record_success", _noop_metrics)
    monkeypatch.setattr(nc, "_record_fail", _noop_metrics)
    monkeypatch.setattr(nc, "CHAT_FALLBACK_MODELS", [])

    out = nc.chat_completion("sys", "u", model="nvidia/llama-3.3-nemotron-super-49b-v1")
    assert out is None