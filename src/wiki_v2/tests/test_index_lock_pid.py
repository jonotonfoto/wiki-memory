# tests/test_index_lock_pid.py — этап 1.3: PID-check + touch() + max_age=3600 (АР-4, без heartbeat-потока)
"""RED phase tests for stage 1.3 (S1.3): file lock with PID-check.

Target API (post-impl):
  class IndexLock:
      DEFAULT_MAX_AGE = 3600          # было 900 (WIKI_LOCK_MAX_AGE)
      def __init__(self, path, max_age=DEFAULT_MAX_AGE)
      def acquire(self, timeout=0.0) -> bool   # + PID-check: читает PID из файла, проверяет живость
      def touch(self) -> None                 # os.utime(path) — refresh mtime (вызывать в цикле индексации)
      def release(self) -> bool
      # PID-check: POSIX -> /proc/{pid}; Windows -> tasklist/ctypes. НИКОГДА os.kill на Windows.

Контракт (из spec-phase-1.md, секция S1.3):
  1. Живой процесс индексирует > max_age -> touch() обновляет mtime -> лок НЕ удаляется.
  2. PID умер, но mtime свежий -> PID-check находит мёртвый процесс -> лок можно перехватить.
  3. Windows без psutil -> fallback: только max_age.
  4. os.kill(pid,0) на Windows -> ЗАПРЕЩЕНО (шлёт CTRL_C_EVENT).

Все тесты ПРОПУЩЕНЫ (skip) — это RED. Локальная реализация (index_lock.py) пока НЕ
имеет touch()/PID-check/max_age=3600. После реализации: убрать @pytest.mark.skip ->
тесты должны стать зелёными без правки тел (они описывают целевое поведение).
"""
import os
import time
from unittest.mock import patch

from wiki_v2.index_lock import DEFAULT_MAX_AGE, IndexLock

RED_REASON = "RED 1.3: PID-check/touch not implemented yet"


class _FakeClock:
    """Управляемый clock для time.time(): без реального ожидания 20 минут."""

    def __init__(self, start: float):
        self.now = start

    def __call__(self, *args, **kwargs) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_busy_lock_with_live_pid_not_reclaimed_after_20min(tmp_path):
    """Сценарий 1: занятый лок с обновляемым mtime (touch) -> второй acquire() False
    даже спустя 20 мин (не удаляет лок живого процесса).

    Процесс A держит лок и каждые 5с вызывает touch(). Виртуальное время +20 мин.
    Процесс B (other) пытается захватить -> False, т.к. PID жив (PID-check), а mtime свеж.
    """
    lock_path = str(tmp_path / "lock")
    start = time.time()
    clock = _FakeClock(start)

    lock = IndexLock(lock_path, max_age=3600)
    assert lock.acquire() is True
    live_pid = os.getpid()

    # Индексация 20 минут: каждые 5с touch()-им, mtime держим свежим.
    with patch("wiki_v2.index_lock.time.time", side_effect=clock), \
            patch("wiki_v2.index_lock.os.path.getmtime", side_effect=clock), \
            patch("wiki_v2.index_lock.os.utime", return_value=None), \
            patch("wiki_v2.index_lock._pid_alive", return_value=True):
        for _ in range(240):  # 240 * 5s = 20 min
            clock.advance(5)
            lock.touch()

    # Параллельный процесс B пытается захватить лок. Живой PID + свежий mtime -> False.
    other = IndexLock(lock_path, max_age=3600)
    with patch("wiki_v2.index_lock.time.time", side_effect=clock), \
            patch("wiki_v2.index_lock.os.path.getmtime", side_effect=clock), \
            patch("wiki_v2.index_lock._pid_alive", return_value=True):
        assert other.acquire() is False

    # Лок живого процесса НЕ удаляется.
    assert os.path.exists(lock_path)
    lock.release()


def test_dead_pid_reclaims_lock_with_fresh_mtime(tmp_path):
    """Сценарий 2: PID в lock-файле мёртв -> acquire() True (перехват протухшего),
    даже если mtime свежий (mtime-only проверка бы удержала бы лок)."""
    lock_path = str(tmp_path / "lock")
    # lock-файл с "мёртвым" PID и свежим mtime
    with open(lock_path, "w") as f:
        f.write(str(999_999))
    fresh = time.time()
    os.utime(lock_path, (fresh, fresh))

    lock = IndexLock(lock_path, max_age=3600)
    with patch("wiki_v2.index_lock._pid_alive", return_value=False):
        assert lock.acquire() is True
    lock.release()


def test_touch_refreshes_mtime_and_keeps_lock(tmp_path):
    """Сценарий 3: touch() обновляет mtime — lock остаётся, возраст сбрасывается."""
    lock_path = str(tmp_path / "lock")
    lock = IndexLock(lock_path, max_age=3600)
    assert lock.acquire() is True

    m0 = os.path.getmtime(lock_path)

    # имитируем проход времени, затем touch
    clock = _FakeClock(time.time() + 300)
    with patch("wiki_v2.index_lock.time.time", side_effect=clock), \
            patch("wiki_v2.index_lock.os.utime", return_value=None) as mock_utime:
        lock.touch()
        mock_utime.assert_called_once()

    # возраст сброшен: getmtime теперь ~ clock.now (свежий), lock жив
    with patch("wiki_v2.index_lock.os.path.getmtime", return_value=clock.now):
        age = time.time() - os.path.getmtime(lock_path)
    assert age < lock.max_age
    assert os.path.exists(lock_path)
    lock.release()


def test_default_max_age_is_one_hour():
    """Сценарий 4: max_age=3600 применяется по умолчанию (НЕ 900)."""
    assert DEFAULT_MAX_AGE == 3600, "DEFAULT_MAX_AGE должно быть 3600 (1 час), а не 900"
