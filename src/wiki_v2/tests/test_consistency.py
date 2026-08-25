# tests/test_consistency.py — этап 1.1: двухфазный коммит, нет сирот (АР-4)
"""Consistency tests for the two-phase commit (stage 1.1, AP-4).

Scenarios:
  a) os.replace fails → DB has content_hash=='PENDING', .tmp file left on
     disk, final .md does NOT exist.
  b) indexer startup finds a PENDING row → cleans up the orphan (file + DB
     row both removed) via cleanup_pending().
  c) successful process_session → DB content_hash == real sha256 (not PENDING),
     final .md exists with correct content.
"""
import hashlib
import os
import sqlite3
import time
from unittest.mock import patch

import numpy as np


def _make_state_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
    """)
    conn.execute("INSERT INTO sessions VALUES ('s1','Тест немотрона',?)",
                 (time.time(),))
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) "
                 "VALUES ('s1','user','Как подключить немотрон?',1)")
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) "
                 "VALUES ('s1','assistant','Проблема в NVIDIA_BASE_URL.',2)")
    conn.commit()
    conn.close()


_GOOD_CONTENT = {
    "summary": "Починили подключение немотрона.",
    "key_topics": ["немотрон", "nvidia"],
    "decisions": ["удалить NVIDIA_BASE_URL"],
    "facts": ["эндпоинт integrate.api.nvidia.com/v1"],
    "links": [],
    "entities": ["nvidia"],
    "concepts": [],
    "quality": "ok",
}


def _setup(tmp_path, monkeypatch):
    """Common test setup: state.db + env vars + reloaded indexer module."""
    _make_state_db(str(tmp_path / "state.db"))
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)
    return idx, wiki


# ─────────────────────────────────────────────────────────────────────────
# (a) crash during os.replace → PENDING row, .tmp present, no final .md
# ─────────────────────────────────────────────────────────────────────────
def test_replace_crash_leaves_pending_and_tmp(tmp_path, monkeypatch):
    """AP-4 (a): мок os.replace падает → в БД content_hash=='PENDING',
    файл .tmp существует, финальной .md-страницы нет."""
    idx, wiki = _setup(tmp_path, monkeypatch)

    fake_vec = np.random.rand(1024).astype(np.float32)

    # Patch os.replace INSIDE atomic module to raise — simulates hard crash
    # right at the rename boundary.
    real_replace = os.replace

    def boom(src, dst, *a, **kw):
        if str(src).endswith(".md.tmp"):
            raise OSError("simulated hard-reboot during os.replace")
        return real_replace(src, dst, *a, **kw)


    with patch("wiki_v2.indexer.extract_content", return_value=_GOOD_CONTENT), \
         patch("wiki_v2.indexer.embed_api_available", return_value=True), \
         patch("wiki_v2.indexer.chat_available", return_value=True), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]), \
         patch("wiki_v2.atomic.os.replace", side_effect=boom):
        # process_session should raise when os.replace fails
        try:
            idx.main(session_id="s1")
        except OSError:
            pass  # expected: the OSError propagates from process_session

    db_path = str(wiki / ".index_v2.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM pages")]
    conn.close()

    assert len(rows) == 1, f"expected 1 PENDING row, got {len(rows)}"
    row = rows[0]
    assert row["content_hash"] == "PENDING", \
        f"expected PENDING hash, got {row['content_hash']!r}"

    # .tmp file should exist (flushed+fsynced, rename failed → left for sweep)
    tmp_files = list(wiki.rglob("*.md.tmp"))
    assert tmp_files, "expected a .md.tmp file left behind after crash"

    # final .md must NOT exist (rename never completed)
    md_files = [p for p in wiki.rglob("*.md") if p.suffix == ".md"]
    assert not md_files, f"expected NO final .md, got {md_files}"


# ─────────────────────────────────────────────────────────────────────────
# (b) startup finds PENDING → cleans up orphan (file + DB row removed)
# ─────────────────────────────────────────────────────────────────────────
def test_startup_cleans_pending_orphans(tmp_path, monkeypatch):
    """AP-4 (b): старт indexer находит PENDING-запись → чистит сироту:
    и файл (если остался), и запись в БД удалены."""
    idx, wiki = _setup(tmp_path, monkeypatch)

    # Manually create a PENDING orphan: a row in the DB pointing at a .tmp
    # file (as if a previous run crashed right at os.replace).
    entities = wiki / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    orphan_md = entities / "orphan-page.md"
    orphan_tmp = entities / "orphan-page.md.tmp"
    orphan_tmp.write_text("# stale orphan content\n", encoding="utf-8")

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    db.upsert_page(
        slug="orphan-page",
        title="Orphan",
        section="entities",
        path=str(orphan_md),
        content_hash="PENDING",
        summary="stale",
        quality="ok",
    )
    db.close()

    assert orphan_tmp.exists(), "precondition: .tmp present"

    # Run main() — cleanup_pending() should fire at startup and remove orphan.
    fake_vec = np.random.rand(1024).astype(np.float32)
    with patch("wiki_v2.indexer.extract_content", return_value=_GOOD_CONTENT), \
         patch("wiki_v2.indexer.embed_api_available", return_value=True), \
         patch("wiki_v2.indexer.chat_available", return_value=True), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()  # no sessions to index (s1 already «completed» → resolved)
        # NOTE: _resolve_sessions will skip s1 (already indexed? no — but
        # since we never marked it indexed, main() will try to process it.
        # To keep the test focused on cleanup only, mark s1 as indexed.)

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    pending_after = db.get_pending_pages()
    all_after = db.all_pages()
    db.close()

    assert pending_after == [], "PENDING orphans should be removed"
    assert all(p["slug"] != "orphan-page" for p in all_after), \
        "orphan-page row must be deleted"
    assert not orphan_tmp.exists(), ".tmp must be removed by cleanup"
    assert not orphan_md.exists(), "orphan .md must be removed if it existed"


def test_startup_recovers_valid_md_when_pending(tmp_path, monkeypatch):
    """Variant: PENDING row where the final .md DID get written (process died
    between os.replace and update_page_hash).  cleanup_pending() must RECOVER
    the page (finalize the hash) — NOT delete valid content (фикс 🔴-бага
    ревью: потеря валидного контента)."""
    idx, wiki = _setup(tmp_path, monkeypatch)

    entities = wiki / "entities"
    entities.mkdir(parents=True, exist_ok=True)
    orphan_md = entities / "orphan.md"
    orphan_md.write_text("# orphan\n", encoding="utf-8")

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    db.upsert_page(
        slug="orphan", title="Orphan", section="entities",
        path=str(orphan_md), content_hash="PENDING",
    )
    # Mark s1 as indexed with the CORRECT content hash so the background
    # _resolve_sessions sees it as unchanged (hash-match) and skips it —
    # leaving main() to only run cleanup_pending().
    s1_hash = idx.session_content_hash("s1")
    db.mark_session_indexed("s1", page_slug="", content_hash=s1_hash)
    db.close()

    # Фоновая индексация (без session_id): s1 помечена индексированной → 
    # _resolve_sessions пропустит её → main() только почистит PENDING.
    with patch("wiki_v2.indexer.extract_content", return_value=_GOOD_CONTENT), \
         patch("wiki_v2.indexer.embed_api_available", return_value=True), \
         patch("wiki_v2.indexer.chat_available", return_value=True), \
         patch("wiki_v2.indexer.embed",
               return_value=[np.zeros(1024, dtype=np.float32)]):
        idx.main()

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    after = db.all_pages()
    row = db.get_page("orphan")
    db.close()
    # Контент НЕ удаляется: запись восстановлена с реальным хэшем
    assert orphan_md.exists(), "valid .md must be KEPT (recovered)"
    assert row is not None, "PENDING page must be finalized, not deleted"
    assert row["content_hash"] != "PENDING", "hash must be finalized"
    # Хэш должен соответствовать РЕАЛЬНОМУ содержимому файла (как его читает
    # cleanup_pending), а не зашитой строке — переводы строк могут отличаться.
    with open(orphan_md, encoding="utf-8", newline="") as f:
        actual_content = f.read()
    assert row["content_hash"] == hashlib.sha256(actual_content.encode()).hexdigest()[:16], \
        "hash must match the file content"
    assert len(after) == 1, "no other pages created"


# ─────────────────────────────────────────────────────────────────────────
# (c) successful write → content_hash == real sha256 (not PENDING)
# ─────────────────────────────────────────────────────────────────────────
def test_successful_write_finalizes_hash(tmp_path, monkeypatch):
    """AP-4 (c): успешная запись → после process_session content_hash == реальный
    sha256 от содержимого .md (не PENDING)."""
    idx, wiki = _setup(tmp_path, monkeypatch)

    fake_vec = np.random.rand(1024).astype(np.float32)
    with patch("wiki_v2.indexer.extract_content", return_value=_GOOD_CONTENT), \
         patch("wiki_v2.indexer.embed_api_available", return_value=True), \
         patch("wiki_v2.indexer.chat_available", return_value=True), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main(session_id="s1")

    md_files = list(wiki.rglob("*.md"))
    assert len(md_files) == 1, f"expected 1 .md, got {len(md_files)}"
    md_path = md_files[0]
    md_text = md_path.read_text(encoding="utf-8")
    real_hash = hashlib.sha256(md_text.encode()).hexdigest()[:16]

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    pages = db.all_pages()
    db.close()

    assert len(pages) == 1
    row = pages[0]
    assert row["content_hash"] != "PENDING", \
        "hash must NOT be PENDING after success"
    assert row["content_hash"] == real_hash, \
        f"DB hash {row['content_hash']!r} != real {real_hash!r}"
    # Sanity: page content contains the summary text
    assert "немотрон" in md_text.lower()
    # And no stale .tmp after success
    assert not list(wiki.rglob("*.md.tmp")), "no .tmp should remain on success"


# ─────────────────────────────────────────────────────────────────────────
# (d) atomic_write itself: flush+fsync+os.replace semantics
# ─────────────────────────────────────────────────────────────────────────
def test_embed_failure_still_marks_session(tmp_path, monkeypatch):
    """🔴-фикс ревью #3: если embed падает (NVIDIA лимиты), страница уже
    сохранена + сессия помечена обработанной → при перезапуске НЕТ дублей.
    Раньше: embed бросал → mark_session_indexed не вызывался → сессия
    индексировалась снова (дубликаты)."""
    idx, wiki = _setup(tmp_path, monkeypatch)

    # embed_text_for_page бросает исключение (имитация лимита NVIDIA)
    with patch("wiki_v2.indexer.extract_content", return_value=_GOOD_CONTENT), \
         patch("wiki_v2.indexer.embed_api_available", return_value=True), \
         patch("wiki_v2.indexer.chat_available", return_value=True), \
         patch("wiki_v2.indexer.embed_text_for_page",
               side_effect=RuntimeError("429 Too Many Requests")):
        processed = idx.main(session_id="s1")

    assert processed == 1, f"session must be processed despite embed failure, got {processed}"

    # Страница создана, хэш финализирован (не PENDING)
    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    pages = db.all_pages()
    is_idx = db.is_session_indexed("s1")
    db.close()
    assert len(pages) == 1, f"page must exist, got {len(pages)}"
    assert pages[0]["content_hash"] != "PENDING", "hash must be finalized"
    assert is_idx, "session must be marked indexed even when embed failed"

    # Второй запуск — сессия НЕ переиндексируется (нет дублей)
    with patch("wiki_v2.indexer.extract_content", return_value=_GOOD_CONTENT), \
         patch("wiki_v2.indexer.embed_api_available", return_value=True), \
         patch("wiki_v2.indexer.chat_available", return_value=True), \
         patch("wiki_v2.indexer.embed_text_for_page",
               side_effect=RuntimeError("429 Too Many Requests")):
        idx.main(session_id="s1")
    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    pages_after = db.all_pages()
    db.close()
    assert len(pages_after) == 1, f"no duplicates allowed, got {len(pages_after)}"


def test_atomic_write_normal_roundtrip(tmp_path):
    """Sanity: atomic_write writes full content and the tmp is gone after."""
    from wiki_v2.atomic import atomic_write
    target = str(tmp_path / "entity" / "page.md")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    payload = "# hello\n" * 500
    with atomic_write(target) as f:
        f.write(payload)
    assert os.path.exists(target)
    with open(target, encoding="utf-8") as f:
        assert f.read() == payload
    assert not os.path.exists(target + ".tmp"), "tmp must be gone on success"


def test_atomic_write_on_commit_callback(tmp_path):
    """on_commit fires AFTER os.replace, so inside it the target exists."""
    from wiki_v2.atomic import atomic_write
    target = str(tmp_path / "p.md")
    fired = {"ok": False, "exists_during": False}

    def on_commit():
        fired["ok"] = True
        fired["exists_during"] = os.path.exists(target)

    with atomic_write(target, on_commit=on_commit) as f:
        f.write("data")
    assert fired["ok"]
    assert fired["exists_during"], "on_commit must run after os.replace"


def test_atomic_write_yield_exception_removes_tmp(tmp_path):
    """Exception during the write body → tmp removed, target not created."""
    from wiki_v2.atomic import atomic_write
    target = str(tmp_path / "p.md")
    try:
        with atomic_write(target) as f:
            f.write("partial")
            raise ValueError("boom during write")
    except ValueError:
        pass
    assert not os.path.exists(target), "target must NOT exist on write failure"
    assert not os.path.exists(target + ".tmp"), \
        "tmp should be removed on write-phase failure"


def test_atomic_write_replace_failure_leaves_tmp(tmp_path):
    """Exception during os.replace → tmp LEFT on disk (valid fsynced data),
    target NOT created. cleanup_pending() can later reclaim it."""
    from wiki_v2.atomic import atomic_write
    target = str(tmp_path / "p.md")
    real_replace = os.replace

    def boom(src, dst, *a, **kw):
        raise OSError("simulated crash at rename")

    with patch("wiki_v2.atomic.os.replace", side_effect=boom):
        try:
            with atomic_write(target) as f:
                f.write("fully-written content")
        except OSError:
            pass

    assert not os.path.exists(target), "target must NOT exist after failed replace"
    assert os.path.exists(target + ".tmp"), \
        "tmp must remain after os.replace failure (hard-reboot boundary)"
    with open(target + ".tmp", encoding="utf-8") as f:
        assert f.read() == "fully-written content"
