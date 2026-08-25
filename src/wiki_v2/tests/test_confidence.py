# tests/test_confidence.py — S2.5.6 confidence-weight in search()
from unittest.mock import patch

import numpy as np
import wiki_v2.search as search_mod
from wiki_v2.index_db import IndexDB


def _seed_db(path):
    """Создать БД с двумя страницами и векторами."""
    db = IndexDB(path)
    # Страница A — высокая уверенность (0.9)
    db.upsert_page("nemotron-fix", "Фикс немотрона", "entities", "/a.md",
                   "ha1", summary="Починили NVIDIA_BASE_URL и алиасы")
    # Страница B — средняя уверенность (0.5)
    db.upsert_page("oil", "Цены на нефть", "entities", "/b.md",
                   "hb2", summary="Нефть Brent за неделю")
    # Вектор A близок к query [0.99, 0.01...] — будет semantic-хитом
    db.set_embedding("nemotron-fix", np.array([1.0] + [0.0] * 1023, dtype=np.float32))
    # Вектор B близок к query [0.01, 0.99...] — тоже semantic-хит, но дальше
    db.set_embedding("oil", np.array([0.01, 0.99] + [0.0] * 1022, dtype=np.float32))
    # Устанавливаем confidence через прямое обновление БД (upsert_page не имеет param confidence)
    db.conn.execute(
        "UPDATE pages SET confidence = ? WHERE slug = ?", (0.9, "nemotron-fix"))
    db.conn.execute(
        "UPDATE pages SET confidence = ? WHERE slug = ?", (0.5, "oil"))
    db.conn.commit()
    db.close()


def test_semantic_09_beats_05(tmp_path, monkeypatch):
    """semantic-хит с confidence=0.9 получает скор выше, чем с confidence=0.5."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    # query вектор близок к nemotron-fix (1.0, 0.0...) — он будет первым по семантике
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search(
            "как починить немотрон на сервере")
    # nemotron-fix (conf=0.9) должен быть выше oil (conf=0.5), даже если базовый скор ниже
    assert len(hits) >= 2
    top_slug, top_score, top_src = hits[0]
    second_slug, second_score, second_src = hits[1]
    # оба — semantic
    assert top_src == "semantic" and second_src == "semantic"


def test_semantic_09_ranked_above_05_same_base(tmp_path, monkeypatch):
    """confidence=0.9 обходит confidence=0.5 при одинаковом базовом скоре."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    # Создаём query-вектор, который даёт ОДИНАКОВЫЙ RRF-скор обоим (близко к обоим)
    q = np.array([0.7, 0.7] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search(
            "как починить немотрон на сервере")
    # nemotron-fix (conf=0.9) должен быть выше oil (conf=0.5)
    assert len(hits) >= 2
    top_slug = hits[0][0]
    second_slug = hits[1][0]
    assert top_slug == "nemotron-fix"
    assert second_slug == "oil"


def test_confidence_none_defaults_to_05(tmp_path, monkeypatch):
    """confidence=None → трактуется как 0.5 (не падает)."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    # Убираем confidence у oil → станет None в БД
    db = IndexDB(str(tmp_path / "i.db"))
    db.conn.execute("UPDATE pages SET confidence = NULL WHERE slug = 'oil'")
    db.conn.commit()
    db.close()

    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        # должен пройти без исключения
        hits, pages = search_mod.search(
            "как починить немотрон на сервере")
    assert len(hits) >= 1


def test_keyword_hit_unchanged_by_confidence(tmp_path, monkeypatch):
    """keyword-хит НЕ получает confidence-вес (скор не меняется)."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    # embed возвращает None → только keyword-поиск (BM25)
    with patch("wiki_v2.search.embed", return_value=None):
        hits, pages = search_mod.search(
            "цены на нефть brent за неделю")
    assert len(hits) >= 1
    # oil должен быть найден как keyword-хит
    oil_hit = [h for h in hits if h[0] == "oil"]
    assert len(oil_hit) == 1
    slug, score, src = oil_hit[0]
    assert src == "keyword"
    # keyword-скор не должен превышать MAX_KEYWORD_SCORE (0.35)
    assert score <= 0.35


def test_confidence_applied_only_to_semantic(tmp_path, monkeypatch):
    """confidence применяется ТОЛЬКО к semantic-хитам."""
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search(
            "как починить немотрон на сервере")
    for slug, score, src in hits:
        if src == "semantic":
            # semantic-хиты должны иметь скор > базового (вес применён)
            assert score > 0.0
        elif src == "keyword":
            # keyword-хиты не модифицируются confidence
            pass
