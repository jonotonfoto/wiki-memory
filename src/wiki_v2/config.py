"""Wiki Memory — cross-platform path & environment resolution.

Replaces the old hardcoded ``windows_config.py`` (Windows) and the inline
``/opt/data`` defaults baked into the Linux/VPS variant. All paths are derived
from environment variables with sensible per-OS defaults, so the same package
runs on Windows desktop, Linux server, or inside a container without edits.

Resolution order (highest priority first):
  1. explicit env var (HERMES_HOME / WIKI_PATH / HERMES_STATE_DB / WIKI_SCRIPTS)
  2. this module's defaults, which are OS-aware via ``os.name``
  3. a ``.env`` file next to the scripts (loaded into os.environ, never
     overwriting already-set keys)

Usage:
    from wiki_v2 import config
    config.configure()                 # one-shot at process start
    wiki = str(config.WIKI_PATH)       # current resolved path

For tests that change env vars, call ``config.reload()`` after setting them
to re-resolve the module-level paths.

"""


from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Shared logger — same name as logging_setup.logger so both modules get
# the exact same Logger object from the logging registry.
logger = logging.getLogger("wiki_v2")
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

IS_WINDOWS = os.name == "nt"
IS_POSIX = not IS_WINDOWS

# Единый конфиг эндпоинтов (endpoints.yaml) — раздаём в env ПЕРВЫМ, до чтения
# констант ниже, чтобы модуль был единственным источником эндпоинтов/моделей.
# setdefault: уже явно заданные env-переменные сохраняются.
from .endpoints import apply as _apply_endpoints  # noqa: E402

_apply_endpoints()


def _default_hermes_home() -> Path:
    if IS_WINDOWS:
        return Path.home() / "AppData" / "Local" / "hermes"
    return Path("/opt/data") if Path("/opt/data").is_dir() else Path.home() / ".hermes"


def _default_python() -> str:
    if IS_WINDOWS:
        return sys.executable
    candidates = [
        str(_default_hermes_home() / ".venv-wiki" / "bin" / "python"),
        "/opt/hermes/.venv/bin/python",
        "python3",
        "python",
    ]
    for c in candidates:
        if c.startswith("/") and os.path.exists(c):
            return c
    return candidates[-1]


def _resolve() -> dict:
    """Compute the current path set from the environment (fresh each call)."""
    home = Path(os.environ.get("HERMES_HOME", str(_default_hermes_home()))).resolve()
    wiki = Path(os.environ.get("WIKI_PATH", str(home / "wiki"))).resolve()
    state = Path(os.environ.get("HERMES_STATE_DB", str(home / "state.db"))).resolve()
    scripts = Path(os.environ.get(
        "WIKI_SCRIPTS", str(Path(__file__).resolve().parent.parent))).resolve()
    agent = Path(os.environ.get("HERMES_AGENT_DIR", str(home / "hermes-agent"))).resolve()
    env = Path(os.environ.get("HERMES_ENV_FILE", str(home / ".env"))).resolve()
    py = os.environ.get("WIKI_PYTHON", _default_python())
    return {
        "HERMES_HOME": home,
        "WIKI_PATH": wiki,
        "STATE_DB": state,
        "SCRIPTS_DIR": scripts,
        "HERMES_AGENT_DIR": agent,
        "ENV_FILE": env,
        "PYTHON": py,
    }


# Module-level current values (refreshed by reload().)
_PATHS = _resolve()

HERMES_HOME: Path = _PATHS["HERMES_HOME"]
WIKI_PATH: Path = _PATHS["WIKI_PATH"]
STATE_DB: Path = _PATHS["STATE_DB"]
SCRIPTS_DIR: Path = _PATHS["SCRIPTS_DIR"]
HERMES_AGENT_DIR: Path = _PATHS["HERMES_AGENT_DIR"]
ENV_FILE: Path = _PATHS["ENV_FILE"]
PYTHON: str = _PATHS["PYTHON"]


