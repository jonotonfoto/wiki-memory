"""Подэтап 4д: IndexDB.get_page_chunk_embeddings(slug) — точечный отбор чанков одной страницы.

Проверяем:
  - возвращает ТОЛЬКО kind like 'page_chunk:%' конкретного slug (не других страниц,
    не сессионных chunk:N, не title)
  - векторы декодируются в np.ndarray с config.EMBED_DIM
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wiki_v2.config as cfg
from wiki_v2.index_db import IndexDB


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WIKI_PATH", str(tmp_path / "wiki"))
    monkeypatch.setenv("WIKI_EMBED_BACKEND", "lmstudio")
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    cfg.reload()
    yield


@pytest.fixture
def db(tmp_path):
    dbfile = tmp_path / "wiki" / ".index_v2.db"
    dbfile.parent.mkdir(parents=True, exist_ok=True)
    return IndexDB(str(dbfile))


def _seed(db):
    dim = cfg.EMBED_DIM
    db.set_embedding("page-a", np.ones(dim, dtype=np.float32), kind="title")
    db.set_embedding("page-a", np.zeros(dim, dtype=np.float32), kind="page_chunk:0")
    db.set_embedding("page-a", np.full(dim, 0.5, dtype=np.float32), kind="page_chunk:1")
    db.set_embedding("page-a", np.full(dim, 0.9, dtype=np.float32), kind="chunk:0")  # сессионный
    db.set_embedding("page-b", np.full(dim, 0.2, dtype=np.float32), kind="page_chunk:0")
    return db


def test_only_own_page_chunks_returned(db):
    _seed(db)
    got = db.get_page_chunk_embeddings("page-a")
    # Фикс 2026-08-24: возвращаются ОБЕ семьи чанков страницы page-a
    # (chunk:N текущая и page_chunk:N легаси); без title, без page-b.
    assert set(got.keys()) == {"chunk:0", "page_chunk:0", "page_chunk:1"}
    assert isinstance(got["page_chunk:0"], np.ndarray)
    assert got["page_chunk:0"].shape == (cfg.EMBED_DIM,)


def test_other_page_not_returned(db):
    _seed(db)
    got_b = db.get_page_chunk_embeddings("page-b")
    assert set(got_b.keys()) == {"page_chunk:0"}
    assert "page_a_chunks" not in got_b


def test_empty_when_no_page_chunk(db):
    _seed(db)
    db.set_embedding("page-c", np.zeros(cfg.EMBED_DIM, dtype=np.float32), kind="title")
    assert db.get_page_chunk_embeddings("page-c") == {}
