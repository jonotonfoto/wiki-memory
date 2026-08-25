"""Tests for wiki_v2.logging_setup — idempotent setup, rotation, console+file, reset, fail-open."""

from __future__ import annotations

import logging
import sys
from logging import StreamHandler
from pathlib import Path

import pytest

# Ensure the module is importable from the test directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wiki_v2.logging_setup import (
    logger,
    reset_logging,
    setup_logging,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_logging():
    """Reset logging state before and after every test."""
    reset_logging()
    yield
    reset_logging()


# ── Tests ───────────────────────────────────────────────────────────────────


def test_logger_name():
    """1. logger.name == 'wiki_v2'."""
    assert logger.name == "wiki_v2"


def test_idempotent_setup():
    """2. Repeated setup_logging() does NOT duplicate handlers."""
    setup_logging()
    handlers_before = list(logger.handlers)
    setup_logging()
    handlers_after = list(logger.handlers)
    assert handlers_after == handlers_before


def test_file_log_created_and_written(tmp_path, capsys):
    """3. setup_logging(log_file=tmp) creates the file and logger.info writes to it."""
    log_file = tmp_path / "test.log"
    setup_logging(log_file=log_file, level=logging.DEBUG)
    logger.info("pytest-write-test")

    # File must exist and contain the message
    assert log_file.exists(), "Log file was not created"
    content = log_file.read_text(encoding="utf-8")
    assert "pytest-write-test" in content

    # Console handler should also have emitted to stdout
    captured = capsys.readouterr()
    assert "pytest-write-test" in captured.out


def test_rotation_config():
    """4. TimedRotatingFileHandler rotates by hour, keeps 24 backups."""
    tmp_path = Path(__file__).resolve().parent / ".tmp_rotation_test"
    try:
        setup_logging(log_file=tmp_path / "rot.log")
        # Find the TimedRotatingFileHandler (time-based rotation by hour)
        from logging.handlers import TimedRotatingFileHandler
        file_handlers = [h for h in logger.handlers if isinstance(h, TimedRotatingFileHandler)]
        assert len(file_handlers) == 1, "Expected exactly one TimedRotatingFileHandler"
        fh = file_handlers[0]
        assert fh.when == "H", f"when={fh.when}, expected H (hourly)"
        assert fh.interval == 3600, f"interval={fh.interval}, expected 3600 (1 hour in seconds)"
        assert fh.backupCount == 24, f"backupCount={fh.backupCount}, expected 24"
    finally:
        # Cleanup
        import shutil
        if tmp_path.exists():
            shutil.rmtree(tmp_path, ignore_errors=True)


def test_console_handler_present():
    """5. A StreamHandler (console) is installed after setup_logging()."""
    setup_logging()
    console_handlers = [h for h in logger.handlers if isinstance(h, StreamHandler)]
    assert len(console_handlers) >= 1, "Expected at least one StreamHandler"
    # Verify it writes to stdout
    ch = console_handlers[0]
    assert ch.stream == sys.stdout


def test_reset_and_rerun():
    """6. reset_logging() clears handlers; setup_logging works again."""
    setup_logging()
    assert len(logger.handlers) > 0, "Handlers should exist after setup"

    reset_logging()
    assert len(logger.handlers) == 0, "All handlers should be cleared after reset"

    # Re-setup should work cleanly
    setup_logging()
    assert len(logger.handlers) > 0, "Handlers should be restored after re-setup"


def test_fail_open_on_invalid_path():
    """7. setup_logging(log_file=unreachable) does NOT raise."""
    # Use a path that is guaranteed to be unwritable on any system
    impossible_path = "/proc/1/root/dev/null/nonexistent/deeply/nested/path/test.log"
    try:
        setup_logging(log_file=impossible_path)
        # If we get here without exception, the test passes
    except Exception as exc:
        pytest.fail(f"setup_logging raised {type(exc).__name__}: {exc}")

    # Console handler should still be present (fail-open)
    console_handlers = [h for h in logger.handlers if isinstance(h, StreamHandler)]
    assert len(console_handlers) >= 1, "Console handler should survive fail-open"


def test_config_logger_alias():
    """8. config.logger is logging_setup.logger == True."""
    try:
        from wiki_v2.config import logger as config_logger
        assert config_logger is logger, "config.logger must be the same object as logging_setup.logger"
    except ImportError:
        pytest.skip("wiki_v2.config not available (no HERMES_HOME)")
