"""Wiki Memory v3 — dashboard data readers for /api/status.

Reads sources from JSONL files (NOT metrics.snapshot()) so the server
process can report real metrics even when the indexer process is separate.

Public API
----------
read_metrics_file() -> dict
read_events() -> list[dict]
read_ts(metric_name, start_ts, end_ts, bucket) -> list[dict]
read_oversized() -> list[dict]
read_log_errors(hours=1) -> list[str]
_build_api_status() -> dict
lmstudio_status(timeout=1.5) -> dict
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

from . import config
from .dashboard_ts import init_db, ingest_jsonl, query_ts, summary_buckets
from .effectiveness import coverage, hit_rate
from .logging_setup import logger
from .status import status

# ── TTL cache for metrics ────────────────────────────────────────────────────

_cache: dict = {}
_lock = threading.Lock()
_TTL = 10.0

# ── Time-series (dashboard_metrics.db) ingestion guard ─────────────────────
# dashboard_ts.ingest_jsonl()/summary_buckets() are NOT called anywhere in the
# working code (only tests) — that's why the trend charts were empty/stale.
# We now call them on each /api/status, rate-limited to avoid re-parsing the
# whole JSONL every request.
_ts_last_ingest: float = 0.0
_TS_INGEST_INTERVAL = 20.0  # seconds between ingest+summary runs


def _ensure_ts_ingested() -> None:
    """Ingest new JSONL lines into dashboard_metrics.db (rate-limited).

    Reads wiki_metrics.jsonl + wiki_search_events.jsonl into ts_metrics and
    rebuilds the aggregate buckets, then /api/status reads fresh charts.
    fail-open: never raises.
    """
    global _ts_last_ingest
    now = time.time()
    if now - _ts_last_ingest < _TS_INGEST_INTERVAL:
        return
    _ts_last_ingest = now
    try:
        # Ensure time-series tables exist before writing (fail-safe: if the
        # metrics DB is freshly created / empty, ingest would otherwise fail
        # silently on "no such table" and the trend charts stay empty).
        init_db()
        ingest_jsonl(
            config.WIKI_PATH / "wiki_metrics.jsonl",
            config.WIKI_PATH / "wiki_search_events.jsonl",
        )
        summary_buckets()
    except Exception as exc:
        logger.debug("dashboard_data._ensure_ts_ingested failed: %s", exc)


def _count_new_sessions_7d() -> int:
    """Sessions indexed in the last 7 days (real DB count).

    Previously hard-coded to 0 → dashboard always showed "Новые за неделю: 0".
    Reads sessions.indexed_at from the wiki index DB (.index_v2.db).
    fail-open → 0.
    """
    try:
        import datetime as _dt
        import sqlite3 as _sqlite
        db_path = config.WIKI_PATH / ".index_v2.db"
        if not db_path.exists():
            return 0
        seven_days_ago = (
            _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=7)
        ).timestamp()
        conn = _sqlite.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE indexed_at > ?",
                (seven_days_ago,),
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("dashboard_data._count_new_sessions_7d failed: %s", exc)
        return 0


def cached_metrics() -> dict:
    """Return metrics from file, cached for _TTL seconds.

    Uses a global _cache dict mutated in-place (never re-assigned).
    """
    with _lock:
        now = time.time()
        if _cache.get("_t", 0) + _TTL > now:
            return _cache["metrics"]
    m = read_metrics_file()
    with _lock:
        _cache["metrics"] = m
        _cache["_t"] = time.time()
    return m

# ── Helpers ──────────────────────────────────────────────────────────────────


def cache_hit_rate(metrics: dict) -> float:
    """Wiki query-cache hit rate from counters written by search.py
    (_cache_bump -> cache_hits_total / cache_misses_total).

    Single source of truth for dashboard UI and /api/status.
    hits+misses == 0 -> 0.0 (never NaN).
    """
    hits = metrics.get("cache_hits_total", 0)
    misses = metrics.get("cache_misses_total", 0)
    total = hits + misses
    return hits / total if total else 0.0


def _effectiveness_rating(hr: float, cov: float) -> str:
    """Return a short text rating based on hit_rate and coverage."""
    score = (hr * 0.6 + cov * 0.4) * 100
    if score >= 80:
        return "Отлично"
    if score >= 60:
        return "Хорошо"
    if score >= 40:
        return "Средне"
    if score > 0:
        return "Низкая"
    return "Нет данных"


# ── Data readers ─────────────────────────────────────────────────────────────


def read_metrics_file() -> dict:
    """Read wiki_metrics.jsonl. inc -> sum, record -> last value.

    Path: config.WIKI_PATH / "wiki_metrics.jsonl"
    Each JSONL line: {"type":"inc","name":X,"value":1} or
                     {"type":"record","name":X,"value":N}
    For inc: sum by name. For record: take the LAST value.
    fail-open: missing file -> {}; bad line -> skip.
    Returns {name: number}
    """
    path = config.WIKI_PATH / "wiki_metrics.jsonl"
    if not path.exists():
        return {}

    sums: dict[str, float] = {}
    records: dict[str, float] = {}

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

                obj_type = obj.get("type")
                name = obj.get("name")
                value = obj.get("value", 0)

                if obj_type == "inc" and name:
                    sums[name] = sums.get(name, 0) + float(value)
                elif obj_type == "record" and name:
                    records[name] = float(value)
    except OSError:
        return {}

    # Merge: inc sums first, then records overwrite same-name keys
    result = dict(sums)
    result.update(records)
    return result


def read_metrics_window(hours: int = 24) -> dict:
    """Sum of inc-counters within the last *hours* hours (by line ts).

    Unlike read_metrics_file() (all-history sums), this respects the
    per-line timestamp, so "errors 24h" really means the last 24 hours.
    record-lines are ignored (they are gauges, not counters).
    fail-open: missing file -> {}; bad line -> skip.
    Returns {name: number}
    """
    path = config.WIKI_PATH / "wiki_metrics.jsonl"
    if not path.exists():
        return {}

    cutoff = time.time() - hours * 3600
    sums: dict[str, float] = {}
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
                if obj.get("type") != "inc":
                    continue
                name = obj.get("name")
                ts = obj.get("ts", 0)
                if not name or not isinstance(ts, (int, float)):
                    continue
                if float(ts) >= cutoff:
                    sums[name] = sums.get(name, 0) + float(obj.get("value", 0) or 0)
    except OSError:
        return {}
    return sums


def read_metric_names() -> set:
    """Names of all counters ever written to wiki_metrics.jsonl (any age).

    One full pass, no windowing. Used to distinguish "metrics module never
    ran" from "ran but nothing in the window".
    fail-open: missing file -> set(); bad line -> skip.
    """
    path = config.WIKI_PATH / "wiki_metrics.jsonl"
    if not path.exists():
        return set()
    names: set = set()
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
                name = obj.get("name")
                if name:
                    names.add(name)
    except OSError:
        return set()
    return names


def read_events() -> list[dict]:
    """Read wiki_search_events.jsonl, return list of events.

    Path via effectiveness._events_path().
    Parses lines, filters obj.get("type") == "search_event".
    fail-open -> [].
    """
    try:
        from .effectiveness import _events_path
    except ImportError:
        return []

    path = _events_path()
    if not path.exists():
        return []

    events: list[dict] = []
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
                if obj.get("type") == "search_event":
                    events.append(obj)
    except OSError:
        return []
    return events


def read_ts(metric_name: str, start_ts: int, end_ts: int, bucket: str) -> list[dict]:
    """Read time series from dashboard_metrics.db via dashboard_ts.query_ts.

    from .dashboard_ts import query_ts
    return query_ts(metric_name, start_ts, end_ts, bucket)
    fail-open -> []
    """
    try:
        return query_ts(metric_name, start_ts, end_ts, bucket)
    except Exception:
        return []


_OVERSIZED_SESSION_RE = re.compile(r"session=(\S+)")


def _entry_session_id(obj) -> str | None:
    """Extract session_id from an oversized-log entry.

    New-style entries carry "session_id"; real log lines are pipe-format
    ("... | session=<id> | msgs=N | ...") and arrive as {"raw": line}.
    """
    if not isinstance(obj, dict):
        return None
    sid = obj.get("session_id")
    if sid:
        return str(sid)
    raw = obj.get("raw")
    if raw:
        m = _OVERSIZED_SESSION_RE.search(raw)
        if m:
            return m.group(1)
    return None


def _indexed_session_ids() -> set:
    """Session ids already handled by the indexer (row in .index_v2.db).

    A row exists after ANY outcome: page created OR deliberate skip-mark
    (page_slug="" + content_hash=""). Only never-selected sessions have no
    row. fail-open -> empty set.
    """
    try:
        import sqlite3 as _sqlite
        db_path = config.WIKI_PATH / ".index_v2.db"
        if not db_path.exists():
            return set()
        conn = _sqlite.connect(str(db_path))
        try:
            rows = conn.execute("SELECT session_id FROM sessions").fetchall()
            return {r[0] for r in rows}
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("dashboard_data._indexed_session_ids failed: %s", exc)
        return set()


def read_oversized() -> list[dict]:
    """Read oversized_sessions.log, keeping only sessions still pending.

    Path: config.WIKI_PATH / "oversized_sessions.log". If missing -> [].
    Each line is a deferred session record. Parse as JSON if possible,
    otherwise keep as string.

    Stale self-expiry (2026-08-25): an entry whose session already has a row
    in the index DB (page created or skip-mark) is dropped — the indexer has
    handled it, it is no longer "awaiting processing". Duplicate log lines
    (pre-dedup era) collapse to the first per session. Entries without a
    recognizable session_id are kept as-is. fail-open: if the index DB is
    unreadable, parsed entries are returned unfiltered.
    """
    path = config.WIKI_PATH / "oversized_sessions.log"
    if not path.exists():
        return []

    results: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    results.append(obj)
                except (json.JSONDecodeError, ValueError):
                    results.append({"raw": line})
    except OSError:
        return []

    handled = _indexed_session_ids()
    if not handled:
        return results

    pending: list[dict] = []
    seen: set = set()
    for obj in results:
        sid = _entry_session_id(obj)
        if sid is None:
            pending.append(obj)
            continue
        if sid in handled or sid in seen:
            continue
        seen.add(sid)
        pending.append(obj)
    return pending


def read_log_errors(hours: int = 1) -> list[str]:
    """Last WARNING/ERROR from wiki_v2.log for the last N hours.

    Path: config.WIKI_PATH / "logs" / "wiki_v2.log". If missing -> [].
    Read last ~200 lines, filter WARNING or ERROR.
    Return up to 20 most recent.
    """
    path = config.WIKI_PATH / "logs" / "wiki_v2.log"
    if not path.exists():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []

    # Take last ~200 lines
    recent = lines[-200:] if len(lines) > 200 else lines

    errors: list[str] = []
    for line in recent:
        if "WARNING" in line or "ERROR" in line:
            errors.append(line.rstrip("\n").rstrip("\r"))

    # Return up to 20 most recent (they are in chronological order)
    return errors[-20:]


# ── API status builder ───────────────────────────────────────────────────────


def _build_api_status() -> dict:
    """Assemble full status for /api/status per requirements F2.7.

    Reads from files (JSONL), not from in-memory metrics.snapshot().
    fail-open: any error -> {"error": str(exc)} (never raises).
    """
    try:
        # Keep the dashboard_metrics.db time-series fresh by ingesting any
        # new JSONL lines (metrics + events) before reading. Rate-limited so
        # we don't re-parse the whole file every request.
        _ensure_ts_ingested()

        s = status()
        hr = hit_rate()
        cov = coverage()

        metrics = cached_metrics()
        events = read_events()

        # Cache hit rate (single shared implementation)
        cache_rate = cache_hit_rate(metrics)

        # Recent queries: last 10 events (by order of appearance, reversed)
        recent_queries = []
        for ev in reversed(events[-10:]):
            recent_queries.append({
                "query": ev.get("query", ""),
                "hits": ev.get("hits", 0),
                "duration_ms": ev.get("duration_ms", 0),
                "source": ev.get("source", "unknown"),
                "ts": ev.get("ts", 0),
            })

        # LM Studio status
        lm = lmstudio_status()

        return {
            "generated_at": int(time.time()),
            "health": {
                "api_state": s.get("api_state", "unknown"),
                "last_indexed_at": s.get("last_indexed_at"),
                "db_size_mb": s.get("db_size_mb", 0),
                "disk_warning": s.get("disk_warning", False),
                "api_errors_24h": s.get("api_errors_24h", 0),
            },
            "effectiveness": {
                "hit_rate": hr,
                "coverage": cov,
                "rating": _effectiveness_rating(hr, cov),
            },
            "database": {
                "pages": s.get("pages", 0),
                "sessions": s.get("sessions", 0),
                "orphans": s.get("orphans", 0),
                "chunks": s.get("chunks", 0),
                "vectors": s.get("vectors", 0),
                "facts": s.get("facts", 0),
                "new_sessions_7d": _count_new_sessions_7d(),
            },
            "api": {
                "embed_calls": metrics.get("embed_api_calls_total", 0),
                "embed_errors": metrics.get("embed_api_errors_total", 0),
                "chat_calls": metrics.get("chat_api_calls_total", 0),
                "chat_errors": metrics.get("chat_api_errors_total", 0),
                "cache_hit_rate": cache_rate,
                "search_fallback": metrics.get("search_fallback_total", 0),
            },
            "search": {
                "recent_queries": recent_queries,
                "total_events": len(events),
            },
            "problems": _problems(),
            "indexing": _indexing(),
            "errors_recent": read_log_errors(),
            "lmstudio": lm,
        }

    except Exception as exc:
        logger.error("_build_api_status failed: %s", exc)
        return {"error": str(exc)}


def lmstudio_status(timeout: float = 1.5) -> dict:
    """Check LM Studio availability.

    GET http://127.0.0.1:1234/v1/models with timeout.
    return {"reachable": bool, "models": [m["id"] for m in data.get("data", [])]}
    fail-open -> {"reachable": False, "models": []}
    """
    try:
        req = urllib.request.Request("http://127.0.0.1:1234/v1/models")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["id"] for m in data.get("data", [])]
            return {"reachable": True, "models": models}
    except Exception:
        return {"reachable": False, "models": []}


# ── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        st = _build_api_status()
        print(json.dumps(st, ensure_ascii=False, indent=2, default=str))
    else:
        print("Usage: python -m wiki_v2.dashboard_data status")


# ── Lazy wrappers (avoid circular import: dashboard→dashboard_data→dashboard_analysis→dashboard) ──

def _problems():
    from .dashboard_analysis import problems
    return problems()


def _indexing():
    from .dashboard_analysis import indexing
    return indexing()
