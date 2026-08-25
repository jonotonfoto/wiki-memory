"""Единый фасад эндпоинтов и загрузки моделей wiki v3.

Единственный модуль, через который потребители (поиск, индексатор, экстрактор,
backfill, migrate, плагин, дашборд) обращаются к сетевым эндпоинтам и к загрузке
моделей. Фасад решает: какой бэкенд активен, надо ли грузить модель (и какую),
и проксирует вызовы в низкий HTTP-слой nvidia_client.

Слои:
    потребители -> gateway.py       (политика: бэкенд + готовность)
                -> nvidia_client.py (низкий HTTP: breaker / rate-limit / throttle)
                -> реальный сервис   (nvidia / lmstudio / llamaserver)

``endpoints.yaml`` (через endpoints.py) — единственный источник правды о url/моделях.
Фасад сам YAML не парсит, берёт конфиг из endpoints.

Ключевая выгода: чтобы переключить модель/бэкенд, меняется только endpoints.yaml
(+ ensure_*_ready), ни один вызов в search/indexer/плагине не трогается.
Заодно уходят лишние загрузки LM Studio под облачный chat и CPU-эмбеддинг.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import endpoints
from . import nvidia_client as _http
from .logging_setup import logger

__all__ = [
    "api_state",
    "chat_available",
    "chat_completion",
    "chat_endpoint",
    "embed",
    "embed_api_available",
    "embed_backend",
    "ensure_chat_ready",
    "ensure_embed_ready",
]


def _lms_bin() -> Path:
    """Путь к CLI LM Studio `lms` (Windows: ~/.lmstudio/bin/lms.exe)."""
    base_dirs = [
        Path(os.environ.get("USERPROFILE", "")) / ".lmstudio" / "bin",
        Path.home() / ".lmstudio" / "bin",
    ]
    for b in base_dirs:
        for name in ("lms", "lms.exe"):
            c = b / name
            if c.exists():
                return c
    return Path("lms")


def _no_window_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _is_local_lmstudio(url: str) -> bool:
    """True, если url указывает на локальный LM Studio (127.0.0.1:1234)."""
    return "127.0.0.1" in url and "1234" in url


def _lms_load(model: str) -> bool:
    """Загрузить модель через `lms` (fail-open). True, если модель готова.

    Сначала `lms ps` — если модель уже загружена, не дублируем загрузку.
    """
    flags = _no_window_flags()
    try:
        ps = subprocess.run([str(_lms_bin()), "ps"], capture_output=True,
                            timeout=30, creationflags=flags)
        if ps.returncode == 0 and model in ps.stdout.decode("utf-8", errors="replace"):
            return True
    except Exception:
        pass  # не смогли проверить — пробуем загрузить (fail-open)
    try:
        r = subprocess.run([str(_lms_bin()), "load", model], capture_output=True,
                           timeout=300, creationflags=flags)
        if r.returncode != 0:
            logger.warning("[gateway] lms load %s failed: %s", model,
                           r.stderr.decode(errors="replace")[:300])
            return False
        return True
    except Exception as exc:
        logger.warning("[gateway] lms load %s error: %s", model, exc)
        return False


def embed_backend() -> str:
    """Активный бэкенд эмбеддингов: nvidia | lmstudio | llamaserver."""
    return endpoints.get("embed.backend") or "nvidia"


def chat_endpoint() -> tuple[str, str]:
    """(url, model) активного chat/extract эндпоинта."""
    return endpoints.chat_endpoint()


def ensure_embed_ready() -> bool:
    """Убедиться, что embed-модель готова (no-op для nvidia/llamaserver).

    nvidia — облако (не нужен lms); llamaserver — CPU llama.cpp, держит watchdog
    wiki_embed_serve.py. Только lmstudio требует явной загрузки через `lms`.
    """
    if embed_backend() != "lmstudio":
        return True
    try:
        _, model, _ = endpoints.embed_endpoint()
    except Exception:
        model = "peteram4/text-embedding-qwen3-embedding-0.6b@q8_0"
    return _lms_load(model)


def ensure_chat_ready() -> bool:
    """Убедиться, что chat/extract-модель готова (no-op для облака).

    Активный чат сейчас — облако NVIDIA (no-op). Если endpoints.yaml переведён на
    локальный LM Studio (127.0.0.1:1234) — грузим chat.model через `lms`.
    """
    try:
        url, model = chat_endpoint()
    except Exception:
        return True
    if not _is_local_lmstudio(url):
        return True
    return _lms_load(model or "gpt-oss-20b")


# ---------------------------------------------------------------------------
# Фасадные обёртки для потребителей (единый выход в сеть через nvidia_client)
# ---------------------------------------------------------------------------


def embed(texts, input_type: str = "query", **kw):
    """Эмбеддинги: ensure_ready + низкий HTTP-слой. None на сбое (fail-open)."""
    try:
        ensure_embed_ready()
    except Exception as exc:
        logger.warning("[gateway] ensure_embed_ready error: %s", exc)
    return _http.embed(texts, input_type=input_type, **kw)


def chat_completion(system: str, user: str, **kw):
    """Chat/extract: ensure_ready + низкий HTTP-слой. None на сбое (fail-open)."""
    try:
        ensure_chat_ready()
    except Exception as exc:
        logger.warning("[gateway] ensure_chat_ready error: %s", exc)
    return _http.chat_completion(system, user, **kw)


def api_state() -> str:
    return _http.api_state()


def embed_api_available() -> bool:
    return _http.embed_api_available()


def chat_available(force: bool = False) -> bool:
    return _http.chat_available(force=force)
