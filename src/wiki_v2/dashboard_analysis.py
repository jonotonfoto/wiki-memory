"""Wiki Memory v3 — dashboard analysis functions.

Provides effectiveness, trends, indexing, cache_stats, and problems()
for the dashboard UI.  All functions are fail-open: they never raise.
"""
from __future__ import annotations

import datetime
import json
import logging
import sqlite3
import time

from . import config
from .dashboard_charts import svg_multi, svg_timeseries
from .dashboard_data import (
    _effectiveness_rating,
    read_events,
    read_oversized,
)
from .dashboard_render import range_seconds, range_to_bucket
from .dashboard_ts import query_ts, series_count
from .effectiveness import coverage, hit_rate
from .index_db import IndexDB
from .indexer import get_unindexed_sessions
from .quality import is_junk_chunk
from .status import status

logger = logging.getLogger("wiki_v2")


def _daily_hit_rate(events: list[dict]) -> list[dict]:
    """Return per-day hit_rate for the last 14 days.

    Each entry: {"date": "YYYY-MM-DD", "hit_rate": float, "total": int}
    """
    today = datetime.date.today()
    buckets: dict[str, dict] = {}
    for d in range(14):
        dt = today - datetime.timedelta(days=d)
        key = dt.isoformat()
        buckets[key] = {"total": 0, "with_hits": 0}

    for ev in events:
        ts = ev.get("ts")
        if not isinstance(ts, (int, float)):
            continue
        try:
            day = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone.utc
            ).date()
        except (ValueError, OSError, OverflowError):
            continue
        if day.isoformat() in buckets:
            buckets[day.isoformat()]["total"] += 1
            if ev.get("hits", 0) > 0:
                buckets[day.isoformat()]["with_hits"] += 1

    result = []
    for key in sorted(buckets):
        b = buckets[key]
        hr = b["with_hits"] / b["total"] if b["total"] else 0.0
        result.append({"date": key, "hit_rate": round(hr, 4), "total": b["total"]})
    return result


def _daily_latency(events: list[dict]) -> list[dict]:
    """Return per-day average latency (ms) for the last 14 days.

    Each entry: {"date": "YYYY-MM-DD", "avg_latency": float}
    """
    today = datetime.date.today()
    buckets: dict[str, list[float]] = {}
    for d in range(14):
        dt = today - datetime.timedelta(days=d)
        buckets[dt.isoformat()] = []

    for ev in events:
        ts = ev.get("ts")
        dur = ev.get("duration_ms")
        if not isinstance(ts, (int, float)) or not isinstance(dur, (int, float)):
            continue
        if not (0.0 <= float(dur) <= 600000.0):
            continue
        try:
            day = datetime.datetime.fromtimestamp(
                ts, tz=datetime.timezone.utc
            ).date()
        except (ValueError, OSError, OverflowError):
            continue
        if day.isoformat() in buckets:
            buckets[day.isoformat()].append(float(dur))

    result = []
    for key in sorted(buckets):
        vals = buckets[key]
        avg = sum(vals) / len(vals) if vals else 0.0
        result.append({"date": key, "avg_latency": round(avg, 2)})
    return result


def read_injects() -> list[dict]:
    """Read wiki_injects.jsonl (fail-open -> [])."""
    try:
        path = config.WIKI_PATH / "wiki_injects.jsonl"
        if not path.exists():
            return []
        rows = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ts = obj.get("ts")
                    if isinstance(ts, (int, float)):
                        rows.append(obj)
                except (json.JSONDecodeError, ValueError):
                    continue
        return rows
    except Exception as exc:
        logger.debug("read_injects failed: %s", exc)
        return []


