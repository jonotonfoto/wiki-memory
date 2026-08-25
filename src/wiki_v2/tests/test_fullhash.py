# tests/test_fullhash.py — этап 1.2: полный хэш сессии + MIGRATE-1 (АР-1)
"""Tests for full-content session hash + migration registry (stage 1.2, AP-1).

Scenarios:
  a) edit in the MIDDLE of a 10k-char session changes the hash (main bug case)
  b) session_raw_text returns the FULL text (no [:500] truncation)
  c) streaming sha256 == one-shot sha256 (equivalence)
  d) migration: user_version<1 DB → migrate_to(1) adds full_content_hash to
     pages, user_version==1
  e) idempotence: migrate_to(2) twice → no crash, user_version==2
  f) old content_hash column stays, not duplicated
"""
import hashlib
import os
import sqlite3
import time


def _make_state_db(path, big=False, mid_edit=False):
    """Create a state.db with session s1. If big=True — 10k+ char message.
    If mid_edit=True — content differs in the MIDDLE of the big message."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
    """)
    conn.execute("INSERT INTO sessions VALUES ('s1','Тест',?)", (time.time(),))
    if big:
        # ~12k chars: 3 parts of 4k each, joined
        part1 = "начало " + "a" * 4000
        part2 = "середина " + "b" * 4000
        part3 = "конец " + "c" * 4000
        content = part1 + part2 + part3
        if mid_edit:
            # edit in the MIDDLE: change part2 slightly
            content = part1 + "середина ИЗМЕНЕНО " + "b" * 3990 + part3
        conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s1','user',?,1)", (content,))
    else:
        conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s1','user','короткий запрос',1)")
    conn.commit()
    conn.close()


def _setup(tmp_path, monkeypatch, big=False, mid_edit=False):
    _make_state_db(str(tmp_path / "state.db"), big=big, mid_edit=mid_edit)
    wiki = tmp_path / "wiki"
    wiki.mkdir(exist_ok=True)  # exist_ok: _setup может вызываться дважды (тест a)
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)
    return idx, wiki


# ─────────────────────────────────────────────────────────────
# (a) главный кейс: правка в СЕРЕДИНЕ длинной сессии меняет хэш
# ─────────────────────────────────────────────────────────────
def test_middle_edit_changes_hash(tmp_path, monkeypatch):
    """Правка в середине 10k-сессии → session_content_hash изменился.
    Раньше (get_session_text резал [:500]/CHUNK_LIMIT) — НЕ менялся (FAIL)."""
    idx, _ = _setup(tmp_path, monkeypatch, big=True)
    h1 = idx.session_content_hash("s1")

    # Пересоздаём state.db с правкой в середине (сначала удалить старый файл)
    os.remove(str(tmp_path / "state.db"))
    import importlib
    _setup(tmp_path, monkeypatch, big=True, mid_edit=True)
    importlib.reload(idx)
    h2 = idx.session_content_hash("s1")

    assert h1 != h2, f"hash must change on middle edit, got {h1} == {h2}"


# ─────────────────────────────────────────────────────────────
# (b) session_raw_text возвращает ВЕСЬ текст (нет [:500])
# ─────────────────────────────────────────────────────────────
def test_session_raw_text_full(tmp_path, monkeypatch):
    idx, _ = _setup(tmp_path, monkeypatch, big=True)
    text = idx.session_raw_text("s1")
    # 12k+ chars must be present in FULL (old get_session_text would cut at 8k)
    assert len(text) > 11000, f"expected full text >11k chars, got {len(text)}"
    # Both beginning and END present (head/tail would keep both, but middle too)
    assert "начало" in text and "конец" in text
    assert "середина" in text, "middle must be present (not truncated)"


# ─────────────────────────────────────────────────────────────
# (c) потоковый sha256 == однократный sha256
# ─────────────────────────────────────────────────────────────
def test_streaming_hash_equals_oneshot(tmp_path, monkeypatch):
    idx, _ = _setup(tmp_path, monkeypatch, big=True)
    text = idx.session_raw_text("s1")
    streamed = idx._streaming_sha256(text)
    oneshot = hashlib.sha256(text.encode()).hexdigest()[:16]
    assert streamed == oneshot, f"streaming {streamed} != oneshot {oneshot}"


# ─────────────────────────────────────────────────────────────
# (d) миграция: user_version<1 → migrate_to(1) добавляет колонку
# ─────────────────────────────────────────────────────────────
def test_migration_adds_full_content_hash(tmp_path, monkeypatch):
    idx, wiki = _setup(tmp_path, monkeypatch)

    # Сначала создаём БД через IndexDB (создаст схему + применит миграции)
    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    db.close()

    # Симулируем СТАРУЮ БД: сбрасываем user_version в 0 (колонки full_* могли
    # уже быть добавлены — проверяем что migrate_to(1) идемпотентен и верен)
    db_path = str(wiki / ".index_v2.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    from wiki_v2.index_db import IndexDB, MIGRATIONS, migrate_to
    conn = sqlite3.connect(db_path)
    migrate_to(conn, 1)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
    conn.close()

    assert ver == 1, f"user_version must be 1, got {ver}"
    assert "full_content_hash" in cols, "full_content_hash must be added by v1"


# ─────────────────────────────────────────────────────────────
# (e) идемпотентность: migrate_to(2) дважды → не падает
# ─────────────────────────────────────────────────────────────
def test_migration_idempotent(tmp_path, monkeypatch):
    idx, wiki = _setup(tmp_path, monkeypatch)

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    db.close()
    db_path = str(wiki / ".index_v2.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 0")
    conn.commit()
    conn.close()

    from wiki_v2.index_db import IndexDB, MIGRATIONS, migrate_to
    conn = sqlite3.connect(db_path)
    migrate_to(conn, 2)
    migrate_to(conn, 2)  # второй раз — идемпотентно
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
    conn.close()

    assert ver == 2, f"user_version must be 2, got {ver}"
    assert "full_content_hash" in cols
    assert "full_text" in cols


# ─────────────────────────────────────────────────────────────
# (f) старая content_hash на месте, не задвоена
# ─────────────────────────────────────────────────────────────
def test_old_content_hash_preserved(tmp_path, monkeypatch):
    idx, wiki = _setup(tmp_path, monkeypatch)

    db = idx.IndexDB(str(wiki / ".index_v2.db"))
    db.upsert_page(slug="p1", title="P1", section="entities", path=str(wiki / "entities" / "p1.md"),
                   content_hash="deadbeef", summary="", quality="ok")
    db.close()

    conn = sqlite3.connect(str(wiki / ".index_v2.db"))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pages)")}
    row = conn.execute("SELECT content_hash FROM pages WHERE slug='p1'").fetchone()
    conn.close()

    assert "content_hash" in cols
    assert "full_content_hash" in cols  # новая колонка добавлена, старая не тронута
    assert row[0] == "deadbeef", "old content_hash value must be preserved"
