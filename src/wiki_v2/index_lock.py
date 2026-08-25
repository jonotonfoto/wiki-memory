# index_lock.py
"""Файловый лок для индексации — гарантирует, что только один процесс
индексирует wiki одновременно (защита от конфликтов cron + хук /new).

Кроссплатформенно: msvcrt (Windows) / fcntl (POSIX). Лок атомарный,
`acquire()` возвращает False, если лок уже занят (не блокирует, а пропускает).
Протухшие локи (возраст > max_age) автоматически перехватываются.
"""
import os
import time

try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover
    msvcrt = None

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover
    fcntl = None

DEFAULT_MAX_AGE = int(os.environ.get("WIKI_LOCK_MAX_AGE", "3600"))  # 1 час: протухший лок от убитого процесса


def _pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is alive.

    POSIX: /proc/{pid} exists.
    Windows: OpenProcess + GetLastError (87=dead, 5=access denied but alive).
    Never raises — returns True on unknown (fail-safe).
    """
    try:
        if os.name == "nt":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x0400, False, pid)
            if handle == 0:
                last_error = kernel32.GetLastError()
                return False if last_error == 87 else True
            kernel32.CloseHandle(handle)
            return True
        else:
            return os.path.exists(f"/proc/{pid}")
    except Exception:
        return True


class IndexLock:
    def __init__(self, path: str, max_age: int = DEFAULT_MAX_AGE):
        self.path = path
        self.max_age = max_age
        self._fd = None

    def acquire(self, timeout: float = 0.0) -> bool:
        """Захватить лок. True — успех, False — занят (не блокирует при timeout=0)."""
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        deadline = time.time() + timeout
        while True:
            self._clean_stale()
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self._fd, str(os.getpid()).encode())
                if fcntl is None:
                    # на Windows: закрываем fd — лок это само существование файла
                    os.close(self._fd)
                    self._fd = None
                else:
                    # на POSIX: держим fd открытым с flock-блокировкой
                    try:
                        fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except OSError:
                        os.close(self._fd)
                        self._fd = None
                        os.unlink(self.path)
                        if time.time() >= deadline:
                            return False
                        time.sleep(0.05)
                        continue
                return True
            except FileExistsError:
                if time.time() >= deadline:
                    return False
                time.sleep(0.05)

    def release(self) -> bool:
        if self._fd is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(self._fd)
            self._fd = None
        if os.path.exists(self.path):
            try:
                os.unlink(self.path)
                return True
            except OSError:
                return False
        return True

    def touch(self) -> None:
        """Refresh mtime of the lock file. No-op if missing, never raises."""
        try:
            os.utime(self.path)
        except OSError:
            pass

    def _clean_stale(self):
        """If lock file exists but is stale (too old OR PID dead), remove it."""
        if not os.path.exists(self.path):
            return
        try:
            age = time.time() - os.path.getmtime(self.path)
            if age > self.max_age:
                os.unlink(self.path)
                return
            # Also check if the owning PID is still alive
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    pid_str = f.read().strip()
                pid = int(pid_str)
                if not _pid_alive(pid):
                    os.unlink(self.path)
            except (ValueError, OSError):
                # PID unreadable — fall back to mtime-only (already checked above)
                pass
        except OSError:
            pass
