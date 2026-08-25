"""Wiki Memory v3 — effectiveness metrics.

Three read-only functions that summarise search quality from the
events log and the index database.

All functions are fail-open: they return a sensible default on any
error rather than raising.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("wiki_v2")


def _events_path() -> Path:
    """Return the wiki_search_events.jsonl path."""
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


def _index_db_path() -> Path:
    """Return the index database path."""
    try:
        from wiki_v2 import config  # type: ignore[import-not-found]
        wiki = config.WIKI_PATH
    except (ImportError, AttributeError):
        wiki = Path(__file__).resolve().parent.parent / "wiki"

    return wiki / ".index_v2.db"


# ── Public API ────────────────────────────────────────────────────────────

def hit_rate() -> float:
    """Fraction of search events that returned at least one hit.

    Reads ``wiki_search_events.jsonl`` and computes
    ``sum(hits>0) / total_events``.  Returns 0.0 when the file is
    empty or missing.
    """
    path = _events_path()
    if not path.exists():
        return 0.0

    total = 0
    with_hits = 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                total += 1
                if obj.get("hits", 0) > 0:
                    with_hits += 1
    except OSError:
        return 0.0

    if total == 0:
        return 0.0
    return with_hits / total


def coverage() -> float:
    """Fraction of indexed sessions that have a non-empty content_hash.

    Reads the ``sessions`` table from the index database.
    Returns 0.0 when the DB is missing or empty.
    """
    db_path = _index_db_path()
    if not db_path.exists():
        return 0.0

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT content_hash FROM sessions"
        ).fetchall()
        conn.close()
    except Exception:
        return 0.0

    if not rows:
        return 0.0

    total = len(rows)
    with_hash = sum(1 for r in rows if (r["content_hash"] or "").strip())

    return with_hash / total


def usage(keyword: str, context: str, answer: str) -> float:
    """Check whether *keyword* appears in both *context* and *answer*.

    Returns 1.0 when the keyword is found in both strings, 0.0
    otherwise.  Returns 0.0 when either context or answer is empty.
    """
    if not context or not answer:
        return 0.0

    kw = keyword.lower()
    return 1.0 if (kw in context.lower() and kw in answer.lower()) else 0.0
