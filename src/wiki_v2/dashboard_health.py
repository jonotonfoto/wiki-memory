"""Wiki Memory v3 — health snapshot for /api/health and dashboard UI.

Collects real-time health metrics of sub-systems (Search API, Embeddings,
Indexer, Extractor, Errors 24h) in a fail-open manner (never crashes JSON).
"""

from __future__ import annotations

import datetime
import os
import socket
import time
from pathlib import Path
from typing import Any

from . import config
from .dashboard_control import _read_lock_pid, _pid_alive, extraction_status, progress
from .dashboard_data import read_log_errors, read_metrics_window
from .endpoints import embed_endpoint, load as load_endpoints
from .logging_setup import logger
from .status import status as get_status

INDEX_WARN_AGE_SECONDS = 26 * 3600
INDEX_ERROR_AGE_SECONDS = 50 * 3600
SOCKET_TIMEOUT = 1.5
LOG_ERRORS_TAIL_LIMIT = 8


def cleanup_state_db() -> None:
    """Delete empty state.db (0 bytes) if present in wiki path."""
    try:
        db_p = config.WIKI_PATH / "state.db"
        if db_p.exists() and db_p.stat().st_size == 0:
            db_p.unlink(missing_ok=True)
    except Exception:
        pass


def _check_tcp(host: str, port: int, timeout: float = SOCKET_TIMEOUT) -> bool:
    """Return True if TCP connection to host:port succeeds within timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _collect_embeddings() -> dict[str, Any]:
    """Collect embeddings and embed-server health."""
    try:
        url, model, _ = embed_endpoint()
        cfg = load_endpoints()
        backend = cfg.get("embed", {}).get("backend", "nvidia")
        
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        
        is_local = "127.0.0.1" in host or "localhost" in host or "integrate.api.nvidia.com" not in host
        server_alive = _check_tcp(host, port) if is_local else True
        
        metrics = read_metrics_window(hours=24)
        errors_24h = int(metrics.get("embed_api_errors_total", 0))
        calls_24h = int(metrics.get("embed_api_calls_total", 0))
        
        status = "ok"
        detail = f"{backend} ({model})"
        if is_local and not server_alive:
            status = "error"
            detail = f"Сервер эмбеддингов недоступен на {host}:{port}"
        elif errors_24h > 10:
            status = "warn"
            detail = f"Много ошибок эмбеддингов: {errors_24h} за сутки"
            
        return {
            "status": status,
            "backend": backend,
            "model": model,
            "url": url,
            "server_alive": server_alive,
            "errors_24h": errors_24h,
            "calls_24h": calls_24h,
            "detail": detail,
        }
    except Exception as exc:
        logger.debug("_collect_embeddings failed: %s", exc)
        return {"status": "unknown", "detail": str(exc)}


def _collect_watchdog() -> dict[str, Any]:
    """Check embed monitor watchdog pidfile."""
    try:
        scripts_root = Path(__file__).resolve().parent.parent
        pidfile = Path(os.environ.get("WIKI_EMBED_MONITOR_PID", str(scripts_root / "wiki_embed_monitor.pid")))
        if not pidfile.exists():
            return {"status": "unknown", "alive": False, "detail": "Сторож не запущен (нет pidfile)"}
        
        text = pidfile.read_text(encoding="utf-8").strip()
        if not text:
            return {"status": "unknown", "alive": False, "detail": "Пустой pidfile сторожа"}
        
        pid = int(text)
        alive = _pid_alive(pid)
        status = "ok" if alive else "warn"
        detail = f"Сторож активен (pid {pid})" if alive else f"Сторож мёртв (pid {pid} не найден)"
        return {
            "status": status,
            "alive": alive,
            "pid": pid,
            "detail": detail,
        }
    except Exception as exc:
        logger.debug("_collect_watchdog failed: %s", exc)
        return {"status": "unknown", "detail": str(exc)}


def _collect_indexer() -> dict[str, Any]:
    """Check indexer status, backlog, and last indexed age."""
    try:
        s = get_status()
        last_indexed = s.get("last_indexed_at")
        now = time.time()
        age_s = (now - last_indexed) if isinstance(last_indexed, (int, float)) else None
        
        lock_pid = _read_lock_pid()
        running = lock_pid is not None and _pid_alive(lock_pid)
        
        backlog = 0
        try:
            from .indexer import get_unindexed_sessions
            backlog = len(get_unindexed_sessions())
        except Exception:
            pass
            
        status = "ok"
        detail = "Индексация в норме"
        
        if running:
            detail = f"Индексация выполняется (pid {lock_pid})"
        elif age_s is not None:
            hours = age_s / 3600
            if age_s > INDEX_ERROR_AGE_SECONDS:
                status = "error"
                detail = f"Давно не индексировалось ({hours:.1f} ч)"
            elif age_s > INDEX_WARN_AGE_SECONDS:
                status = "warn"
                detail = f"Ожидание индексации ({hours:.1f} ч)"
            else:
                detail = f"Последняя индексация {hours:.1f} ч назад"
        else:
            detail = "Индексация ещё не запускалась"
            
        return {
            "status": status,
            "running": running,
            "lock_pid": lock_pid,
            "last_indexed_age_s": age_s,
            "backlog_sessions": backlog,
            "detail": detail,
        }
    except Exception as exc:
        logger.debug("_collect_indexer failed: %s", exc)
        return {"status": "unknown", "detail": str(exc)}


def _collect_extractor() -> dict[str, Any]:
    """Check extractor state, progress, and pending facts."""
    try:
        ext_st = extraction_status()
        prog = progress()
        
        pending_facts = 0
        try:
            facts_path = config.WIKI_PATH / ".facts_pending.jsonl"
            if facts_path.exists():
                with open(facts_path, "r", encoding="utf-8") as f:
                    pending_facts = sum(1 for line in f if line.strip())
        except Exception:
            pass
            
        running = ext_st.get("running", False)
        last_error = ext_st.get("last_error")
        
        status = "ok"
        if last_error:
            status = "error"
            detail = f"Ошибка экстракции: {last_error}"
        elif running:
            detail = f"Экстракция активна ({prog.get('done', 0)}/{prog.get('total', 0)})"
        else:
            detail = f"Экстракция остановлена · фактов в очереди: {pending_facts}"
            
        return {
            "status": status,
            "running": running,
            "state": "running" if running else "idle",
            "progress": prog,
            "pending_facts": pending_facts,
            "last_error": last_error,
            "detail": detail,
        }
    except Exception as exc:
        logger.debug("_collect_extractor failed: %s", exc)
        return {"status": "unknown", "detail": str(exc)}


def _collect_errors_24h() -> dict[str, Any]:
    """Collect error counters (real 24h window) and log tail."""
    try:
        metrics = read_metrics_window(hours=24)
        chat_errors = int(metrics.get("chat_api_errors_total", 0))
        search_fallback = int(metrics.get("search_fallback_total", 0))
        embed_errors = int(metrics.get("embed_api_errors_total", 0))
        
        log_tail = read_log_errors(hours=24)
        if len(log_tail) > LOG_ERRORS_TAIL_LIMIT:
            log_tail = log_tail[-LOG_ERRORS_TAIL_LIMIT:]
            
        total_errs = chat_errors + embed_errors + len(log_tail)
        status = "ok" if total_errs == 0 else ("warn" if total_errs < 5 else "error")
        
        return {
            "status": status,
            "chat_api_errors_24h": chat_errors,
            "search_fallback_total": search_fallback,
            "embed_api_errors_24h": embed_errors,
            "log_tail": log_tail,
            "detail": f"Ошибок за 24ч: чат {chat_errors}, эмбеддинги {embed_errors}, лог {len(log_tail)}",
        }
    except Exception as exc:
        logger.debug("_collect_errors_24h failed: %s", exc)
        return {"status": "unknown", "detail": str(exc)}


def night_strip_events() -> list[dict[str, Any]]:
    """Return events for the last 24 hours for the Night Strip (Лента ночи).

    Fail-open -> [].
    Each event: {"ts": float, "type": "error"|"warn"|"index"|"watchdog", "text": str, "pct": float}
    """
    try:
        now = time.time()
        start_24h = now - 86400
        events = []
        
        log_path = config.WIKI_PATH / "logs" / "wiki_v2.log"
        if log_path.exists():
            try:
                with open(log_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if "WARNING" not in line and "ERROR" not in line:
                            continue
                        parts = line.split(" | ")
                        if len(parts) >= 2:
                            ts_str = parts[0].strip()
                            try:
                                dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                                dt_utc = dt.replace(tzinfo=datetime.timezone.utc)
                                ev_ts = dt_utc.timestamp()
                                if ev_ts >= start_24h:
                                    level = "error" if "ERROR" in parts[1] else "warn"
                                    msg = " | ".join(parts[2:]).strip() if len(parts) > 2 else parts[1]
                                    events.append({
                                        "ts": ev_ts,
                                        "type": level,
                                        "text": msg[:120],
                                    })
                            except Exception:
                                pass
            except Exception:
                pass
        
        try:
            s = get_status()
            last_indexed = s.get("last_indexed_at")
            if isinstance(last_indexed, (int, float)) and last_indexed >= start_24h:
                events.append({
                    "ts": last_indexed,
                    "type": "index",
                    "text": f"Индексация завершена ({datetime.datetime.fromtimestamp(last_indexed, tz=datetime.timezone.utc).strftime('%H:%M')})",
                })
        except Exception:
            pass
            
        try:
            scripts_root = Path(__file__).resolve().parent.parent
            pidfile = Path(os.environ.get("WIKI_EMBED_MONITOR_PID", str(scripts_root / "wiki_embed_monitor.pid")))
            if pidfile.exists():
                mtime = pidfile.stat().st_mtime
                if mtime >= start_24h:
                    events.append({
                        "ts": mtime,
                        "type": "watchdog",
                        "text": "Сторож обновлен (pidfile mtime)",
                    })
        except Exception:
            pass
            
        events.sort(key=lambda x: x["ts"])
        if len(events) > 60:
            events = events[-60:]
            
        result = []
        for ev in events:
            pct = max(0.0, min(100.0, ((ev["ts"] - start_24h) / 86400.0) * 100.0))
            result.append({
                "ts": ev["ts"],
                "type": ev["type"],
                "text": ev["text"],
                "pct": round(pct, 2),
            })
        return result
    except Exception as exc:
        logger.debug("night_strip_events failed: %s", exc)
        return []


def _collect_search_api() -> dict[str, Any]:
    """Check search availability via the index DB (search runs in-process
    inside Hermes, so from the dashboard we verify the index it reads)."""
    try:
        s = get_status()
        vectors = int(s.get("vectors", 0) or 0)
        if s.get("error"):
            return {"status": "error", "detail": "Индекс недоступен (.index_v2.db не читается)"}
        if vectors <= 0:
            return {"status": "warn", "detail": "Индекс пуст — поиск ничего не найдёт"}
        return {
            "status": "ok",
            "detail": f"Индекс доступен · {vectors} векторов",
            "vectors": vectors,
        }
    except Exception as exc:
        logger.debug("_collect_search_api failed: %s", exc)
        return {"status": "unknown", "detail": str(exc)}


def health_snapshot() -> dict[str, Any]:
    """Return a comprehensive health snapshot of all wiki v3 components."""
    cleanup_state_db()
    try:
        search_api = _collect_search_api()
        embeddings = _collect_embeddings()
        watchdog = _collect_watchdog()
        indexer = _collect_indexer()
        extractor = _collect_extractor()
        errors = _collect_errors_24h()

        statuses = [search_api.get("status"), embeddings.get("status"), watchdog.get("status"), indexer.get("status"), extractor.get("status"), errors.get("status")]
        overall = "ok"
        if "error" in statuses:
            overall = "error"
        elif "warn" in statuses or "unknown" in statuses:
            overall = "warn"

        return {
            "overall": overall,
            "timestamp": time.time(),
            "components": {
                "search_api": search_api,
                "embeddings": embeddings,
                "watchdog": watchdog,
                "indexer": indexer,
                "extractor": extractor,
            },
            "errors_24h": errors,
        }
    except Exception as exc:
        logger.error("health_snapshot failed: %s", exc)
        return {
            "overall": "unknown",
            "timestamp": time.time(),
            "components": {},
            "errors_24h": {"status": "unknown", "detail": str(exc)},
        }
