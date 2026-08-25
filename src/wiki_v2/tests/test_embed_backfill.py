"""Tests for wiki_v2.embed_backfill — догонка эмбеддингов для страниц с vecs=0."""
import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure the package is importable from live/sandbox root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wiki_v2.config as cfg
from wiki_v2.embed_backfill import (
    embed_one,
    find_pages_without_embeddings,
)
from wiki_v2.index_db import IndexDB


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME and WIKI_PATH into tmp_path (isolation)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WIKI_PATH", str(tmp_path / "wiki"))
    monkeypatch.setenv("WIKI_EMBED_BACKEND", "lmstudio")
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    cfg.reload()
    yield


@pytest.fixture
def db(tmp_path):
    """Создать тестовую БД с одной страницей без эмбеддингов."""
    dbfile = str(tmp_path / "wiki" / ".index_v2.db")
    dbfile_dir = str(tmp_path / "wiki")
    from pathlib import Path as P
    P(dbfile_dir).mkdir(parents=True, exist_ok=True)
    db = IndexDB(dbfile)
    # страница с текстом, но БЕЗ эмбеддингов
    db.upsert_page(
        slug="page-a",
        title="Some page title",
        section="entities",
        path=str(tmp_path / "page-a.md"),
        content_hash="abc",
        summary="A summary text for the page.",
        quality="ok",
    )
    return db


def test_find_pages_without_embeddings_returns_missing(db):
    """Страница без title/summary-эмбеддинга должна попасть в список."""
    missing = find_pages_without_embeddings(db)
    slugs = {p["slug"] for p in missing}
    assert "page-a" in slugs


def test_find_pages_empty_when_embedded(db):
    """После записи эмбеддинга страница больше не считается «без эмбеддингов»."""
    vec = np.zeros(cfg.EMBED_DIM, dtype=np.float32)
    db.set_embedding("page-a", vec, kind="title")
    db.set_embedding("page-a", vec, kind="summary")
    missing = find_pages_without_embeddings(db)
    assert "page-a" not in {p["slug"] for p in missing}


def test_embed_one_writes_vectors(db, monkeypatch):
    """embed_one должен записать векторы (title/summary) через set_embedding."""
    import wiki_v2.embed_backfill as eb
    dim = cfg.EMBED_DIM
    fake_vec = [0.1] * dim

    # мокаем сетевой вызов embed: возвращаем 2 вектора (title+summary)
    monkeypatch.setattr(eb, "embed", lambda texts, **kw: [fake_vec, fake_vec])
    # ключевых тем нет -> только title+summary
    monkeypatch.setattr(eb, "_read_key_topics", lambda slug: [])

    page = {"slug": "page-a", "title": "Some page title",
            "summary": "A summary text for the page.", "path": ""}
    ok, msg = embed_one(db, page)
    assert ok is True

    kinds = {r[0] for r in db.conn.execute(
        "SELECT kind FROM embeddings WHERE slug='page-a'")}
    assert "title" in kinds
    assert "summary" in kinds


def test_embed_one_failopen_when_no_text(db, monkeypatch):
    """Если текста для эмбеддинга нет — fail-open: (False, сообщение), не исключение."""
    import wiki_v2.embed_backfill as eb
    page = {"slug": "page-a", "title": "", "summary": "",
            "path": str(Path("/nonexistent"))}
    monkeypatch.setattr(eb, "_read_key_topics", lambda slug: [])
    ok, msg = embed_one(db, page)
    assert ok is False
    assert msg  # есть причина пропуска


def test_embed_one_failopen_when_backend_none(db, monkeypatch):
    """Если эмбеддер вернул None (LM Studio недоступен) — fail-open, не исключение."""
    import wiki_v2.embed_backfill as eb
    monkeypatch.setattr(eb, "embed", lambda texts, **kw: None)
    monkeypatch.setattr(eb, "_read_key_topics", lambda slug: [])
    page = {"slug": "page-a", "title": "Title", "summary": "Summ", "path": ""}
    ok, msg = embed_one(db, page)
    assert ok is False