def _inject_relevance_series(events: list[dict], inject_ts: list[float], tol_s: float = 3.0) -> list[dict]:
    """Match search events with inject timestamps using two-pointer."""
    try:
        evs = sorted([e for e in events if isinstance(e.get("ts"), (int, float))], key=lambda x: x["ts"])
        injs = sorted([float(x) if not isinstance(x, dict) else float(x.get("ts", 0)) for x in inject_ts if (isinstance(x, (int, float)) or (isinstance(x, dict) and isinstance(x.get("ts"), (int, float))))])
        
        res = []
        j = 0
        
        for ev in evs:
            ev_ts = float(ev["ts"])
            # Фикс 2026-08-26: chunk_cos (косинус топ-чанка, 0–1) честнее
            # top_score — RRF-fusion скор ~0.02–0.04 по построению и на шкале
            # 0–1 всегда выглядит «почти нулём». Старые события без chunk_cos
            # остаются на fusion-значениях (смешанная шкала в истории).
            chunk_cos = float(ev.get("chunk_cos", 0.0) or 0.0)
            top_score = chunk_cos if chunk_cos > 0.0 else float(ev.get("top_score", 0.0) or 0.0)
            
            while j < len(injs) and injs[j] < ev_ts - tol_s:
                j += 1
            
            matched = False
            if j < len(injs) and abs(injs[j] - ev_ts) <= tol_s:
                matched = True
                j += 1
            
            if matched:
                res.append({"ts": ev_ts, "value": top_score})
            else:
                res.append({"ts": ev_ts, "value": 0.0})
        return res
    except Exception as exc:
        logger.debug("_inject_relevance_series failed: %s", exc)
        return []


def _ts_charts(range_: str) -> dict:
    """Build real time-series charts from dashboard_metrics.db/ts_metrics and events.

    Returns ready-to-render SVG charts for the given range:
      { "inject_relevance", "extraction", "embed_combined", "latency" }.
    """
    try:
        now = int(time.time())
        start = now - range_seconds(range_)
        bucket = range_to_bucket(range_)

        events = read_events()
        window_events = [e for e in events if isinstance(e.get("ts"), (int, float)) and start <= e["ts"] <= now]
        all_injects = read_injects()
        inject_ts = [inj.get("ts") for inj in all_injects if isinstance(inj.get("ts"), (int, float)) and inj.get("ts") >= start - 3.0]
        pts = _inject_relevance_series(window_events, inject_ts, tol_s=3.0)
        # X axis pinned to the selected window, Y pinned to the natural 0..1
        # score domain — so the global range selector visibly zooms this
        # chart and a small score (e.g. 0.02) reads as "near zero".
        inject_relevance_chart = svg_multi(
            [{"points": pts, "color": "#C9973B"}], dots=True,
            x_min=start, x_max=now, y_min=0.0, y_max=1.0,
        )

        valid_n = series_count(["extract_valid_total"], start, now, bucket)
        fb_n = series_count(["extract_fallback_total"], start, now, bucket)
        extraction_chart = svg_multi([
            {"points": [{"ts": r["ts"], "value": r["count"]} for r in valid_n], "color": "#79A05E"},
            {"points": [{"ts": r["ts"], "value": r["count"]} for r in fb_n], "color": "#C25B43"},
        ], x_min=start, x_max=now)

        calls_n = series_count(["embed_api_calls_total"], start, now, bucket)
        err_n = series_count(["embed_api_errors_total"], start, now, bucket)
        embed_combined_chart = svg_multi([
            {"points": [{"ts": r["ts"], "value": r["count"]} for r in calls_n], "color": "#C9973B"},
            {"points": [{"ts": r["ts"], "value": r["count"]} for r in err_n], "color": "#C25B43"},
        ], x_min=start, x_max=now)

        latency = query_ts("search_duration_ms", start, now, bucket)
        latency_chart = svg_timeseries(
            [{"ts": r["ts"], "value": r["value"]} for r in latency],
            label="Задержка поиска (мс)",
            x_min=start, x_max=now,
        )

        return {
            "inject_relevance": inject_relevance_chart,
            "extraction": extraction_chart,
            "embed_combined": embed_combined_chart,
            "latency": latency_chart,
        }
    except Exception as exc:
        logger.debug("_ts_charts failed: %s", exc)
        return {}


# ── Public API ──────────────────────────────────────────────────────────────


def effectiveness() -> dict:
    """hit_rate, coverage, rating.  Empty → 0/0/'Нет данных'."""
    hr = hit_rate()
    cov = coverage()
    return {
        "hit_rate": hr,
        "coverage": cov,
        "rating": _effectiveness_rating(hr, cov),
    }


def trends(days: int = 14) -> dict:
    """Тренды hit_rate/latency по дням."""
    events = read_events()
    return {
        "hit_rate_daily": _daily_hit_rate(events),
        "latency_daily": _daily_latency(events),
    }


