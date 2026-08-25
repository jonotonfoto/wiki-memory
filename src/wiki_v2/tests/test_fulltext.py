# tests/test_fulltext.py — этап 1.4: full_text в БД, keyword_hits без диска
import os
import sqlite3
import tempfile

from wiki_v2.index_db import IndexDB
from wiki_v2.search import keyword_hits


def _make_db(path):
    db = IndexDB(path)
    db.upsert_page("a", "Сознание", "", "p1.md", "h1",
                   summary="психология развития", full_text="Сознание ребёнка формируется в игре")
    db.upsert_page("b", "VPN", "", "p2.md", "h2",
                   summary="сервер", full_text="установка vpn на windows 11")
    # старая страница БЕЗ full_text (не переиндексирована)
    db.upsert_page("c", "Старая", "", "p3.md", "h3", summary="старая запись")
    return db


def test_fulltext_column_in_schema():
    with tempfile.TemporaryDirectory() as td:
        db = IndexDB(os.path.join(td, "t.db"))
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(pages)")}
        assert "full_text" in cols
        db.close()


def test_upsert_saves_fulltext():
    with tempfile.TemporaryDirectory() as td:
        db = IndexDB(os.path.join(td, "t.db"))
        db.upsert_page("x", "T", "", "p.md", "h", full_text="содержимое")
        row = db.get_page("x")
        assert row["full_text"] == "содержимое"
        db.close()


def test_upsert_without_fulltext_backward_compat():
    with tempfile.TemporaryDirectory() as td:
        db = IndexDB(os.path.join(td, "t.db"))
        db.upsert_page("y", "T", "", "p.md", "h")  # без full_text — не падает
        row = db.get_page("y")
        assert row["full_text"] == ""
        db.close()


def test_migrate_adds_fulltext_to_old_db():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "old.db")
        # создаём СТАРУЮ схему без full_text
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE pages (
            slug TEXT PRIMARY KEY, title TEXT NOT NULL, section TEXT NOT NULL,
            path TEXT NOT NULL, content_hash TEXT NOT NULL, summary TEXT DEFAULT '',
            quality TEXT DEFAULT 'ok', created REAL NOT NULL, updated REAL NOT NULL)""")
        conn.execute("""CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, indexed_at REAL NOT NULL,
            page_slug TEXT DEFAULT '', content_hash TEXT DEFAULT '')""")
        conn.commit()
        conn.close()
        # IndexDB открывает → _migrate добавляет full_text
        db = IndexDB(path)
        cols = {r[1] for r in db.conn.execute("PRAGMA table_info(pages)")}
        assert "full_text" in cols
        db.close()


def test_keyword_hits_uses_fulltext_not_disk():
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(os.path.join(td, "t.db"))
        pages = {p["slug"]: p for p in db.all_pages()}
        # full_text из БД, файла p2.md НЕТ на диске
        hits = keyword_hits("vpn настройка", list(pages.values()), k=5)
        slugs = [s for s, _ in hits]
        assert "b" in slugs  # нашёл по full_text из БД
        db.close()


def test_keyword_hits_no_disk_read():
    """Проверить, что keyword_hits НЕ открывает файлы с диска."""
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(os.path.join(td, "t.db"))
        pages = {p["slug"]: p for p in db.all_pages()}
        # страницы указывают на несуществующие файлы — если код читает диск, упадёт/не найдёт
        for p in pages.values():
            p["path"] = os.path.join(td, "несуществующий.md")
        hits = keyword_hits("сознание ребёнка", list(pages.values()), k=5)
        assert any(s == "a" for s, _ in hits)  # нашёл по full_text
        db.close()


def test_keyword_hits_cyrillic_regex():
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(os.path.join(td, "t.db"))
        pages = {p["slug"]: p for p in db.all_pages()}
        hits = keyword_hits("сознание", list(pages.values()), k=5)
        assert any(s == "a" for s, _ in hits)  # кириллица ловится
        db.close()


def test_keyword_hits_old_page_no_fulltext():
    with tempfile.TemporaryDirectory() as td:
        db = _make_db(os.path.join(td, "t.db"))
        pages = {p["slug"]: p for p in db.all_pages()}
        # старая страница без full_text — не падает, ищет по title+summary
        hits = keyword_hits("старая запись", [pages["c"]], k=5)
        assert any(s == "c" for s, _ in hits)
        db.close()
