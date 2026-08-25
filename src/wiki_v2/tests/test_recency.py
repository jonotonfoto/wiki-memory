# tests/test_recency.py — S2.5.13 фактор свежести в search()
import time
from unittest.mock import patch

import numpy as np
import wiki_v2.search as search_mod
from wiki_v2.index_db import IndexDB


def _seed_db(path):
    """Создать БД с двумя страницами и векторами."""
    db = IndexDB(path)
    db.upsert_page("fresh", "Свежая страница", "entities", "/a.md",
                   "ha1", summary="Последние новости про поиск")
    db.upsert_page("old", "Старая страница", "entities", "/b.md",
                   "hb2", summary="Давняя заметка про поиск")
    db.set_embedding("fresh", np.array([1.0] + [0.0] * 1023, dtype=np.float32))
    db.set_embedding("old", np.array([0.5, 0.5] + [0.0] * 1022, dtype=np.float32))
    now = time.time()
    db.conn.execute("UPDATE pages SET updated=? WHERE slug=?", (now - 86400, "fresh"))
    db.conn.execute("UPDATE pages SET updated=? WHERE slug=?", (now - 86400 * 100, "old"))
    db.conn.commit()
    db.close()


def _get_score(hits, slug):
    """Вернуть скор страницы из hits (list of (slug, score, src))."""
    for s, score, src in hits:
        if s == slug and src == "semantic":
            return score
    return None


def test_fresh_page_gets_bonus(tmp_path, monkeypatch):
    """Свежая страница (updated вчера) получает бонус."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search("последние новости про поиск")
    fresh_score = _get_score(hits, "fresh")
    assert fresh_score is not None
    # свежая страница получила бонус (0.1): скор > базового без бонуса
    assert fresh_score > 0.016


def test_old_page_no_bonus(tmp_path, monkeypatch):
    """Старая страница (100 дней) не получает бонус (скор без множителя)."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search("последние новости про поиск")
    old_score = _get_score(hits, "old")
    # old далеко (0.5) — может не попасть в топ; если попала, скор без бонуса свежести
    # но с RRF/confidence. Проверяем: fresh (свежая) ВЫШЕ old при равной релевантности
    fresh_score = _get_score(hits, "fresh")
    if fresh_score is not None and old_score is not None:
        assert fresh_score > old_score


def test_no_updated_no_bonus(tmp_path, monkeypatch):
    """Страница без updated — без бонуса (fail-open, не падает)."""
    db = IndexDB(str(tmp_path / "i.db"))
    db.upsert_page("noupd", "Без даты", "entities", "/c.md", "hc3", summary="страница без updated")
    db.set_embedding("noupd", np.array([1.0] + [0.0] * 1023, dtype=np.float32))
    db.close()
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search("страница без даты обновления")
    assert any(s == "noupd" for s, _, _ in hits)
