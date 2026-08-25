"""Тест грациозной остановки экстракции МЕЖДУ батчами чанков.

map_chunk_tags обрабатывает чанки батчами по 4 параллельно. Если запрошена
остановка (``.stop_request``), текущий батч доделывается, следующие пропускаются.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Направить WIKI_PATH в tmp_path (для .stop_request)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    monkeypatch.delenv("WIKI_PATH", raising=False)
    cfg.reload()
    yield


def test_map_stops_between_batches(tmp_path, monkeypatch):
    """С 9 чанками (2.5 батча по 4): после 1-го батча ставим флаг → обработано 4, не 9."""
    from wiki_v2 import extract

    # Мокаем _map_chunk_one, чтобы вернуть непустые теги и вести счётчик обработанных
    calls = {"n": 0}

    def fake_map(title, chunk):
        calls["n"] += 1
        return ["тег"]

    monkeypatch.setattr(extract, "_map_chunk_one", fake_map)

    # После 1-го батча (4 чанка) появляется .stop_request
    import wiki_v2.config as cfg
    monkeypatch.setattr(cfg, "WIKI_PATH", tmp_path)
    real_stop = extract._stop_requested

    def stop_after_4():
        # ставим флаг, когда обработано >= 4 чанков
        if calls["n"] >= 4:
            (tmp_path / ".stop_request").write_text("1", encoding="utf-8")
        return os.path.exists(tmp_path / ".stop_request")

    monkeypatch.setattr(extract, "_stop_requested", stop_after_4)

    chunks = ["c%d" % i for i in range(9)]  # 9 чанков → батчи 4+4+1
    result = extract.map_chunk_tags("test", chunks)

    # Батч 1 (чанки 0-3, n=4) обработан. После него проверка флага: n>=4 → ставим флаг
    # → break. Значит обработано РОВНО 4 чанка (батч 1), дальше не идём.
    assert calls["n"] == 4, f"после 1-го батча должен быть break (4 чанка), got {calls['n']}"
    # первые 4 чанка точно есть
    assert result.get(0) == ["тег"]
    assert result.get(3) == ["тег"]


def test_map_no_stop_processed_all(tmp_path, monkeypatch):
    """Без .stop_request все 9 чанков обработаны."""
    from wiki_v2 import extract

    calls = {"n": 0}

    def fake_map(title, chunk):
        calls["n"] += 1
        return ["тег"]

    monkeypatch.setattr(extract, "_map_chunk_one", fake_map)
    monkeypatch.setattr(extract, "_stop_requested", lambda: False)

    chunks = ["c%d" % i for i in range(9)]
    result = extract.map_chunk_tags("test", chunks)
    assert calls["n"] == 9, f"все чанки должны быть обработаны, got {calls['n']}"
    assert len(result) == 9
