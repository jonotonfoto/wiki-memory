# tests/test_nvidia_client.py
import os
from unittest.mock import patch, MagicMock
from wiki_v2.nvidia_client import load_api_key, chat_completion


def test_load_api_key_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-123")
    assert load_api_key() == "test-key-123"


def test_load_api_key_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('OTHER=x\nNVIDIA_API_KEY="file-key-456"\n')
    assert load_api_key(env_file=str(env)) == "file-key-456"


def test_chat_completion_success(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    fake = MagicMock()
    fake.json.return_value = {"choices": [{"message": {"content": "hello"}}]}
    fake.raise_for_status = lambda: None
    with patch("wiki_v2.nvidia_client.requests.post", return_value=fake) as p:
        out = chat_completion("sys", "user", model="m", max_tokens=10)
    assert out == "hello"
    assert p.call_count == 1


def test_chat_completion_retries_then_none(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    import requests as rq
    with patch("wiki_v2.nvidia_client.requests.post",
               side_effect=rq.RequestException("boom")) as p, \
         patch("wiki_v2.nvidia_client.time.sleep"):
        out = chat_completion("s", "u", model="m", max_retries=2)
    assert out is None
    assert p.call_count == 3  # initial + 2 retries