def reload() -> None:
    """Re-resolve all module-level paths from the current env.

    Call after changing env vars (e.g. in tests) so ``config.WIKI_PATH`` etc.
    reflect the new values.
    """
    global HERMES_HOME, WIKI_PATH, STATE_DB, SCRIPTS_DIR, HERMES_AGENT_DIR, ENV_FILE, PYTHON
    p = _resolve()
    HERMES_HOME = p["HERMES_HOME"]
    WIKI_PATH = p["WIKI_PATH"]
    STATE_DB = p["STATE_DB"]
    SCRIPTS_DIR = p["SCRIPTS_DIR"]
    HERMES_AGENT_DIR = p["HERMES_AGENT_DIR"]
    ENV_FILE = p["ENV_FILE"]
    PYTHON = p["PYTHON"]


def load_env_file(path: Path | str | None = None) -> None:
    p = Path(path) if path else ENV_FILE
    if not p.exists():
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def apply() -> None:
    os.environ.setdefault("HERMES_HOME", str(HERMES_HOME))
    os.environ.setdefault("WIKI_PATH", str(WIKI_PATH))
    os.environ.setdefault("HERMES_STATE_DB", str(STATE_DB))
    os.environ.setdefault("NVIDIA_ENV_FILE", str(ENV_FILE))

    scripts = str(SCRIPTS_DIR)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    agent = str(HERMES_AGENT_DIR)
    if agent not in sys.path:
        sys.path.insert(0, agent)


def ensure_dirs() -> None:
    for sub in ("entities", "concepts", "comparisons", "queries"):
        (WIKI_PATH / sub).mkdir(parents=True, exist_ok=True)


def configure() -> None:
    load_env_file()
    reload()          # reflect any .env values
    apply()
    ensure_dirs()


# S2.5.1: Controlled taxonomy for tag quality
SCHEMA = [
    "hermes", "plugin", "skill", "cron", "config", "memory", "model",
    "provider", "nvidia", "openrouter", "embedding", "search", "rag",
    "vector", "graph", "project", "person", "org", "tool", "pricing",
]

TAG_SYNONYMS = {
    "subtitle editor": "tool",
    "subtitle_editor": "tool",
    "gemini": "provider",
    "gemini key": "provider",
    "claude": "provider",
    "openai": "provider",
    "llm": "model",
    "language model": "model",
    "эмбеддинг": "embedding",
    "поиск": "search",
    "н.закомолдина": "person",
    "zakomoldina": "person",
    "subtitles editor": "tool",
    "video editor": "tool",
    " кодер": "person",
    "coder": "person",
    "тестер": "person",
    "tester": "person",
    "модель": "model",
    "модель языка": "model",
    "векторная база": "vector",
    "векторное хранилище": "vector",
    "граф знаний": "graph",
    "база знаний": "memory",
    "хранилище": "memory",
    "настройка": "config",
    "конфигурация": "config",
    "скрипт": "tool",
    "скрипты": "tool",
    "плагин": "plugin",
    "плагины": "plugin",
    "навык": "skill",
    "навыки": "skill",
    "задача": "project",
    "задачи": "project",
    "организация": "org",
    "компания": "org",
    "расценки": "pricing",
    "ценообразование": "pricing",
}

# S2.5.2: Query Expansion (расширение запроса через LLM + RRF)
QUERY_EXPANSION_VARIANTS = 4   # LLM генерирует исходный + 3-4 перефраза
QUERY_EXPANSION_ENABLED = True
QUERY_EXPANSION_TTL = 3600     # кэш расширений, секунды
QUERY_EXPANSION_CACHE_MAX = 128  # max записей в кэше (LRU-эвикция)

