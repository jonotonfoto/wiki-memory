# tests/test_search.py
import numpy as np
from unittest.mock import patch
from wiki_v2.index_db import IndexDB
import wiki_v2.search as search_mod


def _seed_db(path):
    db = IndexDB(path)
    db.upsert_page("nemotron-fix", "Фикс немотрона", "entities", "/x.md",
                   "h1", summary="Починили NVIDIA_BASE_URL и алиасы")
    db.upsert_page("oil", "Цены на нефть", "entities", "/y.md",
                   "h2", summary="Нефть Brent за неделю")
    db.set_embedding("nemotron-fix", np.array([1.0] + [0.0] * 1023, dtype=np.float32))
    db.set_embedding("oil", np.array([0.0, 1.0] + [0.0] * 1022, dtype=np.float32))
    db.close()


def test_keyword_hits(tmp_path):
    _seed_db(str(tmp_path / "i.db"))
    db = IndexDB(str(tmp_path / "i.db"))
    pages = list(db.all_pages())
    hits = search_mod.keyword_hits("немотрон nvidia", pages)
    db.close()
    assert hits[0][0] == "nemotron-fix"


def test_search_semantic_wins(tmp_path, monkeypatch):
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    # query vector close to nemotron-fix (длина > 15 символов)
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)
    with patch("wiki_v2.search.embed", return_value=[q]):
        hits, pages = search_mod.search("как починить немотрон на сервере")
    assert hits[0][0] == "nemotron-fix"
    assert hits[0][2] == "semantic"


def test_search_keyword_fallback_when_embed_fails(tmp_path, monkeypatch):
    _seed_db(str(tmp_path / "i.db"))
    monkeypatch.setattr(search_mod, "INDEX_DB", str(tmp_path / "i.db"))
    with patch("wiki_v2.search.embed", return_value=None):
        hits, _ = search_mod.search("цены на нефть brent за неделю")
    assert hits[0][0] == "oil"
    assert hits[0][2] == "keyword"
