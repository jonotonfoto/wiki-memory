# nvidia_client.py
"""NVIDIA API client: chat completions + embeddings. Shared by indexer/search.

Reads the API key from ``NVIDIA_API_KEY`` (env) or from ``NVIDIA_ENV_FILE``
(a .env file), never from a hardcoded path. Endpoints and default models are
module constants and can be overridden via env (``NVIDIA_API_URL``,
``NVIDIA_EMBED_URL``, ``NVIDIA_CHAT_MODEL``, ``NVIDIA_EMBED_MODEL``).
"""
import os
import threading
import time

import requests

from .logging_setup import logger
from .endpoints import apply as _apply_endpoints

# Единый конфиг эндпоинтов (endpoints.yaml) → env. Раздаём при импорте,
# чтобы chat/embed константы ниже читали уже засеянные значения.
_apply_endpoints()

API_URL = os.environ.get("NVIDIA_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
EMBED_URL = os.environ.get("NVIDIA_EMBED_URL", "https://integrate.api.nvidia.com/v1/embeddings")
DEFAULT_CHAT_MODEL = os.environ.get("NVIDIA_CHAT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
DEFAULT_EMBED_MODEL = os.environ.get("NVIDIA_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")

# ── Circuit breaker (этап 1.5, АР-3): reuse TCP + счётчик ошибок подряд ──
# 3 ошибки подряд → режим degraded: вызовы возвращают None БЕЗ HTTP (fast fail).
# Сброс после 1 успеха. Свой счётчик (~20 строк) — без библиотек.
_SESSION = requests.Session()  # reuse TCP-соединений (вместо requests.post каждый раз)
_BREAKER_THRESHOLD = 3
_errors_consecutive = 0
_state = "normal"


def _record_fail() -> None:
    """Учесть ошибку API: 3 подряд → degraded."""
    global _errors_consecutive, _state
    _errors_consecutive += 1
    if _errors_consecutive >= _BREAKER_THRESHOLD:
        _state = "degraded"


def _record_success() -> None:
    """Успешный вызов: сброс счётчика и состояния."""
    global _errors_consecutive, _state
    _errors_consecutive = 0
    _state = "normal"


def api_state() -> str:
    """Текущее состояние API: 'normal' | 'degraded' (для status.py)."""
    return _state


def embed_api_available() -> bool:
    """True если embed API достижим (health-gate индексатора, watchdog).

    Один быстрый пробный эмбеддинг; любой сбой → False. Максимум одна HTTP-попытка
    (max_retries=0, без sleep). Не трогает breaker-состояние (Этап 7).
    """
    try:
        vecs = embed(["_ping"], input_type="query", max_retries=0, timeout=15)
        return bool(vecs)
    except Exception:
        return False


def _fast_fail() -> bool:
    """True если breaker открыт — не делать HTTP, вернуть None."""
    return _state == "degraded"


# ── Rate-limit / защита от блокировки (облако NVIDIA free-tier NIM ~40 RPM) ──
# Пачки параллельных запросов дают 429 и блокировку на часы. Поэтому:
#   - throttle: пауза между chat-вызовами (WIKI_CHAT_MIN_INTERVAL_S),
#   - 429/503 → НЕ долбим (retry усугубляет), ставим кулдаун и fail-open (None).
_CHAT_LOCK = threading.Lock()
_chat_last_ts = 0.0
_chat_blocked_until = 0.0


def _chat_throttle() -> None:
    """Пауза между chat-вызовами, чтобы не словить 429 (потокобезопасно)."""
    global _chat_last_ts
    try:
        interval = float(os.environ.get("WIKI_CHAT_MIN_INTERVAL_S", "0.0"))
    except (TypeError, ValueError):
        interval = 0.0
    if interval <= 0:
        return
    with _CHAT_LOCK:
        now = time.time()
        wait = _chat_last_ts + interval - now
        if wait > 0:
            time.sleep(wait)
        _chat_last_ts = time.time()


def _extend_chat_block(seconds: float) -> None:
    """При 429/503 — не звонить какое-то время (не продлевать блокировку на клиенте)."""
    global _chat_blocked_until
    _chat_blocked_until = time.time() + seconds


def _chat_rate_blocked() -> bool:
    """True если активен кулдаун после rate-limit/блокировки."""
    return time.time() < _chat_blocked_until


# ── Health-probe chat/extract модели ────────────────────────────────────────
_PROBE_SYSTEM = "You are a connectivity probe."
_PROBE_USER = "Reply with exactly the word OK."
_chat_probed: bool | None = None


def chat_available(force: bool = False) -> bool:
    """Проверить, что chat/extract модель доступна и НЕ в rate-limit/блокировке.

    Один лёгкий вызов, кэшируется на процесс (один probe на прогон индексатора).
    Сбой доступа, 429/503 или пустой ответ → False (перед экстракцией → fallback,
    чтобы не долбить заблокированную модель). Fail-open: никогда не кидает.
    """
    global _chat_probed
    if _chat_probed is None or force:
        try:
            out = chat_completion(_PROBE_SYSTEM, _PROBE_USER, max_tokens=8,
                                  temperature=0.0, max_retries=1, timeout=90)
            _chat_probed = bool(out and out.strip())
        except Exception:
            _chat_probed = False
    return _chat_probed


def _default_env_file() -> str:
    return os.environ.get("NVIDIA_ENV_FILE", os.environ.get("HERMES_HOME", "")) + os.sep + ".env"


def load_api_key(env_file: str | None = None) -> str:
    """Return the NVIDIA API key. Priority: env var, then .env file."""
    key = os.environ.get("NVIDIA_API_KEY", "")
    if key:
        return key
    env_file = env_file or _default_env_file()
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NVIDIA_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _headers():
    return {
        "Authorization": f"Bearer {load_api_key()}",
        "Content-Type": "application/json",
    }


def chat_completion(system: str, user: str, model: str = DEFAULT_CHAT_MODEL,
                    max_tokens: int = 2000, temperature: float = 0.3,
                    max_retries: int = 2, timeout: int = 120,
                    empty_reasoning_is_error: bool = False):
    """Return assistant content string, or None after exhausting retries."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # НЕ передаём reasoning_effort и НЕ включаем response_format для reasoning-моделей
        # (gpt-oss и др.): они заставляют модель применять схему к reasoning-потоку,
        # оставляя content пустым (известный баг LM Studio #1773). Нужно дать модели
        # достаточно max_tokens, чтобы она завершила мышление и вывела JSON в content.
    }
    for attempt in range(max_retries + 1):
        if _fast_fail():
            logger.debug("[BREAKER] API degraded — пропуск HTTP (fast fail)")
            return None
        if _chat_rate_blocked():
            logger.warning("[CHAT] rate-limit/блокировка активна — fail-open (None)")
            return None
        _chat_throttle()
        try:
            resp = _SESSION.post(API_URL, headers=_headers(), json=payload, timeout=timeout)
            resp.raise_for_status()
            _record_success()
            # ── metrics: chat_api_calls_total ───────────────────────
            try:
                from wiki_v2 import metrics as _m
                _m.inc("chat_api_calls_total")
            except Exception:
                pass  # fail-open
            msg = resp.json()["choices"][0]["message"]
            content = msg.get("content") or ""
            # Reasoning-модели (gpt-oss и др.) могут класть весь ответ в `reasoning`,
            # оставляя `content` пустым (особенно на длинных промптах).
            if not content.strip():
                reasoning = msg.get("reasoning") or ""
                if empty_reasoning_is_error and reasoning.strip():
                    logger.debug(
                        "chat_completion: content пуст, reasoning непустой — "
                        "empty_reasoning_is_error=True, возвращаю None без retry"
                    )
                    # НЕ _record_fail(): reasoning-empty — НЕ сетевая ошибка (API
                    # отвечает). Раньше 3 таких подряд открывали breaker (degraded)
                    # и ВСЕ дальнейшие chat-вызовы fast-fail в None → массовый
                    # fallback («экстракция встала»), хотя связь была жива.
                    return None
                content = reasoning
            logger.debug("chat_completion raw response (model=%s): %s", model, content)
            return content
        except Exception as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status in (429, 503):
                # rate-limited / перегружено: ретраи УСУГУБЛЯЮТ (продлевают блокировку).
                # Ставим кулдаун и fail-open (None) — экстракция уйдёт в fallback.
                _extend_chat_block(60)
                logger.warning("[CHAT] rate-limit/блокировка HTTP %s — кулдаун 60с, fail-open", status)
                _record_fail()
                return None
            logger.warning("[WARN] chat attempt %d/%d: %s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(5)
    _record_fail()
    # ── metrics: chat_api_errors_total ────────────────────────────
    try:
        from wiki_v2 import metrics as _m
        _m.inc("chat_api_errors_total")
    except Exception:
        pass  # fail-open
    return None


def _embed_endpoint() -> tuple:
    """Вернуть (url, model, needs_input_type) по EMBED_BACKEND из config.

    Локальные бэкенды (LM Studio / llama-server) НЕ принимают input_type
    (проверено пробой 2026-08-12): payload только {model, input}.
    """
    from wiki_v2 import config as _cfg
    backend = getattr(_cfg, "EMBED_BACKEND", "nvidia")
    if backend == "lmstudio":
        return (_cfg.LMSTUDIO_URL, _cfg.LMSTUDIO_MODEL, False)
    if backend == "llamaserver":
        return (_cfg.LLAMASERVER_URL, _cfg.LLAMASERVER_MODEL, False)
    return (EMBED_URL, DEFAULT_EMBED_MODEL, True)


def embed(texts, model: str = DEFAULT_EMBED_MODEL, input_type: str = "query",
          max_retries: int = 2, timeout: int = 60):
    """Return list of embedding vectors (list[float]) for texts, or None on failure.

    Бэкенд выбирается config.EMBED_BACKEND: nvidia (input_type обязателен),
    lmstudio/llamaserver (payload БЕЗ input_type). ОДИН бэкенд на всю базу:
    локальный бэкенд недоступен → None (keyword-only), без fallback на NVIDIA.
    """
    if isinstance(texts, str):
        texts = [texts]
    url, model_name, needs_input_type = _embed_endpoint()
    payload: dict = {"model": model_name, "input": texts,
                     "encoding_format": "float", "truncate": "NONE"}
    if needs_input_type:
        payload["input_type"] = input_type
    for attempt in range(max_retries + 1):
        if _fast_fail():
            logger.debug("[BREAKER] API degraded — пропуск HTTP (fast fail)")
            return None
        try:
            resp = _SESSION.post(url, headers=_headers(), json=payload, timeout=timeout)
            resp.raise_for_status()
            _record_success()
            # ── metrics: embed_api_calls_total ────────────────────────
            try:
                from wiki_v2 import metrics as _m
                _m.inc("embed_api_calls_total")
            except Exception:
                pass  # fail-open
            data = resp.json()["data"]
            data.sort(key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        except Exception as e:
            logger.warning("[WARN] embed attempt %d/%d: %s", attempt + 1, max_retries + 1, e)
            if attempt < max_retries:
                time.sleep(3)
    _record_fail()
    # ── metrics: embed_api_errors_total ──────────────────────────────
    try:
        from wiki_v2 import metrics as _m
        _m.inc("embed_api_errors_total")
    except Exception:
        pass  # fail-open: metrics never breaks embed
    return None
