"""Тесты фикса кнопки «Стоп» в дашборде.

Проверяют, что stop_extraction останавливает ВНЕШНИЙ процесс (запущенный не
через start_extraction, а cron/вручную), читая PID из .index.lock.
И что extraction_status считает running=True только если PID в локе жив.
"""

import pytest


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Направить WIKI_PATH в tmp (чтобы .index.lock писался туда)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    monkeypatch.delenv("WIKI_PATH", raising=False)
    cfg.reload()
    yield


def _write_lock(tmp_path, pid):
    lock = tmp_path / ".index.lock"
    lock.write_text(str(pid), encoding="utf-8")
    return lock


def test_stop_extraction_external_process(tmp_path, monkeypatch):
    """stop_extraction останавливает внешний процесс через PID из .index.lock."""
    from wiki_v2 import dashboard_control as dc

    # Спавним процесс, который сам завершится через 3 сек и удалит lock (как indexer).
    # stop_extraction увидит исчезновение lock и вернёт ok (не ждёт 120с).
    import subprocess
    import sys
    lock = str(tmp_path / ".index.lock")
    code = (
        "import time,os; time.sleep(3); "
        f"os.remove({lock!r}) if os.path.exists({lock!r}) else None"
    )
    child = subprocess.Popen([sys.executable, "-c", code])
    try:
        pid = child.pid
        _write_lock(tmp_path, pid)

        import wiki_v2.config as cfg
        monkeypatch.setattr(cfg, "WIKI_PATH", tmp_path)

        # stop_extraction должна найти PID по lock и дождаться, пока лок исчезнет
        result = dc.stop_extraction()
        assert result.get("ok") is True, f"expected ok, got {result}"
        child.wait(timeout=15)
    finally:
        if child.poll() is None:
            child.kill()
        try:
            (tmp_path / ".index.lock").unlink(missing_ok=True)
        except Exception:
            pass


def test_stop_extraction_not_running(tmp_path, monkeypatch):
    """stop_extraction возвращает not_running, если нет живого процесса и нет lock."""
    import wiki_v2.config as cfg
    from wiki_v2 import dashboard_control as dc
    monkeypatch.setattr(cfg, "WIKI_PATH", tmp_path)

    result = dc.stop_extraction()
    assert result.get("error") == "not_running", f"expected not_running, got {result}"


def test_extraction_status_dead_pid(tmp_path, monkeypatch):
    """extraction_status: мёртвый PID в lock не считается running."""
    import wiki_v2.config as cfg
    from wiki_v2 import dashboard_control as dc
    monkeypatch.setattr(cfg, "WIKI_PATH", tmp_path)

    # Мёртвый PID (999999 — почти наверняка не существует)
    _write_lock(tmp_path, 999999)

    status = dc.extraction_status()
    assert status["running"] is False, f"мёртвый PID не должен считаться running: {status}"


def test_extraction_status_alive_pid(tmp_path, monkeypatch):
    """extraction_status: живой PID в lock считается running."""
    import wiki_v2.config as cfg
    from wiki_v2 import dashboard_control as dc
    monkeypatch.setattr(cfg, "WIKI_PATH", tmp_path)

    import subprocess
    import sys
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    try:
        _write_lock(tmp_path, child.pid)
        status = dc.extraction_status()
        assert status["running"] is True, f"живой PID должен считаться running: {status}"
    finally:
        child.kill()
