"""Tests for wiki_v2.page_chunk_backfill — догонка page_chunk:N (подэтап 4в.4)."""
import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure the package is importable from live/sandbox root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import wiki_v2.config as cfg
from wiki_v2.index_db import IndexDB
from wiki_v2.page_chunk_backfill import backfill_one, find_pages_missing_page_chunk, main


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Point HERMES_HOME and WIKI_PATH into tmp_path (isolation)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("WIKI_PATH", str(tmp_path / "wiki"))
    monkeypatch.setenv("WIKI_EMBED_BACKEND", "lmstudio")
    monkeypatch.delenv("HERMES_STATE_DB", raising=False)
    cfg.reload()
    yield


def _mk_page(db, slug, md_text):
    """Завести страницу в БД + при необходимости записать md-файл. Вернуть dict."""
    dbfile = db.conn
    # путь рядом с тестовой БД
    from pathlib import Path as P
    wiki_dir = P(cfg.WIKI_PATH)
    wiki_dir.mkdir(parents=True, exist_ok=True)
    path = str(wiki_dir / f"{slug}.md")
    if md_text is not None:
        P(path).write_text(md_text, encoding="utf-8")
    db.upsert_page(
        slug=slug, title=f"Page {slug}", section="entities",
        path=path, content_hash="abc",
        summary="Summ", quality="ok",
    )
    return {"slug": slug, "title": f"Page {slug}", "path": path}


@pytest.fixture
def db(tmp_path):
    dbfile = tmp_path / "wiki" / ".index_v2.db"
    dbfile.parent.mkdir(parents=True, exist_ok=True)
    return IndexDB(str(dbfile))


def _long_md():
    return ("# Заголовок\n\n" + "Слово. " * 400)


def test_finds_pages_missing_page_chunk(db):
    """Страница без page_chunk — в списке; с page_chunk:0 — нет."""
    p_with = _mk_page(db, "with-chunk", _long_md())
    p_without = _mk_page(db, "without-chunk", _long_md())
    vec = np.zeros(cfg.EMBED_DIM, dtype=np.float32)
    db.set_embedding(p_with["slug"], vec, kind="page_chunk:0")

    missing = {p["slug"] for p in find_pages_missing_page_chunk(db)}
    assert "with-chunk" not in missing
    assert "without-chunk" in missing


def test_backfill_one_writes_page_chunk(db, monkeypatch):
    """Мок embed_chunks → в БД появились page_chunk:0..K."""
    import wiki_v2.page_chunk_backfill as pb
    page = _mk_page(db, "page-a", _long_md())
    dim = cfg.EMBED_DIM
    n = 3
    fake = {f"page_chunk:{i}": np.ones(dim, dtype=np.float32) for i in range(n)}
    monkeypatch.setattr(pb, "embed_chunks", lambda title, chunks, **kw: fake)

    ok, msg = backfill_one(db, page)
    assert ok is True
    kinds = {r[0] for r in db.conn.execute(
        "SELECT kind FROM embeddings WHERE slug='page-a' AND kind LIKE 'page_chunk:%'")}
    assert kinds == {f"page_chunk:{i}" for i in range(n)}


def test_backfill_fail_open_lmstudio_down(db, monkeypatch):
    """embed_chunks вернул {} (LM Studio недоступен) → (False,...), БД не записана, не падает."""
    import wiki_v2.page_chunk_backfill as pb
    page = _mk_page(db, "page-a", _long_md())
    monkeypatch.setattr(pb, "embed_chunks", lambda title, chunks, **kw: {})

    ok, msg = backfill_one(db, page)
    assert ok is False
    assert msg
    count = db.conn.execute(
        "SELECT COUNT(*) FROM embeddings WHERE slug='page-a'").fetchone()[0]
    assert count == 0


def test_backfill_fail_open_empty_file(db, monkeypatch):
    """Нет файла/пустой файл → (False,...), не падает."""
    import wiki_v2.page_chunk_backfill as pb
    page = {"slug": "page-a", "title": "T", "path": str(Path("/nonexistent"))}

    ok, msg = backfill_one(db, page)
    assert ok is False
    assert msg


def test_dry_run_does_not_write(db, monkeypatch):
    """main(["--dry-run"]) находит страницы, но НЕ пишет в БД."""
    _mk_page(db, "page-a", _long_md())
    rc = main(["--dry-run"])
    assert rc == 0
    count = db.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
    assert count == 0


def test_limit_applies(db, monkeypatch):
    """main(["--limit","1"]) обрабатывает не больше N страниц."""
    import wiki_v2.page_chunk_backfill as pb
    for i in range(3):
        _mk_page(db, f"page-{i}", _long_md())
    dim = cfg.EMBED_DIM
    monkeypatch.setattr(pb, "embed_chunks",
                        lambda title, chunks, **kw: {"page_chunk:0": np.ones(dim, dtype=np.float32)})
    rc = main(["--limit", "1"])
    assert rc == 0
    slugs = {r[0] for r in db.conn.execute(
        "SELECT DISTINCT slug FROM embeddings WHERE kind LIKE 'page_chunk:%'")}
    assert len(slugs) == 1