def indexing() -> dict:
    """last_indexed_at из status(), recent_queries = последние ~10 событий."""
    s = status()
    events = read_events()
    return {
        "last_indexed_at": s.get("last_indexed_at"),
        "recent_queries": events[-10:],
    }


def cache_stats(m: dict) -> dict:
    """cache_hit_rate из метрик.  hits=0, misses=0 → 0.0 (не NaN)."""
    hits = m.get("cache_hits_total", 0) or m.get("embed_cache_hits_total", 0)
    misses = m.get("cache_misses_total", 0)
    total = hits + misses
    return {
        "cache_hit_rate": hits / total if total else 0.0,
        "hits": hits,
        "misses": misses,
    }


# ── Problem zones ───────────────────────────────────────────────────────────


def _problem(key: str, label: str, count, source: str, working: bool,
             label_en: str = "") -> dict:
    """Build a single problem-zone entry.

    label stays Russian (tests + /api/status consumers); label_en is the
    English caption for the dashboard UI language toggle.
    """
    return {
        "key": key,
        "label": label,
        "label_en": label_en,
        "count": count if working else None,
        "source": source,
        "working": working,
        "detail": str(count) if working else "",
    }


def problems() -> dict:
    """Return problem-zone dict for the dashboard.

    Keys: not_indexed, not_extracted, oversized, junk_chunks.
    Each value: {"key", "label", "label_en", "count", "source", "working", "detail"}.
    working=False → count=None (show grey in UI).
    (merge_fallback removed 2026-08-24: it ran the same
     pages.quality='fallback' query as not_extracted and always showed
     an identical duplicate number.)
    """
    result: dict = {}

    # 1. not_indexed — sessions not yet indexed
    try:
        db = IndexDB(str(config.WIKI_PATH / ".index_v2.db"))
        rows = get_unindexed_sessions(db, limit=200, include_indexed=False)
        count = len(rows)
        result["not_indexed"] = _problem(
            "not_indexed", "Не индексировано", count,
            "get_unindexed_sessions", True,
            label_en="Not indexed",
        )
    except Exception as exc:
        logger.debug("problems.not_indexed failed: %s", exc)
        result["not_indexed"] = _problem(
            "not_indexed", "Не индексировано", None,
            "get_unindexed_sessions", False,
            label_en="Not indexed",
        )

    # 2. not_extracted — pages with quality='fallback'
    try:
        db_path = config.WIKI_PATH / ".index_v2.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM pages WHERE quality='fallback'"
        ).fetchone()
        conn.close()
        count = row["cnt"] if row else 0
        result["not_extracted"] = _problem(
            "not_extracted", "Не извлечено (fallback)", count,
            "pages.quality=='fallback'", True,
            label_en="Not extracted (fallback)",
        )
    except Exception as exc:
        logger.debug("problems.not_extracted failed: %s", exc)
        result["not_extracted"] = _problem(
            "not_extracted", "Не извлечено (fallback)", None,
            "pages.quality=='fallback'", False,
            label_en="Not extracted (fallback)",
        )

    # 3. oversized — sessions deferred due to length
    try:
        oversized = read_oversized()
        count = len(oversized)
        result["oversized"] = _problem(
            "oversized", "Отложено из-за длины", count,
            "oversized_sessions.log", True,
            label_en="Deferred (oversized)",
        )
    except Exception as exc:
        logger.debug("problems.oversized failed: %s", exc)
        result["oversized"] = _problem(
            "oversized", "Отложено из-за длины", None,
            "oversized_sessions.log", False,
            label_en="Deferred (oversized)",
        )

    # 4. junk_chunks — pages whose full_text is junk
    try:
        db_path = config.WIKI_PATH / ".index_v2.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT full_text FROM pages WHERE full_text IS NOT NULL AND full_text != ''"
        ).fetchall()
        conn.close()
        junk_count = sum(1 for r in rows if is_junk_chunk(r["full_text"] or ""))
        result["junk_chunks"] = _problem(
            "junk_chunks", "Мусорные страницы", junk_count,
            "quality.is_junk_chunk", True,
            label_en="Junk pages",
        )
    except Exception as exc:
        logger.debug("problems.junk_chunks failed: %s", exc)
        result["junk_chunks"] = _problem(
            "junk_chunks", "Мусорные страницы", None,
            "quality.is_junk_chunk", False,
            label_en="Junk pages",
        )

    return result
