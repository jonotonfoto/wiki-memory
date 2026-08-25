"""wiki-session-finalize — мгновенная доиндексация завершённой сессии.

Подписан на хук on_session_finalize (срабатывает при /new, /reset, истечении
сессии). При получении session_id запускает индексатор в фоне (subprocess,
не блокирует агента), чтобы закрытая длинная сессия сразу попала в wiki,
не дожидаясь cron (каждые 3 часа).

Fail-open: любая ошибка логируется и проглатывается — хук никогда не ломает агента.

Паттерн пути скопирован из wiki-context (env-зависимый, Windows + Linux).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _resolve_home() -> Path:
    h = os.environ.get("HERMES_HOME", "")
    if h:
        return Path(h)
    return Path.home() / "AppData" / "Local" / "hermes"


_HOME = _resolve_home()

# Папка со скриптами wiki_v2 (родитель пакета). Приоритет: env → локальный проект → /opt/data
WIKI_SCRIPTS = os.environ.get("WIKI_SCRIPTS", "")
if not WIKI_SCRIPTS:
    _proj = Path(__file__).resolve().parents[2] / "scripts"  # plugins/wiki-session-finalize -> scripts
    if (_proj / "wiki_v2").is_dir():
        WIKI_SCRIPTS = str(_proj)
    elif (_HOME / "scripts" / "wiki_v2").is_dir():
        WIKI_SCRIPTS = str(_HOME / "scripts")
    else:
        WIKI_SCRIPTS = "/opt/data/scripts"

# Python для запуска индексатора. Приоритет: env → venv desktop → системный python
PYTHON = os.environ.get("WIKI_PYTHON", "")
if not PYTHON:
    _candidates = [
        str(_HOME / "hermes-agent" / "venv" / "Scripts" / "python.exe"),  # Windows desktop
        str(_HOME / "hermes-agent" / "venv" / "bin" / "python"),          # Linux
        "python3",
        "python",
    ]
    for c in _candidates:
        if os.path.exists(c):
            PYTHON = c
            break
    else:
        PYTHON = _candidates[-1]

# Скрипт-обёртка (Windows-аналог run_wiki_indexer.sh). Если нет — fallback на модуль.
_INDEXER_SCRIPT = os.path.join(WIKI_SCRIPTS, "run_wiki_indexer.py")


def _run_indexer(session_id: str) -> None:
    """Запустить индексатор в фоне для одной сессии. Никогда не бросает исключение."""
    try:
        cmd = [PYTHON, "-m", "wiki_v2.indexer", "--session", session_id]
        # рабочая директория = scripts (для импортов)
        cwd = WIKI_SCRIPTS
        # v3: индексация локально через LM Studio (не NVIDIA). ВСЕ эндпоинты
        # берём из единого конфига endpoints.yaml (wiki_v2.endpoints.apply),
        # а не хардкодим здесь. Env наследуется subprocess'ом.
        env = os.environ.copy()
        try:
            sys.path.insert(0, WIKI_SCRIPTS)
            from wiki_v2.endpoints import apply as _endpoints_apply
            _endpoints_apply(env)
        except Exception as _eexc:
            logger.warning("wiki-session-finalize: не удалось применить endpoints.yaml (продолжаем): %s", _eexc)
        # v3: перед запуском индексатора убедиться, что chat/extract-модель готова
        # через единый фасад gateway. Активный чат — облако NVIDIA (no-op, LM Studio
        # НЕ грузим); только если endpoints.yaml переведён на локальный LM Studio —
        # ensure_chat_ready поднимет gpt-oss-20b. Тот же фасад, что у дашборда.
        try:
            sys.path.insert(0, WIKI_SCRIPTS)
            from wiki_v2.gateway import ensure_chat_ready
            ensure_chat_ready()
            logger.info("wiki-session-finalize: extract-модель готова (gateway)")
        except Exception as _mexc:
            logger.warning("wiki-session-finalize: не удалось обеспечить готовность extract-модели (продолжаем): %s", _mexc)
        # фоновый запуск: не блокируем агента
        subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info("wiki-session-finalize: запущен индексатор для session=%s", session_id)
    except Exception as e:
        logger.warning("wiki-session-finalize: не удалось запустить индексатор: %s", e)


def on_session_finalize(*, session_id: Any = None, platform: Any = None, **_: Any) -> None:
    """Хук: при /new или /reset — доиндексировать закрытую сессию в фоне."""
    try:
        sid = str(session_id or "").strip()
        if not sid:
            logger.debug("wiki-session-finalize: нет session_id, пропуск")
            return
        # Не трогаем очень короткие/временные id без содержимого — фильтрует сам индексатор.
        logger.info("wiki-session-finalize: session_id=%s platform=%s", sid, platform)
        _run_indexer(sid)
    except Exception as e:
        logger.warning("wiki-session-finalize hook failed: %s", e)


def register(ctx) -> None:
    ctx.register_hook("on_session_finalize", on_session_finalize)
    logger.info("wiki-session-finalize plugin registered (scripts=%s)", WIKI_SCRIPTS)
