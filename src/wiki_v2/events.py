"""Wiki Memory v3 — search event logging.

Writes structured JSONL to ``wiki_search_events.jsonl`` inside
``HERMES_HOME/wiki/``.  Append-only with O_APPEND + advisory lock.
MAX_LINES=5000 with rotation when exceeded.  log_event() is fail-open
(never raises).

Empty / very short queries (fewer than MIN_QUERY_LEN chars) are silently
skipped — they are noise, not signal.

Usage
-----
    from wiki_v2.events import log_event
    log_event("hermes agent config", hits=3, top_slug="hermes-config",
              top_score=0.82, context_chars=1200, duration_ms=45,
              source="semantic", session_id="20260814_123456")
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

# ── Logger from the shared wiki_v2 logger registry ────────────────────────
logger = logging.getLogger("wiki_v2")

# ── Resolve events file path (same pattern as metrics.py) ─────────────────
def _events_path() -> Path:
    """Return the wiki_search_events.jsonl path inside HERMES_HOME/wiki/."""
    try:
        from wiki_v2 import config  # type: ignore[import-not-found]
        home = config.HERMES_HOME
    except (ImportError, AttributeError):
        home = Path(__file__).resolve().parent.parent

    events_dir = home / "wiki"
    try:
        events_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        events_dir = Path(__file__).resolve().parent
        try:
            events_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return events_dir / "wiki_search_events.jsonl"


try:
    from wiki_v2 import config as _config  # единый порог (Этап 5.2)
    _MIN_QUERY_LEN = _config.get("WIKI_MIN_QUERY_LEN", 3)
except (ImportError, AttributeError):
    _MIN_QUERY_LEN = 3

# skip queries shorter than this (was 15 — too aggressive,
# most real short queries were dropped from the history)
MIN_QUERY_LEN = _MIN_QUERY_LEN
MAX_LINES = 5000    # rotation threshold for the JSONL file


def _rotate(path: Path) -> None:
    """Rotate wiki_search_events.jsonl to keep at most MAX_LINES."""
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= MAX_LINES:
            return
        # Keep the last MAX_LINES entries
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[-MAX_LINES:])
    except Exception:
        # If rotation fails (corrupted file?), just truncate — fail-open
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _append_line(path: Path, obj: dict) -> None:
    """Append a single JSON line to the events file with O_APPEND + lock."""
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            if hasattr(os, "lockf"):
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
            else:
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            line = json.dumps(obj, ensure_ascii=False) + "\n"
            os.write(fd, line.encode("utf-8"))
        finally:
            if hasattr(os, "lockf"):
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
            else:
                try:
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            os.close(fd)
    except Exception as exc:
        logger.debug("events._append_line failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────

def log_event(
    query: str,
    hits: int,
    top_slug: str = "",
    top_score: float = 0.0,
    context_chars: int = 0,
    duration_ms: float = 0.0,
    source: str = "unknown",
    session_id: str = "",
    gate_decision: str = "",
) -> None:
    """Log a single search event to wiki_search_events.jsonl.

    Parameters
    ----------
    query : str
        The search query text.  Empty or very short queries are skipped.
    hits : int
        Number of pages returned.
    top_slug : str
        Slug of the top-ranked page.
    top_score : float
        Score of the top-ranked page.
    context_chars : int
        Number of context characters returned.
    duration_ms : float
        Search duration in milliseconds.
    source : str
        Search source (e.g. 'semantic', 'keyword', 'bm25').
    session_id : str
        Hermes session ID.

    Never raises — wraps everything in try/except + logger.
    """
    try:
        # Skip empty / short queries
        if not query or len(query.strip()) < MIN_QUERY_LEN:
            return

        path = _events_path()
        _rotate(path)

        obj = {
            "ts": time.time(),
            "type": "search_event",
            "query": query.strip(),
            "hits": hits,
            "top_slug": top_slug,
            "top_score": top_score,
            "context_chars": context_chars,
            "duration_ms": duration_ms,
            "source": source,
            "session_id": session_id,
            "gate_decision": gate_decision,
        }
        _append_line(path, obj)
    except Exception as exc:
        logger.debug("events.log_event(%r) failed: %s", query, exc)
