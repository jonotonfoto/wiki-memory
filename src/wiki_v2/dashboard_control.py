"""Wiki v3 — extraction (indexing) control.

Manages background ``python -m wiki_v2.indexer`` lifecycle:
start_extraction(mode), stop_extraction(), extraction_status(), progress().

All functions are fail-open: they never raise, returning safe defaults on error.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from . import config
from .logging_setup import logger


def _endpoints_chat():
    """Активный chat-эндпоинт через единый фасад gateway: (url, model).

    Фасад (gateway.chat_endpoint) читает endpoints.yaml — единственный источник
    правды. Локальную модель для облака НЕ грузим (no-op в gateway.ensure_chat_ready).
    """
    try:
        from .gateway import chat_endpoint
        return chat_endpoint()
    except Exception:
        return ("http://127.0.0.1:1234/v1/chat/completions", "gpt-oss-20b")

# ---------------------------------------------------------------------------
# Internal state — guarded by _lock so concurrent callers can't race.
# ---------------------------------------------------------------------------
_lock = threading.Lock()

_status: dict[str, Any] = {
    "running": False,
    "pid": None,
    "mode": None,       # "normal" | "full"
    "done": 0,
    "started_at": None,
    "last_error": None,
    "proc": None,       # subprocess.Popen | None
}

# Root of the scripts directory (parent of the wiki_v2 package).
_SCRIPTS_ROOT: Path = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Interpreter selection for the spawned indexer.
#
# The dashboard server itself may run under a python WITHOUT numpy (cron
# keeps it alive under its own interpreter).  ``wiki_v2.indexer`` requires
# numpy, so we probe candidates once per process and cache the first that
# passes.  Priority: $WIKI_INDEXER_PYTHON → Hermes venv → sys.executable.
# ---------------------------------------------------------------------------
_indexer_python_cache: str | None = None
_py_lock = threading.Lock()


def _candidate_interpreters() -> list[str]:
    out: list[str] = []
    env_py = os.environ.get("WIKI_INDEXER_PYTHON")
    if env_py and Path(env_py).exists():
        out.append(env_py)
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        for name in ("pythonw.exe", "python.exe"):
            cand = Path(hermes_home) / "hermes-agent" / "venv" / "Scripts" / name
            if cand.exists():
                out.append(str(cand))
                break
    out.append(sys.executable)
    return out


def _interpreter_can_import(exe: str) -> bool:
    try:
        r = subprocess.run(
            [exe, "-c", "import numpy"],
            cwd=str(_SCRIPTS_ROOT),
            capture_output=True, timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return r.returncode == 0
    except Exception:
        return False


def indexer_python() -> str:
    """Interpreter able to run ``-m wiki_v2.indexer`` (cached per process).

    Отдельный лок (_py_lock): вызывается внутри start_extraction, которая уже
    держит _lock — повторный захват непереentrantного Lock дал бы дедлок.
    """
    global _indexer_python_cache
    with _py_lock:
        if _indexer_python_cache is None:
            for exe in _candidate_interpreters():
                if exe == sys.executable or _interpreter_can_import(exe):
                    _indexer_python_cache = exe
                    break
            else:
                _indexer_python_cache = sys.executable
        return _indexer_python_cache


def _read_lock_pid() -> int | None:
    """Read the PID of the running indexer from ``.index.lock`` (if any).

    The lock file is written by the indexer itself (single-process guard).
    This lets the dashboard stop ANY extraction process — even one launched
    by cron or manually — not just one started via ``start_extraction``.
    """
    try:
        lock_path = Path(str(config.WIKI_PATH)) / ".index.lock"
        if not lock_path.exists():
            return None
        text = lock_path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        pid = int(text)
        return pid if pid > 0 else None
    except (OSError, ValueError, TypeError):
        return None


def _resolve_windowless(exe: str, env: dict) -> str:
    """Обход venv-трамплина, чтобы CREATE_NO_WINDOW реально работал.

    pythonw.exe из venv (uv) — трамплин: он перезапускает консольный базовый
    python.exe БЕЗ наших creation flags → всплывает чёрное консольное окно.
    Возвращаем настоящий GUI-subsystem pythonw.exe базового интерпретатора
    (home из pyvenv.cfg), а пакеты венва отдаём через PYTHONPATH.
    Не venv / нет файлов → exe без изменений (fail-open).
    """
    try:
        p = Path(exe)
        if os.name != "nt" or p.parent.name.lower() != "scripts":
            return exe
        venv_dir = p.parent.parent
        cfg = venv_dir / "pyvenv.cfg"
        if not cfg.exists():
            return exe
        home = ""
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().lower().startswith("home"):
                _, _, val = line.partition("=")
                home = val.strip()
                break
        if not home:
            return exe
        base_pw = Path(home) / "pythonw.exe"
        site_pkgs = venv_dir / "Lib" / "site-packages"
        if base_pw.exists() and site_pkgs.exists():
            extra = str(site_pkgs)
            if env.get("PYTHONPATH"):
                extra = extra + os.pathsep + env["PYTHONPATH"]
            env["PYTHONPATH"] = extra
            return str(base_pw)
    except Exception:
        return exe
    return exe


def start_extraction(mode: str = "normal", limit: int | None = None) -> dict:
    """Start background extraction (indexing).

    Parameters
    ----------
    mode : str
        ``"normal"`` — background, up to MAX_SESSIONS_PER_RUN sessions.
        ``"full"`` — same runner with a very high cap (no practical limit).
    limit : int | None
        Explicit page/session cap for this run (dashboard field
        «Страниц за запуск», default 5 in the indexer). Overrides the
        mode default; clamped to 1..100000, invalid values are ignored
        (fail-open → indexer default).

    Returns
    -------
    dict
        ``{"ok": True, "pid": <pid>, "mode": <mode>}`` on success.
        ``{"ok": False, "error": <reason>}`` on failure.
    """
    with _lock:
        if _status["running"]:
            return {"ok": False, "error": "already_running"}

        try:
            env = os.environ.copy()
            # For "full" mode, push a very high cap so the indexer doesn't
            # stop early.  The indexer reads WIKI_MAX_SESSIONS_PER_RUN.
            if mode == "full":
                env["WIKI_MAX_SESSIONS_PER_RUN"] = "100000"
            if limit is not None:
                try:
                    n = max(1, min(int(limit), 100000))
                    env["WIKI_MAX_SESSIONS_PER_RUN"] = str(n)
                except (TypeError, ValueError):
                    pass  # fail-open: битый лимит → дефолт индексера

            # Экстракция идёт по активному chat-эндпоинту из единого конфига
            # endpoints.yaml (сейчас — облако NVIDIA nemotron-3-super-120b-a12b).
            # Локальную модель НЕ загружаем вообще: облако не требует LM Studio,
            # а принудительная загрузка gpt-oss-20b мешала другим локальным моделям.
            _chat_url, _chat_model = _endpoints_chat()
            env["NVIDIA_CHAT_MODEL"] = _chat_model
            env["NVIDIA_API_URL"] = _chat_url

            _pflags = 0
            if os.name == "nt":
                _pflags = subprocess.CREATE_NO_WINDOW  # не показывать консольное окно python.exe
            exe = indexer_python()
            exe = _resolve_windowless(exe, env)
            proc = subprocess.Popen(
                [exe, "-m", "wiki_v2.indexer"],
                cwd=str(_SCRIPTS_ROOT),
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_pflags,
            )

            _status.update({
                "running": True,
                "pid": proc.pid,
                "mode": mode,
                "started_at": __import__("time").time(),
                "last_error": None,
                "proc": proc,
            })

            # If the process exits immediately, mark as failed.
            if proc.poll() is not None:
                _status["running"] = False
                _status["last_error"] = f"process exited immediately with code {proc.returncode}"
                _status["proc"] = None
                return {"ok": False, "error": _status["last_error"]}

            logger.info("extraction started pid=%d mode=%s python=%s", proc.pid, mode, exe)
            return {"ok": True, "pid": proc.pid, "mode": mode}

        except Exception as exc:
            logger.error("start_extraction failed: %s", exc)
            _status["last_error"] = str(exc)
            return {"ok": False, "error": str(exc)}


def stop_extraction() -> dict:
    """Stop the running extraction process.

    Stops gracefully (soft stop): writes ``.stop_request`` so the indexer
    finishes the CURRENT page/session, then waits. Force-kills only if the
    soft stop times out (avoids tearing a page mid-write).

    Works for ANY extraction process — even one started by cron or manually —
    because it falls back to reading the PID from ``.index.lock`` when the
    process was not started via ``start_extraction``.

    Returns
    -------
    dict
        ``{"ok": True}`` on success.
        ``{"ok": False, "error": <reason>}`` on failure.
    """
    with _lock:
        proc = _status.get("proc")
        pid = _status.get("pid")
        own = proc is not None

        if not own:
            # Not started via dashboard — try the lock file (cron/manual indexer).
            pid = _read_lock_pid()
            if pid is None:
                return {"ok": False, "error": "not_running"}

        stop_flag = os.path.join(str(config.WIKI_PATH), ".stop_request")
        try:
            Path(stop_flag).write_text("1", encoding="utf-8")
        except Exception as _e:
            logger.warning("stop_extraction: cannot write stop flag: %s", _e)

        soft_timed_out = False
        try:
            if own and proc is not None:
                # We own the subprocess — wait for it to finish the page.
                proc.wait(timeout=120)
            elif pid is not None:
                # External process (cron/manual): we cannot wait on a Popen we
                # don't own. Sleep-poll the lock file until it disappears
                # (indexer exits and removes it), or the process dies.
                import time as _time
                waited = 0
                while waited < 120:
                    _time.sleep(2)
                    waited += 2
                    if _read_lock_pid() is None:
                        break  # indexer exited, lock removed
        except subprocess.TimeoutExpired:
            # Мягкий стоп не уложился в 120с — НЕ выходим с ошибкой (раньше
            # общий except возвращал {"ok": False} и до форс-килла/снятия флага
            # дело не доходило, вопреки докстрингу). Проваливаемся в kill ниже.
            soft_timed_out = True
        except Exception as _exc:
            logger.error("stop_extraction failed: %s", _exc)
            return {"ok": False, "error": str(_exc)}

        # Force-kill fallback if it's still running after the wait.
        still_pid = _read_lock_pid() or (pid if pid and _pid_alive(pid) else None)
        if soft_timed_out and still_pid:
            logger.warning("stop_extraction: soft stop timed out, force kill pid=%d", still_pid)
            _kill_pid(still_pid)
        elif still_pid:
            logger.warning("stop_extraction: процесс ещё жив после мягкого стопа pid=%d", still_pid)

        # Remove the stop flag.
        try:
            Path(stop_flag).unlink(missing_ok=True)
        except Exception:
            pass

        # Reset our own state.
        _status["running"] = False
        _status["pid"] = None
        _status["proc"] = None
        logger.info("extraction stopped (graceful) pid=%s", pid)
        return {"ok": True}


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID exists.

    Windows: ``os.kill(pid, 0)`` is unreliable — it raises ``SystemError``
    (not ``OSError``) for dead PIDs, and signals aren't really supported.
    Use ``tasklist`` instead (reliable, built-in). On POSIX use ``os.kill``.
    """
    import subprocess as _sp
    if _os_name_nt():
        try:
            # CREATE_NO_WINDOW: иначе каждый вызов tasklist создаёт conhost
            # (чёрное консольное окно), которое мигает при поллинге /api/control
            # во время экстракции. Остальные subprocess в проекте уже используют этот флаг.
            _flags = 0
            if _os_name_nt():
                _flags = getattr(_sp, "CREATE_NO_WINDOW", 0)
            out = _sp.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                          capture_output=True, timeout=15, creationflags=_flags)
            # tasklist на русской Windows выводит в OEM/UTF-16 — декодируем толерантно.
            text = out.stdout.decode("utf-8", errors="replace")
            return str(pid) in text
        except Exception:
            return False
    import os as _os
    try:
        _os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_pid(pid: int) -> None:
    """Force-kill a process by PID (Windows-safe)."""
    import subprocess as _sp
    if _os_name_nt():
        try:
            _sp.run(["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True, timeout=15)
        except Exception as _e:
            logger.warning("stop_extraction: taskkill failed: %s", _e)
    else:
        import os as _os
        try:
            _os.kill(pid, 9)
        except OSError:
            pass


def _os_name_nt() -> bool:
    import os as _os
    return _os.name == "nt"


def extraction_status() -> dict:
    """Return a snapshot of the current extraction status.

    The ``proc`` object is stripped from the output.

    ``running`` is True if either (a) a process was started via
    ``start_extraction`` and is alive, OR (b) an indexer lock file
    (``.index.lock``) exists — meaning SOME extraction process is running,
    regardless of who started it (dashboard, cron, manual).
    """
    with _lock:
        proc = _status.get("proc")
        # If running and process has finished, clean up.
        if _status["running"] and proc is not None:
            rc = proc.poll()
            if rc is not None:
                _status["running"] = False
                _status["pid"] = None
                _status["proc"] = None
                if rc != 0:
                    _status["last_error"] = f"process exited with code {rc}"

        # Determine running: our own process, OR an external lock file.
        own_running = bool(_status["running"])
        lock_pid = _read_lock_pid()
        # External lock is "running" only if the PID in it is actually alive.
        external_running = bool(lock_pid) and _pid_alive(lock_pid) and not own_running

        # Return a clean copy (no proc object).
        return {
            "running": own_running or external_running,
            "pid": _status["pid"] if own_running else lock_pid,
            # mode осмыслен только для СВОЕГО запуска; для внешнего (cron/ручной)
            # оставлять старое значение — враньё (баг 2026-08-25).
            "mode": _status["mode"] if own_running else None,
            "done": _status["done"],
            "started_at": _status["started_at"] if own_running else None,
            "last_error": _status["last_error"],
        }


def progress() -> dict:
    """Compute indexing progress from the database.

    Reads ``status()`` for ``pages`` (done) and ``problems()`` for
    ``not_indexed`` count.  Total = done + not_indexed.

    Returns
    -------
    dict
        ``{"done": N, "total": M, "pct": float}``
    """
    try:
        from .dashboard_analysis import problems as _problems_fn
        from .status import status as _status_fn
    except ImportError:
        return {"done": 0, "total": 0, "pct": 0.0}

    try:
        done = _status_fn().get("pages", 0)
    except Exception:
        done = 0

    try:
        not_indexed = _problems_fn().get("not_indexed", {}).get("count")
    except Exception:
        not_indexed = None

    if not_indexed is None:
        total = done
        pct = 100.0
    else:
        total = done + not_indexed
        pct = (done / total * 100) if total else 0.0

    return {"done": done, "total": total, "pct": round(pct, 2)}


# ---------------------------------------------------------------------------
# Endpoint configuration helpers (GET/POST /api/config)
# ---------------------------------------------------------------------------

_EXTRACT_DEFAULT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_EXTRACT_DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def get_extract_endpoint() -> dict:
    """Current extraction (LLM) endpoint. Read from env.

    Returns
    -------
    dict
        ``{"url": str, "model": str, "key_set": bool}``
    """
    try:
        url = os.environ.get("NVIDIA_API_URL", _EXTRACT_DEFAULT_URL)
        model = os.environ.get("NVIDIA_CHAT_MODEL", _EXTRACT_DEFAULT_MODEL)
        key = os.environ.get("NVIDIA_API_KEY", "")
        return {"url": url, "model": model, "key_set": bool(key)}
    except Exception as exc:
        logger.error("get_extract_endpoint failed: %s", exc)
        return {"url": _EXTRACT_DEFAULT_URL, "model": _EXTRACT_DEFAULT_MODEL, "key_set": False}


def set_extract_endpoint(url=None, key=None, model=None) -> dict:
    """Update extraction endpoint env vars (fail-open).

    Updates ``os.environ`` only — does NOT write to .env file.
    Affects new subprocesses / future imports.

    Returns
    -------
    dict
        ``{"ok": True}``
    """
    try:
        if url is not None and url != "":
            os.environ["NVIDIA_API_URL"] = url
        if model is not None and model != "":
            os.environ["NVIDIA_CHAT_MODEL"] = model
        if key is not None and key != "":
            os.environ["NVIDIA_API_KEY"] = key
        return {"ok": True}
    except Exception as exc:
        logger.error("set_extract_endpoint failed: %s", exc)
        return {"ok": False, "error": str(exc)}


def get_embed_endpoint() -> dict:
    """Current embedding endpoint. Read from config.

    Returns
    -------
    dict
        ``{"backend": str, "url": str, "model": str, "dim": int}``
    """
    try:
        from . import config

        backend = config.EMBED_BACKEND
        if backend == "lmstudio":
            url = config.LMSTUDIO_URL
            model = config.LMSTUDIO_MODEL
        elif backend == "llamaserver":
            url = config.LLAMASERVER_URL
            model = config.LLAMASERVER_MODEL
        else:
            # nvidia or unknown — fall back to nvidia defaults
            url = os.environ.get("NVIDIA_EMBED_URL", "https://integrate.api.nvidia.com/v1/embeddings")
            model = os.environ.get("NVIDIA_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")
        return {"backend": backend, "url": url, "model": model, "dim": config.EMBED_DIM}
    except Exception as exc:
        logger.error("get_embed_endpoint failed: %s", exc)
        return {"backend": "nvidia", "url": "https://integrate.api.nvidia.com/v1/embeddings", "model": "nvidia/nv-embedqa-e5-v5", "dim": 1024}


def set_embed_endpoint(backend=None, url=None, model=None) -> dict:
    """Update embedding endpoint env vars (fail-open).

    Parameters
    ----------
    backend : str | None
        ``"nvidia"`` | ``"lmstudio"`` | ``"llamaserver"``
    url : str | None
        Embedding service URL.
    model : str | None
        Embedding model name.

    Returns
    -------
    dict
        ``{"ok": True, "requires_reindex": bool}``
    """
    try:
        from . import config

        old_backend = config.EMBED_BACKEND
        requires_reindex = False

        if backend is not None and backend != "":
            os.environ["WIKI_EMBED_BACKEND"] = backend
            requires_reindex = (backend != old_backend)

        if url is not None and url != "":
            if backend == "lmstudio" or (backend is None and old_backend == "lmstudio"):
                os.environ["LMSTUDIO_URL"] = url
            elif backend == "llamaserver" or (backend is None and old_backend == "llamaserver"):
                os.environ["LLAMASERVER_URL"] = url
            else:
                os.environ["NVIDIA_EMBED_URL"] = url

        if model is not None and model != "":
            if backend == "lmstudio" or (backend is None and old_backend == "lmstudio"):
                os.environ["LMSTUDIO_MODEL"] = model
            elif backend == "llamaserver" or (backend is None and old_backend == "llamaserver"):
                os.environ["LLAMASERVER_MODEL"] = model
            else:
                os.environ["NVIDIA_EMBED_MODEL"] = model

        return {"ok": True, "requires_reindex": requires_reindex}
    except Exception as exc:
        logger.error("set_embed_endpoint failed: %s", exc)
        return {"ok": False, "requires_reindex": False, "error": str(exc)}


def api_config_get() -> dict:
    """Full config snapshot for GET /api/config.

    Returns
    -------
    dict
        ``{"extract": {...}, "embed": {...}}``
    """
    try:
        return {
            "extract": get_extract_endpoint(),
            "embed": get_embed_endpoint(),
        }
    except Exception as exc:
        logger.error("api_config_get failed: %s", exc)
        return {
            "extract": {"url": _EXTRACT_DEFAULT_URL, "model": _EXTRACT_DEFAULT_MODEL, "key_set": False},
            "embed": {"backend": "nvidia", "url": "https://integrate.api.nvidia.com/v1/embeddings", "model": "nvidia/nv-embedqa-e5-v5", "dim": 1024},
        }


def api_config_set(data: dict) -> dict:
    """Handle POST /api/config.

    Parameters
    ----------
    data : dict
        ``{"section": "extract" | "embed", ...fields}``

    Returns
    -------
    dict
        ``{"ok": True, "requires_reindex": bool}`` or error.
    """
    try:
        section = data.get("section")
        if section == "extract":
            return set_extract_endpoint(
                url=data.get("url"),
                key=data.get("key"),
                model=data.get("model"),
            )
        if section == "embed":
            return set_embed_endpoint(
                backend=data.get("backend"),
                url=data.get("url"),
                model=data.get("model"),
            )
        return {"ok": False, "error": "unknown section"}
    except Exception as exc:
        logger.error("api_config_set failed: %s", exc)
        return {"ok": False, "error": str(exc)}