# S2.5.4: EMBED_BACKEND — какой сервис считает эмбеддинги.
#   'nvidia'    — NVIDIA API (nv-embedqa-e5-v5), платно-лимитный, input_type обязателен
#   'lmstudio'  — LM Studio локально (Qwen3-Embedding-0.6B Q8_0 / bge-m3), БЕЗ input_type
#   'llamaserver' — llama-server на VPS (Qwen3 Q4_K_M, порт 11436 через прокси), БЕЗ input_type
# ОДИН бэкенд на ВСЮ базу (векторы разных моделей несопоставимы даже при 1024-dim).
# Локальные бэкенды недоступны → embed() возвращает None (поиск деградирует в keyword-only),
# НИКОГДА не переключаемся молча на NVIDIA (иначе смешение несопоставимых векторов).
EMBED_BACKEND = os.environ.get("WIKI_EMBED_BACKEND", "nvidia")  # дефолт nvidia (VPS-совместимость)
LMSTUDIO_URL = os.environ.get("LMSTUDIO_URL", "http://127.0.0.1:1234/v1/embeddings")
LMSTUDIO_MODEL = os.environ.get("LMSTUDIO_MODEL", "peteram4/text-embedding-qwen3-embedding-0.6b@q8_0")
LLAMASERVER_URL = os.environ.get("LLAMASERVER_URL", "http://127.0.0.1:11436/v1/embeddings")
LLAMASERVER_MODEL = os.environ.get("LLAMASERVER_MODEL", "qwen3-q4")
EMBED_DIM = 1024  # единая размерность для всех бэкендов (проверка dims при пере-эмбеддинге)

# Экстракция/chat: параллелизм LLM-вызовов и пауза между ними.
# Облако NVIDIA (free-tier NIM ~40 RPM) — НЕ терпит пачек: пачки по 4 потока
# дают 429 и блокировку на часы. Поэтому parallel=1, min_interval_s=1.5.
# Значения приходят из endpoints.yaml (WIKI_CHAT_PARALLEL / WIKI_CHAT_MIN_INTERVAL_S).
CHAT_PARALLEL = int(os.environ.get("WIKI_CHAT_PARALLEL", "4"))
CHAT_MIN_INTERVAL_S = float(os.environ.get("WIKI_CHAT_MIN_INTERVAL_S", "0.0"))

# S2.5.5: Мульти-вектор на страницу
W_MULTIVECTOR_TITLE = float(os.environ.get("W_MULTIVECTOR_TITLE", "1.0"))
W_MULTIVECTOR_SUMMARY = float(os.environ.get("W_MULTIVECTOR_SUMMARY", "0.8"))
W_MULTIVECTOR_TAG = float(os.environ.get("W_MULTIVECTOR_TAG", "0.6"))
HIGH_CONFIDENCE_SCORE = float(os.environ.get("HIGH_CONFIDENCE_SCORE", "0.60"))
CONFIDENCE_WEIGHT = float(os.environ.get("CONFIDENCE_WEIGHT", "0.2"))

# S4.1: default confidence for individual facts when LLM doesn't provide one
WIKI_FACT_CONFIDENCE_DEFAULT = float(os.environ.get("WIKI_FACT_CONFIDENCE_DEFAULT", "0.5"))

# S2.5.10: лимит суммарного контекста чанков при синтезе (символов).
WIKI_CONTEXT_MAX_LEN = int(os.environ.get("WIKI_CONTEXT_MAX_LEN", "2000"))
WIKI_META_ENABLED = True # S4.12: писать meta.json рядом со страницей

# S2.5.13: фактор свежести — мягкий бонус за недавность (не ломает поиск).
RECENCY_DAYS = float(os.environ.get("RECENCY_DAYS", "14"))
RECENCY_BONUS = float(os.environ.get("RECENCY_BONUS", "0.1"))

# S4.6: кривая забывания Эббингауза — спад confidence фактов со временем.
# effective_confidence = confidence × 0.5^(days_since / half_life)
WIKI_FORGET_ENABLED = bool(os.environ.get("WIKI_FORGET_ENABLED", "True") in ("1","true","True"))
WIKI_FORGET_HALF_LIFE_DAYS = float(os.environ.get("WIKI_FORGET_HALF_LIFE_DAYS", "30"))

# S4.3: CoVe — Chain-of-Verification: проверка факта перед записью (опц., выкл по умолчанию).
WIKI_COVE_ENABLED = bool(os.environ.get("WIKI_COVE_ENABLED", "False") in ("1", "true", "True"))
WIKI_COVE_PROMPT = (
    "Оцени, согласуется ли следующий факт с уже известными знаниями. "
    "Ответь ТОЛЬКО одним словом: True / False / Unknown."
)

