"""Единый конфиг эндпоинтов Wiki v3 (source of truth → env).

Единственное место, где задаются ВСЕ сетевые эндпоинты и модели:

- ``embed``  — какой сервис считает эмбеддинги (поиск/индексация), см. ``_embed_endpoint``.
- ``chat``   — эндпоинт экстракции/индексации (LLM-генерация), см. ``NVIDIA_API_URL``.

Файл ``endpoints.yaml`` лежит рядом с модулем. ``apply()`` раздаёт его значения по
стандартным env-переменным (``WIKI_EMBED_BACKEND``, ``NVIDIA_API_URL``,
``NVIDIA_CHAT_MODEL``, ``LMSTUDIO_*``, ...) в target-env (по умолчанию ``os.environ``).
Запускающие скрипты/плагины копируют окружение и вызывают ``apply()`` — перемена
эндпоинта/модели происходит в ОДНОМ месте (YAML), а не в каждом лаунчере.

Семантика: ``apply(env)`` заполняет env через ``setdefault`` — уже явно заданные
переменные (напр. передаваемые снаружи) сохраняются, YAML закрывает пробелы.
Fail-open: нет файла / нет PyYAML → ничего не перезаписывается (поведение прежнее).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

# Путь к файлу конфига: рядом с модулем, либо переопределён env-переменной.
ENDPOINTS_FILE = Path(
    os.environ.get("WIKI_ENDPOINTS_FILE", str(Path(__file__).resolve().parent / "endpoints.yaml"))
)

# Дефолты — зеркалируют прежние значения в config.py/nvidia_client.py.
DEFAULTS: dict[str, Any] = {
    "embed": {
        "backend": "nvidia",  # nvidia | lmstudio | llamaserver
        "dim": 1024,
        "nvidia": {
            "url": "https://integrate.api.nvidia.com/v1/embeddings",
            "model": "nvidia/nv-embedqa-e5-v5",
        },
        "lmstudio": {
            "url": "http://127.0.0.1:1234/v1/embeddings",
            "model": "peteram4/text-embedding-qwen3-embedding-0.6b@q8_0",
        },
        "llamaserver": {
            "url": "http://127.0.0.1:11436/v1/embeddings",
            "model": "qwen3-q4",
        },
    },
    "chat": {
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "model": "nvidia/nemotron-3-super-120b-a12b",
        # Параллелизм экстракции и пауза между LLM-вызовами. Облако — без пачек
        # (free-tier NIM ~40 RPM): parallel 1, мин. 1.5s между вызовами.
        "parallel": 4,
        "min_interval_s": 0.0,
        "local": {
            "url": "http://127.0.0.1:1234/v1/chat/completions",
            "model": "gpt-oss-20b",
            "parallel": 4,
            "min_interval_s": 0.0,
        },
        "nvidia_fallback": {
            "url": "https://integrate.api.nvidia.com/v1/chat/completions",
            "model": "nvidia/nemotron-3-super-120b-a12b",
        },
    },
}

# Маппинг плоских env-имён на вложенные ключи.
# (key, default) — default используется, когда в конфиге нет значения.
_ENV_MAP: list[tuple[str, tuple[str, ...] | str, Any]] = [
    ("WIKI_EMBED_BACKEND", ("embed", "backend"), "nvidia"),
    ("EMBED_DIM", ("embed", "dim"), 1024),
    ("NVIDIA_EMBED_URL", ("embed", "nvidia", "url"), None),
    ("NVIDIA_EMBED_MODEL", ("embed", "nvidia", "model"), None),
    ("LMSTUDIO_URL", ("embed", "lmstudio", "url"), None),
    ("LMSTUDIO_MODEL", ("embed", "lmstudio", "model"), None),
    ("LLAMASERVER_URL", ("embed", "llamaserver", "url"), None),
    ("LLAMASERVER_MODEL", ("embed", "llamaserver", "model"), None),
    ("NVIDIA_API_URL", ("chat", "url"), None),
    ("NVIDIA_CHAT_MODEL", ("chat", "model"), None),
    ("WIKI_CHAT_PARALLEL", ("chat", "parallel"), None),
    ("WIKI_CHAT_MIN_INTERVAL_S", ("chat", "min_interval_s"), None),
]


def _deep_merge(base: dict, override: dict) -> dict:
    """Рекурсивно слить *override* поверх *base* (ключи справа выигрывают)."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict | None:
    try:
        import yaml
    except Exception:
        return None

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load() -> dict:
    """Вернуть объединённый конфиг: DEFAULTS ← endpoints.yaml (YAML выигрывает).

    Fail-open: если файл отсутствует/битый/нет PyYAML — вернуть DEFAULTS (старое поведение).
    """
    cfg = _load_yaml(ENDPOINTS_FILE)
    if cfg:
        return _deep_merge(DEFAULTS, cfg)
    return dict(DEFAULTS)


def get(key: str):
    """Простой точечный доступ: ``get("embed.backend")`` → значение или None."""
    cfg = load()
    cur: Any = cfg
    for part in key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def apply(env: dict | None = None) -> None:
    """Раздать значения конфига по env-переменным (setdefault).

    *env* — целевое окружение (по умолчанию ``os.environ``). Уже заданные ключи
    не перезаписываются. Fail-open: ошибка загрузки → ничего не делаем.
    """
    target = env if env is not None else os.environ
    cfg = load()
    for var, path, default in _ENV_MAP:
        value: Any = cfg
        found = True
        if isinstance(path, tuple):
            for part in path:
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    found = False
                    break
        if not found:
            value = default
        if value is None:
            continue
        target.setdefault(var, str(value))
    # Защита от «правильная модель снята с API» (2026-08-26): NVIDIA закрыла
    # chat-модель без предупреждения (410 Gone), а стейл-значение NVIDIA_CHAT_MODEL
    # в унаследованном env перебивало yaml (setdefault выше). Всегда экспортируем
    # цепочку моделей ИЗ yaml (первичная chat.model + chat.fallback) в отдельный
    # ключ, который НЕ зависит от окружения: gateway при 404/410 перебирает её.
    chat = cfg.get("chat") or {}
    chain: list = []
    if chat.get("model"):
        chain.append(chat["model"])
    for m in chat.get("fallback") or []:
        if m and m not in chain:
            chain.append(m)
    if chain:
        target.setdefault("NVIDIA_CHAT_MODEL_FALLBACK", ",".join(chain))


def chat_endpoint() -> tuple[str, str]:
    """Активный chat/extract эндпоинт: (url, model)."""
    cfg = load()
    return cfg["chat"]["url"], cfg["chat"]["model"]


def embed_endpoint() -> tuple[str, str, bool]:
    """Активный embed-эндпоинт: (url, model, needs_input_type).

    ``needs_input_type`` = True только для NVIDIA (Qwen3/bge локально его не принимают).
    """
    cfg = load()
    backend = cfg["embed"]["backend"]
    if backend == "lmstudio":
        return (cfg["embed"]["lmstudio"]["url"], cfg["embed"]["lmstudio"]["model"], False)
    if backend == "llamaserver":
        return (cfg["embed"]["llamaserver"]["url"], cfg["embed"]["llamaserver"]["model"], False)
    return (cfg["embed"]["nvidia"]["url"], cfg["embed"]["nvidia"]["model"], True)
