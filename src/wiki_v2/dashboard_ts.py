"""Wiki Memory v3 — dashboard time series (SQLite from jsonl)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("wiki_v2")


def _db_path() -> Path:
    """Return the dashboard_metrics.db path inside WIKI_PATH."""
    try:
        # Import here to avoid circular imports
        from wiki_v2 import config  # type: ignore[import-not-found]
        return config.WIKI_PATH / "dashboard_metrics.db"
    except (ImportError, AttributeError):
        # Fallback for testing
        return Path(__file__).resolve().parent.parent / "dashboard_metrics.db"


def init_db() -> None:
    """Initialize SQLite database for time series metrics."""
    try:
        db_path = _db_path()
        # ⚠️ Создать родительскую директорию (WIKI_PATH может не существовать в тестах/чистой системе)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            
            # Raw metrics table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_metrics (
                    metric_name TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    value REAL,
                    tags TEXT,
                    UNIQUE(metric_name, ts)
                )
            """)
            
            # Bucket tables for pre-aggregated data
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_metrics_1min (
                    metric_name TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    value REAL,
                    UNIQUE(metric_name, ts)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_metrics_1hour (
                    metric_name TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    value REAL,
                    UNIQUE(metric_name, ts)
                )
            """)
            
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ts_metrics_1day (
                    metric_name TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    value REAL,
                    UNIQUE(metric_name, ts)
                )
            """)
            
            conn.commit()
    except Exception as exc:
        logger.debug("dashboard_ts.init_db failed: %s", exc)
        # fail-open: continue without DB


def _parse_jsonl_line(line: str) -> dict[str, Any] | None:
    """Parse a single JSONL line, return None on failure (fail-open)."""
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _determine_metric_from_record(obj: dict[str, Any]) -> tuple[str, float, dict[str, Any]] | None:
    """Extract metric name, value, and tags from a record object."""
    obj_type = obj.get("type")
    if obj_type == "inc":
        name = obj.get("name")
        if not name:
            return None
        value = float(obj.get("value", 1))
        tags = obj.get("tags", {})
        return name, value, tags
    elif obj_type == "record":
        name = obj.get("name")
        if not name:
            return None
        value = float(obj.get("value", 0))
        tags = obj.get("tags", {})
        return name, value, tags
    elif obj_type == "search_event":
        # Create metrics from search events
        name = f"search.{obj.get('source', 'unknown')}.hits"
        value = float(obj.get("hits", 0))
        tags = {
            "query": obj.get("query", ""),
            "duration_ms": obj.get("duration_ms", 0),
            "top_score": obj.get("top_score", 0.0),
        }
        return name, value, tags
    return None


