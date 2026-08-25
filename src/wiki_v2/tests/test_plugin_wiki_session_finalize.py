# tests/test_plugin_wiki_session_finalize.py
"""Тесты плагина wiki-session-finalize (хук on_session_finalize)."""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

_PLUGIN_PY = (Path(__file__).resolve().parents[3] / "plugins"
              / "wiki-session-finalize" / "__init__.py")


def _load_plugin(monkeypatch, tmp_path):
    """Импортировать плагин, изолировав WIKI_SCRIPTS/PYTHON от реальной ФС."""
    monkeypatch.setenv("WIKI_SCRIPTS", str(tmp_path / "scripts"))
    monkeypatch.setenv("WIKI_PYTHON", "python-test")
    spec = importlib.util.spec_from_file_location("wiki_session_finalize_test", _PLUGIN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class MockCtx:
    def __init__(self):
        self.registered = {}

    def register_hook(self, name, fn):
        self.registered[name] = fn


def test_registers_hook(tmp_path, monkeypatch):
    mod = _load_plugin(monkeypatch, tmp_path)
    ctx = MockCtx()
    mod.register(ctx)
    assert "on_session_finalize" in ctx.registered
    assert ctx.registered["on_session_finalize"] is mod.on_session_finalize


def test_on_finalize_launches_indexer(tmp_path, monkeypatch):
    mod = _load_plugin(monkeypatch, tmp_path)
    with patch("subprocess.Popen") as mock_popen:
        mod.on_session_finalize(session_id="sess-abc", platform="desktop")
    # Индексатор запускается фоном (Popen). До него плагин может вызвать
    # _ensure_oss_model_loaded() (её subprocess.run тоже пользуется Popen),
    # поэтому проверяем ПО КОМАНДЕ, а не по счётчику глобальных вызовов.
    cmds = [c.args[0] for c in mock_popen.call_args_list if c.args]
    assert any(
        isinstance(cmd, list) and "--session" in cmd and "sess-abc" in cmd
        for cmd in cmds
    )


def test_on_finalize_no_session_skips(tmp_path, monkeypatch):
    mod = _load_plugin(monkeypatch, tmp_path)
    with patch("subprocess.Popen") as mock_popen:
        mod.on_session_finalize(session_id=None, platform="desktop")
    mock_popen.assert_not_called()


def test_on_finalize_empty_session_skips(tmp_path, monkeypatch):
    mod = _load_plugin(monkeypatch, tmp_path)
    with patch("subprocess.Popen") as mock_popen:
        mod.on_session_finalize(session_id="", platform="desktop")
    mock_popen.assert_not_called()


def test_on_finalize_never_raises(tmp_path, monkeypatch):
    """Fail-open: ошибка внутри не бросается наружу."""
    mod = _load_plugin(monkeypatch, tmp_path)
    with patch("subprocess.Popen", side_effect=OSError("boom")):
        mod.on_session_finalize(session_id="sess-x", platform="desktop")
