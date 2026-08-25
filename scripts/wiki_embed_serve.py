"""wiki_embed_serve.py — авто-поддержка llama-server (эмбеддинги на CPU).

Два режима:
- **default (одноразовый, вызывается cron Hermes каждые 5 мин):** проверяет, что
  llama-server поднят (если Hermes жив) и что долгоживущий сторож (`--monitor`)
  запущен; сам завершается. Держит инфраструктуру, но не блокирует cron-тик.
- **`--monitor` (долгоживущий сторож, запускается одноразовым как detached):**
  сам порождает llama-server, а затем в цикле СЛЕДИТ ЗА ПРОЦЕССОМ HERMES прямо
  «в сервере» (в управляющем процессе). Когда Hermes закрывается и работа с БД
  завершена — сторож сам останавливает llama-server и завершается (выгружается).

Так решается «выгрузка при закрытом Hermes» без внешнего планировщика: сторож живёт
независимо от cron (DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP), поэтому переживает
закрытие Hermes и доводит auto-unload до конца.

Защита от поломки: сервер/сторож НЕ выгружаются, пока идёт индексация или свежая
работа с базой. Индикаторы активности:
  - захвачен файл-лок индексации ``.index.lock`` (PID жив) — активный прогон;
  - живы процессы wiki-индексатора / догонки эмбеддингов / дашборда;
  - свежие (последние ``IDLE_MINS`` минут) записи wiki-индекса выходят в лог.
Если Hermes закрыт, но активность ЕСТЬ — сервер остаётся жить до завершения работы,
и выгружается только когда и Hermes закрыт, и активность прекратилась.

Эмбеддинги считаются НА ПРОЦЕССОРЕ (CPU-сборка llama.cpp, без CUDA) той же моделью
Qwen3-Embedding-0.6B Q8_0, что и LM Studio, поэтому векторы совместимы (dim 1024) —
пере-эмбеддинг не нужен. VRAM модель не занимает совсем.

Выход: пустой stdout (тихо), либо одна строка с сообщением, когда что-то сделано.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LLAMA_DIR = Path(os.environ.get("WIKI_LLAMA_DIR", r"%LOCALAPPDATA%\hermes\llama.cpp"))
LLAMA_SERVER = LLAMA_DIR / "llama-server.exe"

# Дефолт — та же модель Q8_0, что использует LM Studio (векторы идентичны).
MODEL = os.environ.get(
    "WIKI_EMBED_GGUF",
    r"H:\Ai\Lm studio model\PeterAM4\Qwen3-Embedding-0.6B-GGUF\Qwen3-Embedding-0.6B-Q8_0.gguf",
)
PORT = int(os.environ.get("WIKI_EMBED_PORT", "11435"))
PORT_HOST = "127.0.0.1"
CTX = int(os.environ.get("WIKI_EMBED_CTX", "512"))
THREADS = int(os.environ.get("WIKI_EMBED_THREADS", "20"))
HERMES_PROC = os.environ.get("WIKI_HERMES_PROC", "Hermes")

# Интервал цикла сторожа (сек) и файл-лок единственного экземпляра сторожа.
MONITOR_INTERVAL = int(os.environ.get("WIKI_EMBED_MONITOR_INTERVAL", "30"))
MONITOR_PIDFILE = Path(os.environ.get("WIKI_EMBED_MONITOR_PID", str(HERE / "wiki_embed_monitor.pid")))

# Каталог данных wiki (рядом живёт .index.lock и .index_v2.db).
WIKI_PATH = Path(os.environ.get("WIKI_PATH", str(Path.home() / "AppData/Local/hermes/wiki")))
INDEX_LOCK = Path(os.environ.get("WIKI_INDEX_LOCK", str(WIKI_PATH / ".index.lock")))
LOG_PATH = Path(os.environ.get("WIKI_LOG_PATH", str(WIKI_PATH / "logs/wiki_v2.log")))

# Сколько минут «тишины» по активным индикаторам нужно, чтобы счесть работу завершённой
# и разрешить выгрузку сервера после закрытия Hermes.
IDLE_MINS = int(os.environ.get("WIKI_EMBED_IDLE_MINS", "10"))

_DETACH = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP


def _port_is_listening() -> bool:
    try:
        with socket.create_connection((PORT_HOST, PORT), timeout=1.0):
            return True
    except OSError:
        return False


def _lock_pid_alive() -> bool:
    """True, если файл-лок индексации существует и его PID жив (идёт прогон)."""
    if not INDEX_LOCK.exists():
        return False
    try:
        pid = int(INDEX_LOCK.read_text().strip())
    except Exception:
        return INDEX_LOCK.exists()
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return True


def _active_subprocess() -> bool:
    """True, если жива реальная работа с БД, которую нельзя прерывать выгрузкой.

    Это индексация/догонка/обходы/экстракция — процессы, которые ПИШУТ индекс и
    зависят от эмбеддингов. Дашборд НЕ входит: это постоянный сервис (его держит
    свой cron каждые 5 мин), он вектор-индекс не трогает — не блокирует выгрузку.
    Исключаем и сам watchdog (--monitor), чтобы он не считал себя «работой».
    """
    markers = [
        "indexer.py", "wiki_v3_sweep_loader.py", "wiki_embed_backfill_loader.py",
        "page_chunk_backfill", "llm_extractor", "github_extractor",
    ]
    self_name = Path(__file__).resolve().name
    try:
        import psutil
        for p in psutil.process_iter(["name", "cmdline"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if self_name in cmd or "--monitor" in cmd:
                continue
            if any(m in cmd for m in markers):
                return True
    except Exception:
        pass
    return False


def _recent_log_activity(cutoff: float) -> bool:
    """True, если индекс-лог изменялся позже cutoff (свежая работа с БД)."""
    try:
        return LOG_PATH.stat().st_mtime > cutoff
    except OSError:
        return False


def _work_active() -> bool:
    """Любая активная работа с базой/индексацией — сервер выгружать нельзя."""
    return _lock_pid_alive() or _active_subprocess() or _recent_log_activity(time.time() - IDLE_MINS * 60)


def _proc_target() -> str:
    return HERMES_PROC[:-4] if HERMES_PROC.lower().endswith(".exe") else HERMES_PROC


def _hermes_alive() -> bool:
    target = _proc_target()
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            name = (p.info.get("name") or "")
            if name.lower().endswith(".exe"):
                name = name[:-4]
            if name.lower() == target.lower():
                return True
        return False
    except Exception:
        pass
    # fallback без psutil: пробуем по имени через tasklist
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {target}.exe"],
            capture_output=True, text=True, creationflags=0x08000000,
        ).stdout
        return target.lower() in out.lower()
    except Exception:
        return True  # fail-open: не можем проверить -> не трогаем сервер


def _start_server() -> None:
    if not LLAMA_SERVER.exists():
        print(f"wiki_embed_serve: llama-server не найден: {LLAMA_SERVER}", file=sys.stderr)
        return
    log = HERE / "wiki_embed_serve.log"
    args = [
        str(LLAMA_SERVER),
        "-m", str(MODEL),
        "--embedding",
        "-c", str(CTX),
        "--threads", str(THREADS),
        "--port", str(PORT),
        "--host", PORT_HOST,
    ]
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%F %T")
    except Exception:
        stamp = time.strftime("%F %T")
    with open(log, "a", encoding="utf-8", errors="replace") as lf:
        lf.write(f"\n===== {stamp} launch =====\n")
        subprocess.Popen(
            args,
            cwd=str(LLAMA_DIR),
            stdout=lf,
            stderr=subprocess.STDOUT,
            creationflags=_DETACH,
        )
    print(f"wiki_embed_serve: llama-server запущен (CPU, port {PORT})")


def _stop_server() -> None:
    if not _port_is_listening():
        return
    killed = False
    try:
        import psutil
        needle = f"--port {PORT}"
        for p in psutil.process_iter(["name", "cmdline"]):
            if (p.info.get("name") or "").lower() != "llama-server.exe":
                continue
            cmd = " ".join(p.info.get("cmdline") or [])
            if needle in cmd:
                p.terminate()
                killed = True
    except Exception:
        pass
    if not killed:
        subprocess.run(
            ["taskkill", "/F", "/IM", "llama-server.exe"],
            capture_output=True, creationflags=0x08000000,
        )
    print("wiki_embed_serve: llama-server остановлен (Hermes закрыт, работа завершена) — VRAM/RAM освобождены")


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс с PID (никогда не бросает; True при неизвестности)."""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return True


