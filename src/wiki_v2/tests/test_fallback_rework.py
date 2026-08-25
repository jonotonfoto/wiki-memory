"""Фикс fallback-переделки: сбойная страница обновляется, а не дублируется.

Проверяет, что process_session при наличии session->page связи на fallback-страницу
переделывает ИМЕННО её (MERGE, quality fallback->ok), а не создаёт новый дубль.
"""
import sqlite3

import pytest


@pytest.fixture(autouse=True)
def _tmp_wiki(tmp_path, monkeypatch):
    """Направить HERMES_HOME/WIKI_PATH/STATE_DB во временную директорию."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import wiki_v2.config as cfg
    monkeypatch.delenv("WIKI_PATH", raising=False)
    cfg.reload()
    yield


def _mk_state_db(tmp_path, session_id, title):
    """Минимальная state.db с сессией и сообщением."""
    db = tmp_path / "state.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at INTEGER)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp INTEGER)")
    conn.execute("INSERT INTO sessions (id, title, started_at) VALUES (?,?,?)",
                 (session_id, title, 0))
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                 (session_id, "user", "Проверяем наличие дашборда в проекте", 0))
    conn.commit()
    conn.close()


def _mk_fallback_page(db, slug, session_id):
    """Создать fallback-страницу и связать её с сессией."""
    from wiki_v2 import config
    target_dir = config.WIKI_PATH / "entities"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{slug}.md"
    md = ("---\ntitle: 'Test'\nquality: fallback\nkey_topics: ['Some long fallback title that never matches tags']\n"
          "---\n\n# Test\n\nfallback fragment\n---\n📅 Дата разговора: 2026-08-16\n")
    path.write_text(md, encoding="utf-8")
    db.upsert_page(slug=slug, title="Test", section="entities", path=str(path),
                   content_hash="old", summary="fallback", quality="fallback")
    db.conn.execute(
        "INSERT OR REPLACE INTO sessions (session_id, page_slug, content_hash, indexed_at) VALUES (?,?,?,?)",
        (session_id, slug, "oldhash", 1786912088.0))
    db.conn.commit()


def test_fallback_rework_updates_same_page(tmp_path, monkeypatch):
    """Когда сессия связана с fallback-страницей, process_session обновляет её (не дублирует)."""
    import wiki_v2.indexer as idx_mod
    from wiki_v2.index_db import IndexDB
    from wiki_v2.indexer import process_session

    session_id = "20260816_test1"
    db_path = tmp_path / ".index_v2.db"
    db = IndexDB(str(db_path))

    # Подготовить state.db + fallback-страницу, связанную с сессией
    _mk_state_db(tmp_path, session_id, "Test title")
    _mk_fallback_page(db, "test-title", session_id)

    # Направить STATE_DB во временный файл (indexer копирует его при импорте)
    monkeypatch.setattr(idx_mod, "STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setattr(idx_mod, "session_raw_text", lambda sid: "short text")
    monkeypatch.setattr(idx_mod, "split_text", lambda text: [])

    # extract_content возвращает ok (модель теперь работает)
    monkeypatch.setattr(idx_mod, "extract_content", lambda title, text: {
        "summary": "разговор о дашборде",
        "key_topics": ["дашборд", "проект"],
        "entities": [], "concepts": [], "links": [],
        "quality": "ok",
    })
    monkeypatch.setattr(idx_mod, "map_chunk_tags", lambda title, chunks: [])
    monkeypatch.setattr(idx_mod, "reduce_chunk_tags", lambda title, tags: [])
    monkeypatch.setattr(idx_mod, "embed_multivector", lambda title, summary, topics: {})
    monkeypatch.setattr(idx_mod, "embed_chunks", lambda title, chunks: {})
    # find_merge_target вернёт None (по тегам fallback не находится) — но фикс
    # должен найти по session->page связи ДО тегов.
    # ⚠️ indexer импортирует find_merge_target напрямую (from .pages import ...),
    # поэтому мокать надо idx_mod.find_merge_target, а не wiki_v2.pages.
    monkeypatch.setattr(idx_mod, "find_merge_target",
                        lambda topics, candidates, new_title=None: None)

    session = {"id": session_id, "title": "Test title", "started_at": 0}
    slug = process_session(db, session)

    # Должна обновиться ТА ЖЕ fallback-страница, а не создаться дубль
    assert slug == "test-title", f"ожидали переделку test-title, получили {slug}"
    page = db.get_page("test-title")
    assert page is not None
    assert page["quality"] == "ok", f"страница должна стать ok, получили {page['quality']}"

    # Не должно быть дубля с суффиксом
    n = db.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    assert n == 1, f"должна быть 1 страница, получили {n} (дубль создан?)"
    db.close()


def test_normal_page_uses_merge_target(tmp_path, monkeypatch):
    """Без session->fallback связи — обычный find_merge_target (поведение не сломано)."""
    import wiki_v2.indexer as idx_mod
    from wiki_v2.index_db import IndexDB
    from wiki_v2.indexer import process_session

    session_id = "20260816_test2"
    db_path = tmp_path / ".index_v2.db"
    db = IndexDB(str(db_path))
    _mk_state_db(tmp_path, session_id, "Normal title")

    # Создаём существующую ok-страницу target-page, чтобы MERGE нашёл её
    from wiki_v2 import config
    target_dir = config.WIKI_PATH / "entities"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "target-page.md"
    target_path.write_text(
        "---\ntitle: 'Target'\nquality: ok\nkey_topics: ['дашборд']\n---\n\n# Target\n---\n📅 Дата разговора: 2026-08-16\n",
        encoding="utf-8")
    db.upsert_page(slug="target-page", title="Target", section="entities", path=str(target_path),
                   content_hash="x", summary="old", quality="ok")

    monkeypatch.setattr(idx_mod, "STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setattr(idx_mod, "session_raw_text", lambda sid: "short text")
    monkeypatch.setattr(idx_mod, "split_text", lambda text: [])
    monkeypatch.setattr(idx_mod, "extract_content", lambda title, text: {
        "summary": "s", "key_topics": ["дашборд"], "entities": [], "concepts": [],
        "links": [], "quality": "ok",
    })
    monkeypatch.setattr(idx_mod, "map_chunk_tags", lambda title, chunks: [])
    monkeypatch.setattr(idx_mod, "reduce_chunk_tags", lambda title, tags: [])
    monkeypatch.setattr(idx_mod, "embed_multivector", lambda title, summary, topics: {})
    monkeypatch.setattr(idx_mod, "embed_chunks", lambda title, chunks: {})
    # Нет session->page связи → get_page_slug_for_session вернёт "" → используем find_merge_target
    # (мокаем idx_mod.find_merge_target, чтобы вернул target-page)
    monkeypatch.setattr(idx_mod, "find_merge_target",
                        lambda topics, candidates, new_title=None: "target-page")

    session = {"id": session_id, "title": "Normal title", "started_at": 0}
    slug = process_session(db, session)
    assert slug == "target-page", f"должен использоваться find_merge_target, получили {slug}"
    # страница target-page осталась одна (MERGE, не CREATE)
    n = db.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    assert n == 1, f"должна быть 1 страница, получили {n}"
    db.close()


def test_ok_page_session_link_merges(tmp_path, monkeypatch):
    """3.1: сессия имеет связь с ok-страницей → MERGE в неё, не CREATE (дубль исключён)."""
    import wiki_v2.indexer as idx_mod
    from wiki_v2.index_db import IndexDB
    from wiki_v2.indexer import process_session

    session_id = "20260818_oklink"
    db_path = tmp_path / ".index_v2.db"
    db = IndexDB(str(db_path))
    _mk_state_db(tmp_path, session_id, "Ok-title")

    # Создаём ok-страницу и СВЯЗЫВАЕМ сессию с ней (page_slug)
    from wiki_v2 import config
    target_dir = config.WIKI_PATH / "entities"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / "ok-page.md"
    target_path.write_text(
        "---\ntitle: 'Ok Page'\nquality: ok\nkey_topics: ['тема']\n---\n\n# Ok Page\n---\n📅 Дата разговора: 2026-08-18\n",
        encoding="utf-8")
    db.upsert_page(slug="ok-page", title="Ok Page", section="entities", path=str(target_path),
                   content_hash="x", summary="old", quality="ok")
    # сессия привязана к ok-странице
    db.mark_session_indexed(session_id, page_slug="ok-page", content_hash="prevhash")
    assert db.get_page_slug_for_session(session_id) == "ok-page"

    monkeypatch.setattr(idx_mod, "STATE_DB", str(tmp_path / "state.db"))
    monkeypatch.setattr(idx_mod, "session_raw_text", lambda sid: "short text")
    monkeypatch.setattr(idx_mod, "split_text", lambda text: [])
    monkeypatch.setattr(idx_mod, "extract_content", lambda title, text: {
        "summary": "s2", "key_topics": ["другая-тема"], "entities": [], "concepts": [],
        "links": [], "quality": "ok",
    })
    monkeypatch.setattr(idx_mod, "map_chunk_tags", lambda title, chunks: [])
    monkeypatch.setattr(idx_mod, "reduce_chunk_tags", lambda title, tags: [])
    monkeypatch.setattr(idx_mod, "embed_multivector", lambda title, summary, topics: {})
    monkeypatch.setattr(idx_mod, "embed_chunks", lambda title, chunks: {})
    # по тегам НЕ находится (другая тема) → без фикса 3.1 был бы CREATE
    monkeypatch.setattr(idx_mod, "find_merge_target",
                        lambda topics, candidates, new_title=None: None)

    session = {"id": session_id, "title": "Ok-title", "started_at": 0}
    slug = process_session(db, session)
    # связь session→page выигрывает: MERGE в ok-page, не CREATE
    assert slug == "ok-page", f"ожидали MERGE в ok-page, получили {slug}"
    n = db.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    assert n == 1, f"должна быть 1 страница (не дубль), получили {n}"
