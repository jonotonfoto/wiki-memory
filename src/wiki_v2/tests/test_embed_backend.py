# tests/test_embed_backend.py
"""S2.5.4: EMBED_BACKEND — выбор сервиса эмбеддингов.

Контракты:
- backend='nvidia' → URL NVIDIA, model nv-embedqa-e5-v5, payload С input_type
- backend='lmstudio' → URL LM Studio, model qwen3, payload БЕЗ input_type
- backend='llamaserver' → URL llama-server (11436), model qwen3-q4, payload БЕЗ input_type
- локальный бэкенд недоступен → embed() возвращает None (keyword-only), без fallback
- результат: 1024-dim
"""
import numpy as np
import pytest
import wiki_v2.nvidia_client as nc
from wiki_v2 import config as _cfg
from wiki_v2.nvidia_client import embed


@pytest.fixture(autouse=True)
def _reset_breaker_and_config():
    nc._errors_consecutive = 0
    nc._state = "normal"
    old_backend = _cfg.EMBED_BACKEND
    yield
    nc._errors_consecutive = 0
    nc._state = "normal"
    _cfg.EMBED_BACKEND = old_backend


def _fake_resp(emb):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"index": 0, "embedding": emb}]}
    return R()


def test_nvidia_backend_payload_has_input_type(monkeypatch):
    """nvidia: URL=EMBED_URL, model=DEFAULT, payload содержит input_type."""
    _cfg.EMBED_BACKEND = "nvidia"
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _fake_resp([0.1] * 1024)

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    out = embed("привет", input_type="passage")
    assert out is not None and len(out[0]) == 1024
    assert captured["url"] == nc.EMBED_URL
    assert captured["json"]["model"] == nc.DEFAULT_EMBED_MODEL
    assert captured["json"]["input_type"] == "passage"


def test_lmstudio_backend_no_input_type(monkeypatch):
    """lmstudio: URL=LMSTUDIO_URL, model=qwen3, БЕЗ input_type."""
    _cfg.EMBED_BACKEND = "lmstudio"
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _fake_resp([0.2] * 1024)

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    out = embed("привет", input_type="query")
    assert out is not None and len(out[0]) == 1024
    assert captured["url"] == _cfg.LMSTUDIO_URL
    assert captured["json"]["model"] == _cfg.LMSTUDIO_MODEL
    assert "input_type" not in captured["json"]


def test_llamaserver_backend_no_input_type(monkeypatch):
    """llamaserver: URL=11436, model=qwen3-q4, БЕЗ input_type."""
    _cfg.EMBED_BACKEND = "llamaserver"
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _fake_resp([0.3] * 1024)

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    out = embed("привет")
    assert out is not None and len(out[0]) == 1024
    assert captured["url"] == _cfg.LLAMASERVER_URL
    assert captured["json"]["model"] == _cfg.LLAMASERVER_MODEL
    assert "input_type" not in captured["json"]


def test_local_backend_unavailable_returns_none(monkeypatch):
    """Локальный бэкенд недоступен → None (keyword-only), БЕЗ fallback на NVIDIA."""
    _cfg.EMBED_BACKEND = "lmstudio"

    def fake_post(url, headers=None, json=None, timeout=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    monkeypatch.setattr(nc.time, "sleep", lambda s: None)
    out = embed("привет")
    assert out is None
    # breaker: embed() целиком = 1 ошибка (ретраи внутри — не считаются)
    assert nc._errors_consecutive == 1


def test_embed_returns_1024_dim(monkeypatch):
    """Размерность — 1024 (совместимость с базой). Тестирует РЕАЛЬНЫЙ embed()."""
    _cfg.EMBED_BACKEND = "nvidia"

    def fake_post(url, headers=None, json=None, timeout=None):
        return _fake_resp([0.0] * 1024)

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    out = embed("тест")
    assert out is not None
    assert len(out[0]) == _cfg.EMBED_DIM == 1024


def test_llamaserver_backend_unavailable_returns_none(monkeypatch):
    """llamaserver недоступен → None (keyword-only), без fallback."""
    _cfg.EMBED_BACKEND = "llamaserver"

    def fake_post(url, headers=None, json=None, timeout=None):
        raise ConnectionError("connection refused")

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    monkeypatch.setattr(nc.time, "sleep", lambda s: None)
    out = embed("привет")
    assert out is None
    assert nc._errors_consecutive == 1


def test_unknown_backend_defaults_to_nvidia(monkeypatch):
    """Неизвестный бэкенд → NVIDIA (дефолт, needs_input_type=True)."""
    _cfg.EMBED_BACKEND = "invalid"
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return _fake_resp([0.1] * 1024)

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    out = embed("привет")
    assert out is not None
    assert captured["url"] == nc.EMBED_URL
    assert "input_type" in captured["json"]


def test_embed_payload_truncate_none_and_encoding_float(monkeypatch):
    """Payload содержит truncate='NONE' и encoding_format='float'."""
    _cfg.EMBED_BACKEND = "nvidia"
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _fake_resp([0.1] * 1024)

    monkeypatch.setattr(nc._SESSION, "post", fake_post)
    embed("привет")
    assert captured["json"]["truncate"] == "NONE"
    assert captured["json"]["encoding_format"] == "float"


def test_embed_preserves_order_by_index(monkeypatch):
    """Результаты сортируются по index (ответ может прийти вразнобой)."""
    _cfg.EMBED_BACKEND = "nvidia"
    emb2 = [0.2] * 1024
    emb0 = [0.0] * 1024
    emb1 = [0.1] * 1024

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [
                {"index": 2, "embedding": emb2},
                {"index": 0, "embedding": emb0},
                {"index": 1, "embedding": emb1},
            ]}
    monkeypatch.setattr(nc._SESSION, "post", lambda *a, **k: R())
    out = embed(["a", "b", "c"])
    assert out is not None and len(out) == 3
    assert out[0] == emb0 and out[1] == emb1 and out[2] == emb2
