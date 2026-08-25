# tests/test_endpoints.py
"""Единый конфиг эндпоинтов (endpoints.yaml → env).

Контракты:
- load() возвращает объединённые дефолты + YAML (YAML выигрывает).
- apply(env) раздаёт стандартные env-переменные setdefault'ом (не перебивает заданные).
- chat_endpoint()/embed_endpoint() возвращают (url, model[, needs_input_type]).
- Fail-open: отсутствие YAML/PyYAML не роняет загрузку (дефолты).
"""
import os

import pytest

from wiki_v2 import endpoints as ep


@pytest.fixture(autouse=True)
def _isolate_file(monkeypatch, tmp_path):
    """Подменяем путь к конфигу на временный, чтобы тесты не читали живой файл."""
    import wiki_v2.endpoints as _ep
    cfg = tmp_path / "endpoints.yaml"
    cfg.write_text(
        "embed:\n"
        "  backend: lmstudio\n"
        "  dim: 1024\n"
        "  lmstudio:\n"
        "    url: http://127.0.0.1:9999/v1/embeddings\n"
        "    model: test-embed\n"
        "chat:\n"
        "  url: http://127.0.0.1:9999/v1/chat/completions\n"
        "  model: test-chat\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_ep, "ENDPOINTS_FILE", cfg)


def test_load_yaml_beats_defaults():
    d = ep.load()
    assert d["embed"]["backend"] == "lmstudio"
    assert d["embed"]["lmstudio"]["model"] == "test-embed"
    assert d["chat"]["model"] == "test-chat"


def test_load_missing_file_returns_defaults(monkeypatch):
    monkeypatch.setattr(ep, "ENDPOINTS_FILE", ep.Path(os.devnull))
    d = ep.load()
    assert d["embed"]["backend"] == "nvidia"  # дефолт
    assert d["chat"]["model"] == "nvidia/nemotron-3-super-120b-a12b"


def test_apply_seeds_env():
    target = {}
    ep.apply(target)
    assert target["WIKI_EMBED_BACKEND"] == "lmstudio"
    assert target["LMSTUDIO_URL"] == "http://127.0.0.1:9999/v1/embeddings"
    assert target["LMSTUDIO_MODEL"] == "test-embed"
    assert target["NVIDIA_API_URL"] == "http://127.0.0.1:9999/v1/chat/completions"
    assert target["NVIDIA_CHAT_MODEL"] == "test-chat"


def test_apply_does_not_override_existing():
    target = {"WIKI_EMBED_BACKEND": "nvidia", "NVIDIA_API_URL": "https://x"}
    ep.apply(target)
    assert target["WIKI_EMBED_BACKEND"] == "nvidia"  # сохранено
    assert target["NVIDIA_API_URL"] == "https://x"


def test_embed_endpoint_lmstudio_no_input_type():
    url, model, needs_input = ep.embed_endpoint()
    assert url == "http://127.0.0.1:9999/v1/embeddings"
    assert model == "test-embed"
    assert needs_input is False


def test_chat_endpoint():
    url, model = ep.chat_endpoint()
    assert url == "http://127.0.0.1:9999/v1/chat/completions"
    assert model == "test-chat"


def test_embed_endpoint_nvidia_needs_input_type(monkeypatch, tmp_path):
    cfg = tmp_path / "ep.yaml"
    cfg.write_text("embed:\n  backend: nvidia\n", encoding="utf-8")
    monkeypatch.setattr(ep, "ENDPOINTS_FILE", cfg)
    url, model, needs_input = ep.embed_endpoint()
    assert needs_input is True
    assert url == ep.DEFAULTS["embed"]["nvidia"]["url"]
    assert model == ep.DEFAULTS["embed"]["nvidia"]["model"]


def test_get_deep_key():
    assert ep.get("embed.backend") == "lmstudio"
    assert ep.get("embed.lmstudio.model") == "test-embed"
    assert ep.get("chat.url").endswith("/chat/completions")
    assert ep.get("nope.missing") is None
