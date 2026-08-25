# tests/test_oversized_registry.py
"""Этап 2: дедуп oversized-сессий через реестр (indexer `_log_oversized`).

Проверяем, что одна и та же «слишком большая» сессия не логируется повторно
при каждом фоновом прогоне: реестр (JSON) запоминает session_id, а сам
`_log_oversized` является страховкой от дублей на любом пути вызова.
"""
import json


def _fresh_indexer(tmp_path, monkeypatch):
    """Направить WIKI_PATH/STATE_DB в tmp и перезагрузить модуль indexer."""
    wiki = tmp_path / "wiki"
    wiki.mkdir(exist_ok=True)
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))
    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg

    _cfg.reload()
    importlib.reload(idx)
    return idx


def _log_lines(idx):
    with open(idx.OVERSIZED_LOG, encoding="utf-8") as f:
        return [l for l in f if l.strip()]


def test_second_run_no_repeat_log(tmp_path, monkeypatch):
    """Одна oversized-сессия логируется один раз, хоть бы прогонов было много."""
    idx = _fresh_indexer(tmp_path, monkeypatch)
    for _ in range(3):  # три «фоновых прогона» видят одну и ту же сессию
        idx._log_oversized("s_mastodon", 5000)
    assert len(_log_lines(idx)) == 1
    assert "s_mastodon" in _log_lines(idx)[0]
    # реестр запомнил сессию
    assert idx._is_oversized_known("s_mastodon") is True


def test_registry_persists_across_reload(tmp_path, monkeypatch):
    """Реестр переживает перезапуск модуля (лежит на диске, не в памяти)."""
    idx = _fresh_indexer(tmp_path, monkeypatch)
    idx._log_oversized("s_1", 5000)
    # «перезапуск»: свежий import читает реестр с диска
    idx2 = _fresh_indexer(tmp_path, monkeypatch)
    assert idx2._is_oversized_known("s_1") is True
    assert idx2._is_oversized_known("s_never") is False


def test_log_oversized_dedup_direct_call(tmp_path, monkeypatch):
    """Страховка внутри _log_oversized: прямой повторный вызов не пишет дубль."""
    idx = _fresh_indexer(tmp_path, monkeypatch)
    idx._log_oversized("s_x", 3000)
    idx._log_oversized("s_x", 3000)  # напрямую, без _resolve_sessions
    assert len(_log_lines(idx)) == 1


def test_fail_open_corrupt_registry(tmp_path, monkeypatch):
    """Битый JSON реестра не роняет индексатор (fail-open → считаем неизвестной)."""
    idx = _fresh_indexer(tmp_path, monkeypatch)
    with open(idx.OVERSIZED_REGISTRY, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    # не падает, сессия логируется
    idx._log_oversized("s_ok", 2500)
    assert len(_log_lines(idx)) == 1


def test_registry_contains_iso_timestamp(tmp_path, monkeypatch):
    """Реестр хранит session_id -> ts (для анализа когда занесена)."""
    idx = _fresh_indexer(tmp_path, monkeypatch)
    idx._log_oversized("s_ts", 4000)
    reg = json.load(open(idx.OVERSIZED_REGISTRY, encoding="utf-8"))
    assert "s_ts" in reg
    assert reg["s_ts"]  # непустой timestamp
