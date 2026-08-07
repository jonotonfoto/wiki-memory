# index_db.py
"""SQLite index for wiki pages, embeddings, and indexed sessions."""
import sqlite3
import time

import numpy as np

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    summary TEXT DEFAULT '',
    quality TEXT DEFAULT 'ok',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    slug TEXT PRIMARY KEY REFERENCES pages(slug) ON DELETE CASCADE,
    vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    indexed_at REAL NOT NULL,
    page_slug TEXT DEFAULT '',
    content_hash TEXT DEFAULT ''
);
"""


def _migrate(conn):
    """Add new columns to existing tables (idempotent, safe on old DBs)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "content_hash" not in cols:
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()


class IndexDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        _migrate(self.conn)
        self.conn.commit()

    def upsert_page(self, slug, title, section, path, content_hash,
                    summary="", quality="ok"):
        now = time.time()
        self.conn.execute(
            """INSERT INTO pages (slug, title, section, path, content_hash,
                                  summary, quality, created, updated)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET
                 title=excluded.title, section=excluded.section,
                 path=excluded.path, content_hash=excluded.content_hash,
                 summary=excluded.summary, quality=excluded.quality,
                 updated=excluded.updated""",
            (slug, title, section, path, content_hash, summary, quality, now, now))
        self.conn.commit()

    def get_page(self, slug):
        r = self.conn.execute("SELECT * FROM pages WHERE slug=?", (slug,)).fetchone()
        return dict(r) if r else None

    def all_pages(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM pages")]

    def set_embedding(self, slug, vector: np.ndarray):
        self.conn.execute(
            "INSERT OR REPLACE INTO embeddings (slug, vector) VALUES (?,?)",
            (slug, vector.astype(np.float32).tobytes()))
        self.conn.commit()

    def get_all_embeddings(self):
        out = {}
        for r in self.conn.execute("SELECT slug, vector FROM embeddings"):
            out[r["slug"]] = np.frombuffer(r["vector"], dtype=np.float32)
        return out

    def is_session_indexed(self, session_id):
        r = self.conn.execute("SELECT 1 FROM sessions WHERE session_id=?",
                              (session_id,)).fetchone()
        return r is not None

    def mark_session_indexed(self, session_id, page_slug="", content_hash=None):
        self.conn.execute(
            """INSERT INTO sessions (session_id, indexed_at, page_slug, content_hash)
               VALUES (?,?,?,COALESCE(?, ''))
               ON CONFLICT(session_id) DO UPDATE SET
                indexed_at=excluded.indexed_at,
                page_slug=excluded.page_slug,
                content_hash=COALESCE(excluded.content_hash,
                                      sessions.content_hash)""",
            (session_id, time.time(), page_slug, content_hash))
        self.conn.commit()

    def set_session_hash(self, session_id, content_hash):
        self.conn.execute(
            "UPDATE sessions SET content_hash=? WHERE session_id=?",
            (content_hash, session_id))
        self.conn.commit()

    def get_session_hash(self, session_id) -> str:
        r = self.conn.execute(
            "SELECT content_hash FROM sessions WHERE session_id=?",
            (session_id,)).fetchone()
        return (r["content_hash"] or "") if r else ""

    def close(self):
        self.conn.close()