# S4.2: Write-Gate — новые факты ставятся в очередь подтверждения (опц., выкл по умолчанию).
WIKI_WRITE_GATE_ENABLED = bool(os.environ.get("WIKI_WRITE_GATE_ENABLED", "False") in ("1", "true", "True"))

# S2.5.14: семантический дедуп — порог косинусной близости для слияния.
SEMANTIC_DEDUP_COSINE = float(os.environ.get("SEMANTIC_DEDUP_COSINE", "0.85"))

# S3.4: Пороги мусора
WIKI_GARBAGE_MIN_LEN = int(os.environ.get("WIKI_GARBAGE_MIN_LEN", "20"))

# S2.5.11: тонкая настройка — параметры поиска (не хардкод).
TOP_K = int(os.environ.get("TOP_K", "5"))
WIKI_ADAPTIVE_TOP_K_ENABLED = bool(os.environ.get("WIKI_ADAPTIVE_TOP_K_ENABLED", "True") in ("1","true","True"))
WIKI_MAX_TOP_K = int(os.environ.get("WIKI_MAX_TOP_K", "10"))
RRF_K = int(os.environ.get("RRF_K", "60"))  # подбор 40-80
W_BM25 = float(os.environ.get("W_BM25", "1.3"))  # вес BM25-канала в гибридном RRF (фикс 2026-08-24)
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "500"))
CHUNK_OVERLAP = float(os.environ.get("CHUNK_OVERLAP", "0.15"))


# ── CFG: parameter defaults + get() resolver ───────────────────────────

CFG: dict = {
    # indexer / session
    "MAX_SESSIONS_PER_RUN": 5,
    "IDLE_MINUTES": 32,
    # chunker
    "CHUNK_LIMIT": 8000,
    # search quality gates (единый порог min_query_len — Этап 5.2)
    "WIKI_MIN_QUERY_LEN": 3,
    # lock (from index_lock.py DEFAULT_MAX_AGE)
    "LOCK_MAX_AGE": 3600,
    # garbage collection
    "GARBAGE_MIN_LEN": 20,
    # session status / messages
    "WIKI_MSG_LIMIT": 50,
    # search budget (ms)
    "WIKI_SEARCH_BUDGET_MS": 500,
    # vector tier limits (pages)
    "VECTOR_HOT_LIMIT": 50_000,
    "VECTOR_WARM_LIMIT": 200_000,
    # synthesis context budget
    "WIKI_CONTEXT_MAX_LEN": 2000,
    # S2.5.3: temporary scoring thresholds (deprecated in S2.5.3 — no long-term contracts)
    "MIN_SEMANTIC_SCORE": 0.40,
    "MAX_KEYWORD_SCORE": 0.35,
    "MIN_KEYWORD_SCORE": 0.30,
}


def _cast(value: object, default: object):
    """Try to cast *value* to the type of *default*; return default on failure."""
    if isinstance(default, bool):
        # bool is subclass of int — handle first
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes")
        return bool(value)
    try:
        return type(default)(value)
    except (ValueError, TypeError):
        logger.warning(
            "config.get(%r): cannot cast %r to %s — falling back to default %r",
            None, value, type(default).__name__, default,
        )
        return default


def get(key: str, default=None):
    """Resolve a config parameter.

    Priority chain (highest → lowest):
      1. OS environment variable (``os.environ``)
      2. Value loaded from ``.env`` file (already merged into ``os.environ`` by
         ``load_env_file()`` / ``configure()``)
      3. ``CFG[key]`` default dict
      4. *default* argument

    The env value is cast to the type of the CFG default when available; if
    casting fails a warning is logged and the CFG default (or *default*) is
    returned — never raises.
    """
    # Determine the canonical default for type-casting
    cfg_default = CFG.get(key)

    # 1 / 2: os.environ already contains .env values after load_env_file()
    env_val = os.environ.get(key)
    if env_val is not None and cfg_default is not None:
        return _cast(env_val, cfg_default)
    if env_val is not None and cfg_default is None:
        # No CFG default to infer type from — return raw string
        return env_val

    # 3: CFG dict
    if cfg_default is not None:
        return cfg_default

    # 4: caller-supplied default
    return default