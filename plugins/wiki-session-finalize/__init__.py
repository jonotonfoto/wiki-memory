"""wiki-session-finalize — immediate re-index of a just-closed session.

Hooks ``on_session_finalize`` (fires on /new, /reset, session expiry). When a
session_id is provided, it launches the indexer for that one session in the
background (subprocess), so a closed long session enters the wiki immediately
instead of waiting for the cron sweep. Fail-open: errors are logged, never
crash the agent.

Cross-platform: resolves paths via ``wiki_v2.config``. Works on Windows desktop,
Linux server, and inside a container.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from wiki_v2 import config
    config.load_env_file()
    config.apply()
    WIKI_SCRIPTS = str(config.SCRIPTS_DIR)
    PYTHON = config.PYTHON
except Exception as e:  # pragma: no cover
    logger.warning("wiki-session-finalize: config init failed: %s", e)
    WIKI_SCRIPTS = os.environ.get("WIKI_SCRIPTS", "")
    PYTHON = os.environ.get("WIKI_PYTHON", sys.executable)


def _run_indexer(session_id: str) -> None:
    try:
        cmd = [PYTHON, "-m", "wiki_v2.indexer", "--session", session_id]
        subprocess.Popen(
            cmd,
            cwd=WIKI_SCRIPTS,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        logger.info("wiki-session-finalize: launched indexer for session=%s", session_id)
    except Exception as e:
        logger.warning("wiki-session-finalize: failed to launch indexer: %s", e)


def on_session_finalize(*, session_id: Any = None, platform: Any = None, **_: Any) -> None:
    try:
        sid = str(session_id or "").strip()
        if not sid:
            logger.debug("wiki-session-finalize: no session_id, skip")
            return
        logger.info("wiki-session-finalize: session_id=%s platform=%s", sid, platform)
        _run_indexer(sid)
    except Exception as e:
        logger.warning("wiki-session-finalize hook failed: %s", e)


def register(ctx) -> None:
    ctx.register_hook("on_session_finalize", on_session_finalize)
    logger.info("wiki-session-finalize plugin registered (scripts=%s)", WIKI_SCRIPTS)
