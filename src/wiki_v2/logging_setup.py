"""Wiki Memory v3 — structured logging setup.

Provides a single, idempotent ``setup_logging()`` that installs:
  - TimedRotatingFileHandler (hourly rotation, keeps 24 backups) → log file in HERMES_HOME/wiki/logs/
  - Console handler → stdout

All modules should import the shared logger::

    from wiki_v2.logging_setup import logger
    logger.info("…")

The ``config.logger`` is kept as an alias to the same object so existing
imports via ``from wiki_v2.config import logger`` continue to work.

"""


from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# Ротация по ВРЕМЕНИ (по часам) — чтобы лог не раздувался и всегда был
# доступен «последний час» отдельным файлом. Храним 24 файла (сутки).
LOG_WHEN = "H"        # интервал: час
LOG_INTERVAL = 1      # каждые 1 час
LOG_BACKUP_COUNT = 24 # хранить 24 часа

# ── Shared logger (the single source of truth) ───────────────────────────

logger = logging.getLogger("wiki_v2")
logger.setLevel(logging.DEBUG)  # let handlers decide the level


def _log_file_path() -> Path:
    """Return the directory and log file path.

    Uses ``config.HERMES_HOME`` when available, falls back to a local
    ``logs/`` directory next to this module.
    """
    try:
        from wiki_v2 import config  # type: ignore[import-not-found]
        home = config.HERMES_HOME
    except (ImportError, AttributeError):
        home = Path(__file__).resolve().parent.parent

    log_dir = home / "wiki" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # If we truly can't create the directory (e.g. permission denied),
        # fall back to a local logs/ dir next to this module.
        log_dir = Path(__file__).resolve().parent / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # fail-open; file handler will be skipped entirely
    return log_dir / "wiki_v2.log"


# Formatter used by both handlers.
_LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"

_console_handler: logging.Handler | None = None
_file_handler: TimedRotatingFileHandler | None = None


def setup_logging(
    log_file: str | Path | None = None,
    level: int = logging.INFO,
) -> None:
    """Configure the wiki_v2 logger (idempotent).

    Parameters
    ----------
    log_file : str | Path | None
        Explicit path to the log file.  When *None* the default location
        inside ``HERMES_HOME/wiki/logs/`` is used (see :func:`_log_file_path`).
    level : int
        Minimum logging level for both handlers (default: INFO).

    Safety
    ------
    - Idempotent: calling this multiple times does NOT duplicate handlers.
    - Fail-open: if the file handler cannot be created, only the console
      handler is installed — the function never raises.
    """
    global _console_handler, _file_handler

    # Strip any NullHandler left by config.py (or other modules) so only
    # real handlers remain.
    logger.handlers = [h for h in logger.handlers if not isinstance(h, logging.NullHandler)]

    # ── Console handler (stdout) ───────────────────────────────────────
    if _console_handler is None:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT))
        logger.addHandler(ch)
        _console_handler = ch

    # ── File handler (rotating) ────────────────────────────────────────
    if _file_handler is not None:
        return  # already installed — idempotent guard

    try:
        log_path = Path(log_file) if log_file else _log_file_path()
    except Exception:
        # If path resolution itself fails, skip file handler.
        return

    # Ensure the parent directory exists for an explicit log_file too
    # (the default path is already handled by _log_file_path).
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        # If we can't create the directory, skip the file handler
        # (fail-open: log to console only).
        return

    try:
        fh = TimedRotatingFileHandler(
            filename=str(log_path),
            when=LOG_WHEN,
            interval=LOG_INTERVAL,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FMT))
        logger.addHandler(fh)
        _file_handler = fh
    except OSError:
        # Fail-open: log to console only.
        pass


def reset_logging() -> None:
    """Remove all handlers (useful for tests or reconfiguration).

    After calling this, call :func:`setup_logging` again to restore.
    """
    global _console_handler, _file_handler
    logger.handlers.clear()
    _console_handler = None
    _file_handler = None


# ── End ──────────────────────────────────────────────────────────────────
