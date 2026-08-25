# tests/test_index_lock.py
import os

from wiki_v2.index_lock import IndexLock


def test_acquire_release(tmp_path):
    lock = IndexLock(str(tmp_path / "lock"))
    assert lock.acquire() is True
    assert lock.release() is True


def test_second_acquire_returns_false(tmp_path):
    lock = IndexLock(str(tmp_path / "lock"))
    assert lock.acquire() is True
    other = IndexLock(str(tmp_path / "lock"))
    assert other.acquire() is False
    # после release первый — снова доступен
    assert lock.release() is True
    assert other.acquire() is True
    other.release()


def test_acquire_without_release_cleans_stale(tmp_path):
    # принудительно оставляем «протухший» лок (возраст > max_age)
    lock = IndexLock(str(tmp_path / "lock"))
    assert lock.acquire() is True
    # протухаем файл
    f = lock.path
    old = os.path.getmtime(f) - 10_000
    os.utime(f, (old, old))
    assert IndexLock(str(tmp_path / "lock")).acquire() is True
