"""Wiki Memory v3 — lightweight metrics (inc / record / snapshot).

Writes structured JSONL to ``wiki_metrics.jsonl`` inside HERMES_HOME/wiki/.
MAX_LINES=5000 with rotation when exceeded.  inc() is fail-open (never raises).
Append-only with O_APPEND + fcntl lock for cross-platform safety.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

# ── Logger from the shared wiki_v2 logger registry ────────────────────────
logger = logging.getLogger("wiki_v2")

# ── Resolve metrics file path (same pattern as logging_setup._log_file_path) ──
def _metrics_path() -> Path:
    """Return the wiki_metrics.jsonl path inside HERMES_HOME/wiki/."""
    try:
        from wiki_v2 import config  # type: ignore[import-not-found]
        home = config.HERMES_HOME
    except (ImportError, AttributeError):
        home = Path(__file__).resolve().parent.parent

    metrics_dir = home / "wiki"
    try:
        metrics_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        # Fall back to local dir next to this module
        metrics_dir = Path(__file__).resolve().parent
        try:
            metrics_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass  # fail-open
    return metrics_dir / "wiki_metrics.jsonl"


# ── In-memory counters (mutable dict protected by lock) ───────────────────
_counters: dict[str, float] = {}
_lock = threading.Lock()

MAX_LINES = 5000  # rotation threshold for the JSONL file


def _rotate(path: Path) -> None:
    """Rotate wiki_metrics.jsonl to keep at most MAX_LINES."""
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
    """Append a single JSON line to the metrics file with O_APPEND + lock."""
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            # Cross-platform advisory lock (fcntl on POSIX, locking module on Windows)
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
        logger.debug("metrics._append_line failed: %s", exc)


# ── Public API ────────────────────────────────────────────────────────────

def inc(name: str, tags: dict = None) -> None:
    """Increment a counter by 1 and write one JSONL line.

    Never raises — wraps everything in try/except + logger.
    """
    global _counters
    try:
        with _lock:
            _counters[name] = _counters.get(name, 0) + 1

        path = _metrics_path()
        # Rotate before writing if needed
        _rotate(path)

        obj = {
            "ts": time.time(),
            "type": "inc",
            "name": name,
            "value": 1,
        }
        if tags:
            obj["tags"] = tags
        _append_line(path, obj)
    except Exception as exc:
        logger.debug("metrics.inc(%r) failed: %s", name, exc)


def record(name: str, value: float, tags: dict = None) -> None:
    """Record a numeric metric value and write one JSONL line."""
    try:
        with _lock:
            _counters[name] = value

        path = _metrics_path()
        _rotate(path)

        obj = {
            "ts": time.time(),
            "type": "record",
            "name": name,
            "value": value,
        }
        if tags:
            obj["tags"] = tags
        _append_line(path, obj)
    except Exception as exc:
        logger.debug("metrics.record(%r, %s) failed: %s", name, value, exc)


def snapshot() -> dict:
    """Return a copy of the current in-memory counters as a plain dict."""
    with _lock:
        return dict(_counters)