def ingest_jsonl(metrics_path: Path, events_path: Path) -> int:
    """Ingest JSONL files into SQLite database.
    
    Returns number of lines processed.
    """
    processed = 0
    batch_size = 500
    batch: list[tuple] = []
    
    def flush_batch() -> int:
            nonlocal batch
            if not batch:
                return 0
            try:
                with sqlite3.connect(_db_path()) as conn:
                    conn.executemany(
                        """
                        INSERT OR REPLACE INTO ts_metrics (metric_name, ts, value, tags)
                        VALUES (?, ?, ?, ?)
                        """,
                        batch
                    )
                    conn.commit()
                    count = len(batch)
                    batch = []
                    return count
            except Exception as exc:
                logger.debug("dashboard_ts.ingest_jsonl batch failed: %s", exc)
                batch = []  # Clear batch on error to avoid poison pill
                return 0
    
    # Process metrics file
    if metrics_path.exists():
        try:
            with open(metrics_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = _parse_jsonl_line(line)
                    if obj is None:
                        continue
                    metric_info = _determine_metric_from_record(obj)
                    if metric_info is None:
                        continue
                    name, value, tags = metric_info
                    ts = int(obj.get("ts", time.time()))
                    batch.append((name, ts, value, json.dumps(tags, ensure_ascii=False)))
                    processed += 1
                    if len(batch) >= batch_size:
                        flush_batch()
        except Exception as exc:
            logger.debug("dashboard_ts.ingest_jsonl metrics file failed: %s", exc)
    
    # Process events file
    if events_path.exists():
        try:
            with open(events_path, "r", encoding="utf-8") as f:
                for line in f:
                    obj = _parse_jsonl_line(line)
                    if obj is None:
                        continue
                    metric_info = _determine_metric_from_record(obj)
                    if metric_info is None:
                        continue
                    name, value, tags = metric_info
                    ts = int(obj.get("ts", time.time()))
                    batch.append((name, ts, value, json.dumps(tags, ensure_ascii=False)))
                    processed += 1
                    if len(batch) >= batch_size:
                        flush_batch()
        except Exception as exc:
            logger.debug("dashboard_ts.ingest_jsonl events file failed: %s", exc)
    
    # Flush remaining batch
    flush_batch()
    return processed


def summary_buckets() -> None:
    """Pre-calculate 1min/1hour/1day buckets from raw data."""
    try:
        with sqlite3.connect(_db_path()) as conn:
            # 1-minute buckets (60 seconds)
            conn.execute("""
                INSERT OR REPLACE INTO ts_metrics_1min (metric_name, ts, value)
                SELECT 
                    metric_name,
                    (ts / 60) * 60 as ts,
                    AVG(value) as value
                FROM ts_metrics
                GROUP BY metric_name, (ts / 60) * 60
            """)
            
            # 1-hour buckets (3600 seconds)
            conn.execute("""
                INSERT OR REPLACE INTO ts_metrics_1hour (metric_name, ts, value)
                SELECT 
                    metric_name,
                    (ts / 3600) * 3600 as ts,
                    AVG(value) as value
                FROM ts_metrics
                GROUP BY metric_name, (ts / 3600) * 3600
            """)
            
            # 1-day buckets (86400 seconds)
            conn.execute("""
                INSERT OR REPLACE INTO ts_metrics_1day (metric_name, ts, value)
                SELECT 
                    metric_name,
                    (ts / 86400) * 86400 as ts,
                    AVG(value) as value
                FROM ts_metrics
                GROUP BY metric_name, (ts / 86400) * 86400
            """)
            
            conn.commit()
    except Exception as exc:
        logger.debug("dashboard_ts.summary_buckets failed: %s", exc)


def series_count(
    metric_names: list[str],
    start_ts: int,
    end_ts: int,
    bucket: str,
) -> list[dict[str, Any]]:
    """Count of metric events per time-bucket for the given metric names.

    Unlike query_ts (which averages values), this counts how many raw lines
    fell into each bucket — ideal for activity charts (calls/errors per hour).
    Returns [{ts, count}] chronologically, only buckets that have events.
    """
    bucket_secs = {"minute": 60, "hour": 3600, "day": 86400}.get(bucket)
    if bucket_secs is None:
        logger.warning("dashboard_ts.series_count invalid bucket: %s", bucket)
        return []
    if not metric_names:
        return []
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            placeholders = ",".join("?" * len(metric_names))
            cur = conn.execute(
                f"SELECT (ts/{bucket_secs})*{bucket_secs} AS bts, COUNT(*) AS cnt "
                f"FROM ts_metrics WHERE metric_name IN ({placeholders}) "
                "AND ts BETWEEN ? AND ? GROUP BY bts ORDER BY bts",
                (*metric_names, int(start_ts), int(end_ts)),
            )
            return [{"ts": r["bts"], "count": r["cnt"]} for r in cur.fetchall()]
    except Exception as exc:
        logger.debug("dashboard_ts.series_count failed: %s", exc)
        return []


def query_ts(metric_name: str, start_ts: int, end_ts: int, bucket: str) -> list[dict[str, Any]]:
    """Query time series data for a metric name and time range.
    
    Args:
        metric_name: Name of the metric to query
        start_ts: Start timestamp (inclusive)
        end_ts: End timestamp (inclusive)
        bucket: Bucket size - "minute", "hour", or "day"
        
    Returns:
        List of dictionaries with "ts" and "value" keys
    """
    if bucket not in ("minute", "hour", "day"):
        logger.warning("dashboard_ts.query_ts invalid bucket: %s", bucket)
        return []
    
    table_map = {
        "minute": "ts_metrics_1min",
        "hour": "ts_metrics_1hour", 
        "day": "ts_metrics_1day"
    }
    
    table_name = table_map[bucket]
    
    try:
        with sqlite3.connect(_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                f"""
                SELECT ts, value FROM {table_name}
                WHERE metric_name = ? AND ts BETWEEN ? AND ?
                ORDER BY ts
                """,
                (metric_name, start_ts, end_ts)
            )
            results = []
            for row in cursor.fetchall():
                results.append({
                    "ts": row["ts"],
                    "value": row["value"]
                })
            return results
    except Exception as exc:
        logger.debug("dashboard_ts.query_ts failed: %s", exc)
        return []