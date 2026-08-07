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

DEFAULT_MAX_AGE = int(os.environ.get("WIKI_LOCK_MAX_AGE", "900"))  # 15 мин: протухший лок от убитого процесса


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

    def _clean_stale(self):
        """Если файл-лок существует, но старше max_age — удаляем (сбой процесса)."""
        if not os.path.exists(self.path):
            return
        try:
            age = time.time() - os.path.getmtime(self.path)
            if age > self.max_age:
                os.unlink(self.path)
        except OSError:
            pass
