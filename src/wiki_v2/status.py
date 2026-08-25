"""Wiki Memory v3 — health-check status() + CLI.

Returns a dict with the current state of the wiki index, API, and disk.
Never raises — wraps everything in try/except + logger.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from . import config
from .logging_setup import logger

# ── Thresholds ──────────────────────────────────────────────────────────────

API_ERROR_NORMAL = 0
API_ERROR_DEGRADED = 2  # 1..2 → degraded, >=3 → offline
DISK_WARNING_PCT = 0.10  # <10% free → warning


def _db_path() -> Path:
    """Return the wiki index DB path (`.index_v2.db`), NOT state.db.

    The dashboard/status must report on the WIKI index (pages, indexed
    sessions, embeddings), not on Hermes' session DB (state.db), which has
    no `pages` table and would always show 0.
    """
    return config.WIKI_PATH / ".index_v2.db"


def _read_db(fn):
    """Open the state DB and call fn(conn) → result.

    If the DB file does not exist or any error occurs, returns None.
    """
    try:
        if not _db_path().exists():
            return None
        conn = sqlite3.connect(str(_db_path()))
        conn.row_factory = sqlite3.Row
        result = fn(conn)
        conn.close()
        return result
    except Exception as exc:
        logger.debug("status._read_db failed: %s", exc)
        return None


def _count_pages(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]


def _count_sessions(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]


def _max_indexed_at(conn) -> float | None:
    r = conn.execute("SELECT MAX(indexed_at) FROM sessions").fetchone()[0]
    return float(r) if r is not None else None


def _count_orphans(conn) -> int:
    """Pages without any embedding (LEFT JOIN)."""
    r = conn.execute(
        "SELECT COUNT(*) FROM pages p "
        "LEFT JOIN embeddings e ON p.slug = e.slug "
        "WHERE e.slug IS NULL"
    ).fetchone()[0]
    return r


def _count_chunks(conn) -> int:
    """Embedding rows that are chunks (kind 'chunk:N' or 'page_chunk:N')."""
    return conn.execute(
        "SELECT COUNT(*) FROM embeddings "
        "WHERE kind LIKE 'chunk:%' OR kind LIKE 'page_chunk:%'"
    ).fetchone()[0]


def _count_vectors(conn) -> int:
    """Total embedding vectors in the index."""
    return conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]


def _api_errors_24h() -> tuple[int, bool]:
    """Read embed_api_errors_total from the metrics JSONL file (24h window).

    The dashboard runs as a SEPARATE process, so metrics.snapshot() (in-memory
    counters of THIS process) is always empty there → api_state stayed
    "unknown". Windowed sums live in dashboard_data.read_metrics_window
    (single ts-filtering implementation shared with the health section).

    has_metrics is False when no embed metric line was EVER written
    (metrics module never ran for embeds).
    """
    try:
        from .dashboard_data import read_metric_names, read_metrics_window

        names = read_metric_names()
        saw_embed = bool(names & {"embed_api_errors_total", "embed_api_calls_total"})
        if not saw_embed:
            return (0, False)
        err_count = int(read_metrics_window(hours=24).get("embed_api_errors_total", 0))
        return (err_count, True)
    except Exception as exc:
        logger.debug("status._api_errors_24h failed: %s", exc)
        return (0, False)


def _api_state(errors: int, has_metrics: bool) -> str:
    """Map error count + metrics presence to api_state string."""
    if not has_metrics:
        return "unknown"
    if errors == 0:
        return "normal"
    if errors <= API_ERROR_DEGRADED:
        return "degraded"
    return "offline"


def _disk_info() -> tuple[float, float, bool]:
    """(db_size_mb, disk_free_gb, disk_warning)."""
    db_path = _db_path()
    db_size_mb = 0.0
    if db_path.exists():
        db_size_mb = round(db_path.stat().st_size / (1024 * 1024), 2)

    usage = shutil.disk_usage(str(db_path))
    free_gb = round(usage.free / (1024 ** 3), 2)
    total_gb = round(usage.total / (1024 ** 3), 2)
    warning = total_gb > 0 and (free_gb / total_gb) < DISK_WARNING_PCT

    return db_size_mb, free_gb, warning


def status() -> dict:
    """Return a health-check dict.

    Never raises.  If the DB does not exist, returns a dict with
    ``error: True`` and zeroed counts.
    """
    try:
        errors_24h, has_metrics = _api_errors_24h()
        api_state = _api_state(errors_24h, has_metrics)

        db_path = _db_path()
        if not db_path.exists():
            return {
                "error": True,
                "api_state": api_state,
                "api_errors_24h": errors_24h,
                "last_indexed_at": None,
                "pages": 0,
                "sessions": 0,
                "orphans": 0,
                "chunks": 0,
                "vectors": 0,
                "db_size_mb": 0.0,
                "disk_free_gb": 0.0,
                "disk_warning": False,
            }

        db_size_mb, disk_free_gb, disk_warning = _disk_info()

        def _db_fn(conn):
            return (
                _count_pages(conn),
                _count_sessions(conn),
                _max_indexed_at(conn),
                _count_orphans(conn),
                _count_chunks(conn),
                _count_vectors(conn),
            )

        result = _read_db(_db_fn)
        if result is None:
            return {
                "error": True,
                "api_state": api_state,
                "api_errors_24h": errors_24h,
                "last_indexed_at": None,
                "pages": 0,
                "sessions": 0,
                "orphans": 0,
                "chunks": 0,
                "vectors": 0,
                "db_size_mb": db_size_mb,
                "disk_free_gb": disk_free_gb,
                "disk_warning": disk_warning,
            }

        pages, sessions, last_indexed_at, orphans, chunks, vectors = result

        return {
            "api_state": api_state,
            "api_errors_24h": errors_24h,
            "last_indexed_at": last_indexed_at,
            "pages": pages,
            "sessions": sessions,
            "orphans": orphans,
            "chunks": chunks,
            "vectors": vectors,
            "db_size_mb": db_size_mb,
            "disk_free_gb": disk_free_gb,
            "disk_warning": disk_warning,
        }

    except Exception as exc:
        logger.error("status() unexpected error: %s", exc)
        return {
            "error": True,
            "api_state": "unknown",
            "api_errors_24h": 0,
            "last_indexed_at": None,
            "pages": 0,
            "sessions": 0,
            "orphans": 0,
            "db_size_mb": 0.0,
            "disk_free_gb": 0.0,
            "disk_warning": False,
        }


# ── CLI ─────────────────────────────────────────────────────────────────────

def _cli():
    """Print status lines to stdout."""
    s = status()
    for key, val in s.items():
        print(f"{key}: {val}")


if __name__ == "__main__":
    _cli()