def _monitor_acquire_lease() -> bool:
    """Атомарно занять место единственного сторожа (паттерн IndexLock).

    Создаёт PID-файл через ``os.open(O_CREAT|O_EXCL)`` — атомарно, только ОДИН
    процесс создаст файл; остальные получат ``FileExistsError``. Если файл есть,
    но его PID мёртв (осиротевший/краш) — удаляем и пробуем ещё раз.
    Возвращает True — этот процесс стал сторожем (владеет PID-файлом).
    """
    for _ in range(2):
        try:
            fd = os.open(str(MONITOR_PIDFILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, str(os.getpid()).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            if not _monitor_pidfile_stale():
                return False
            try:
                MONITOR_PIDFILE.unlink(missing_ok=True)
            except Exception:
                return False
    return False


def _monitor_pidfile_stale() -> bool:
    """True, если PID-файл существует, но его PID мёртв (можно удалять)."""
    try:
        pid = int(MONITOR_PIDFILE.read_text().strip())
    except Exception:
        return True
    return not _pid_alive(pid)


def _monitor_already_running() -> bool:
    """True, если сторож уже запущен: PID-файл есть и его PID жив."""
    if not MONITOR_PIDFILE.exists():
        return False
    return not _monitor_pidfile_stale()


def _start_monitor() -> None:
    """Запустить долгоживущий сторож как detached (переживает cron и сам Hermes)."""
    py = sys.executable
    args = [py, str(Path(__file__).resolve()), "--monitor"]
    log = HERE / "wiki_embed_serve.log"
    try:
        import datetime
        stamp = datetime.datetime.now().strftime("%F %T")
    except Exception:
        stamp = time.strftime("%F %T")
    with open(log, "a", encoding="utf-8", errors="replace") as lf:
        lf.write(f"\n===== {stamp} monitor launch =====\n")
        subprocess.Popen(
            args,
            cwd=str(HERE),
            stdout=lf,
            stderr=subprocess.STDOUT,
            creationflags=_DETACH,
        )
    print("wiki_embed_serve: сторож (--monitor) запущен")


def _monitor_loop() -> int:
    """Долгоживущий сторож: сам следит за Hermes и выгружает сервер/себя.

    Логика «проверки работы Hermes в самом сервере»:
    - Hermes жив            -> держим llama-server поднятым, спим.
    - Hermes закрыт + работа -> держим (не сломать индексацию), спим.
    - Hermes закрыт + просто  -> останавливаем llama-server и выходим (выгрузились).
    """
    # Единственный экземпляр: атомарный захват PID-файла. Кто первый (O_EXCL) — тот
    # сторож; остальные конкурентные старты завершаются, не порождая дублей.
    if not _monitor_acquire_lease():
        print("wiki_embed_serve: сторож уже работает (lease занят) — пропускаю")
        return 0
    try:
        while True:
            hermes = _hermes_alive()
            listening = _port_is_listening()
            if hermes:
                if not listening:
                    _start_server()
            elif _work_active():
                # Hermes закрыт, но идёт работа — сервер держим, не выгружаем.
                print("wiki_embed_serve[monitor]: Hermes закрыт, но идёт работа с БД — сервер держу")
            else:
                # Hermes закрыт и всё тихо — выгружаем сервер и завершаем сторожа.
                if listening:
                    _stop_server()
                print("wiki_embed_serve[monitor]: Hermes закрыт, работа завершена — выгрузка")
                return 0
            time.sleep(MONITOR_INTERVAL)
    finally:
        try:
            MONITOR_PIDFILE.unlink(missing_ok=True)
        except Exception:
            pass


def main() -> None:
    if "--monitor" in sys.argv:
        sys.exit(_monitor_loop())

    listening = _port_is_listening()
    hermes = _hermes_alive()

    # Hermes жив: сервер должен быть поднят (сторож тоже поднимет, но не медлим).
    if not listening and hermes:
        _start_server()

    # Убедиться, что долгоживущий сторож запущен (он доделает выгрузку при закрытии Hermes).
    if hermes and not _monitor_already_running():
        _start_monitor()

    # Ручной/probe-режим при закрытом Hermes: очищаем осиротевший сервер, если work нет.
    if not hermes and listening and not _work_active() and not _monitor_already_running():
        _stop_server()


if __name__ == "__main__":
    main()
