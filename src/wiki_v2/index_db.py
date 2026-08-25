# index_db.py
"""
SQLite index for wiki pages, embeddings, and indexed sessions.
"""
import sqlite3
import time

import numpy as np

from . import config
from .logging_setup import logger

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    path TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    summary TEXT DEFAULT '',
    quality TEXT DEFAULT 'ok',
    full_text TEXT DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS embeddings (
    slug TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'page',
    vector BLOB NOT NULL,
    embed_model_id TEXT DEFAULT '',
    PRIMARY KEY (slug, kind),
    FOREIGN KEY (slug) REFERENCES pages(slug) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    indexed_at REAL NOT NULL,
    page_slug TEXT DEFAULT '',
    content_hash TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS entities (
    slug TEXT NOT NULL,
    entity TEXT NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    from_slug TEXT NOT NULL,
    to_slug TEXT NOT NULL,
    strength REAL DEFAULT 1.0
);
CREATE TABLE IF NOT EXISTS edges (
    from_slug TEXT NOT NULL,
    to_slug TEXT NOT NULL,
    rel TEXT NOT NULL,
    strength REAL DEFAULT 1.0
);
"""

# MIGRATION REGISTRY (этап 1.2, S1.2): единый реестр миграций схемы.
# Каждая будущая фаза ДОБАВЛЯЕТ номер сюда, НЕ задаёт свои пороги.
# Применяются migrate_to() по user_version — идемпотентно.
# Формат: (version, sql, migration_type) where migration_type is 'add_column' or 'rebuild'
MIGRATIONS: list[tuple] = [
    (1, "ALTER TABLE pages ADD COLUMN full_content_hash TEXT DEFAULT ''", 'add_column'),
    (2, "ALTER TABLE pages ADD COLUMN full_text TEXT DEFAULT ''", 'add_column'),
    # S2.5.4: какая embed-модель считала вектор (одна модель на всю базу).
    # Значение — id модели (напр. 'nvidia/nv-embedqa-e5-v5', 'text-embedding-qwen3-embedding-0.6b').
    (3, "ALTER TABLE embeddings ADD COLUMN embed_model_id TEXT DEFAULT ''", 'add_column'),
    # S2.5.5: Мульти-вектор на страницу: перестраиваем таблицу embeddings
    (4, "REBUILD_EMBEDDINGS", 'rebuild'),
    # S2.5.6: Confidence tracking на страницу — confidence, contested, contradictions.
    (5, "ALTER TABLE pages ADD COLUMN confidence REAL DEFAULT 0.5", 'add_column'),
    (5, "ALTER TABLE pages ADD COLUMN contested INTEGER DEFAULT 0", 'add_column'),
    (5, "ALTER TABLE pages ADD COLUMN contradictions TEXT DEFAULT '[]'", 'add_column'),
    (6, "CREATE TABLE IF NOT EXISTS entities (slug TEXT NOT NULL, entity TEXT NOT NULL, kind TEXT NOT NULL)", 'add_table'),
    (6, "CREATE TABLE IF NOT EXISTS links (from_slug TEXT NOT NULL, to_slug TEXT NOT NULL, strength REAL DEFAULT 1.0)", 'add_table'),
    # S4.7: Knowledge graph edges — directed relations with predicates.
    (7, "CREATE TABLE IF NOT EXISTS edges (from_slug TEXT NOT NULL, to_slug TEXT NOT NULL, rel TEXT NOT NULL, strength REAL DEFAULT 1.0)", 'add_table'),
]


def _column_exists(conn, table: str, col: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    return col in cols


def _rebuild_embeddings_v4(conn):
    """Перестраиваем таблицу embeddings: добавляем колонку kind и делаем PK (slug, kind).
    Идемпотентно: если колонка kind существует и PK корректный — ничего не делаем.
    """
    print("[_rebuild_embeddings_v4] Called")  # DEBUG
    # Проверяем, нужно ли перестраивать
    cur = conn.execute("PRAGMA table_info(embeddings)")
    columns = {row[1]: row for row in cur.fetchall()}
    if "kind" in columns:
        print("[_rebuild_embeddings_v4] kind column exists")  # DEBUG
        # Проверяем, что PK состоит из slug и kind
        # Получаем информацию о первичном ключе
        idx_list = conn.execute("PRAGMA index_list(embeddings)").fetchall()
        if idx_list:
            # Предполагаем, что первый индекс в списке — это PK (так как у нас только один индекс на таблицу)
            idx_name = idx_list[0][1]
            idx_info = conn.execute(f"PRAGMA index_info({idx_name})").fetchall()
            if len(idx_info) == 2:
                # Два столбца в PK
                pk_col_names = {row[2] for row in idx_info}  # row[2] - имя столбца
                if pk_col_names == {'slug', 'kind'}:
                    print("[_rebuild_embeddings_v4] PK is already (slug, kind)")  # DEBUG
                    # Уже перестроено
                    return
    print("[_rebuild_embeddings_v4] Rebuilding embeddings table")  # DEBUG
    # Вызывается ВНУТРИ транзакции migrate_to (BEGIN уже открыт). НЕ начинать свой BEGIN.
    # Иначе — вложенная транзакция SQLite = "cannot start a transaction within a transaction".
    try:
        # Создаем новую таблицу с новой схемой
        conn.execute("""
            CREATE TABLE embeddings_new (
                slug TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'page',
                vector BLOB NOT NULL,
                embed_model_id TEXT DEFAULT '',
                PRIMARY KEY (slug, kind),
                FOREIGN KEY (slug) REFERENCES pages(slug) ON DELETE CASCADE
            )
        """)
        # Копируем данные из старой таблицы, предполагая kind='page'
        conn.execute("""
            INSERT INTO embeddings_new (slug, kind, vector, embed_model_id)
            SELECT slug, 'page', vector, embed_model_id FROM embeddings
        """)
        # Удаляем старую таблицу
        conn.execute("DROP TABLE embeddings")
        # Переименовываем новую таблицу
        conn.execute("ALTER TABLE embeddings_new RENAME TO embeddings")
        print("[_rebuild_embeddings_v4] Rebuild completed")  # DEBUG
    except Exception as e:
        print(f"[_rebuild_embeddings_v4] Error during rebuild: {e}")  # DEBUG
        raise


def migrate_to(conn, target: int) -> None:
    """Применить миграции реестра до target (включительно), идемпотентно.

    Читает PRAGMA user_version, применяет MIGRATIONS[user_version+1 .. target]
    по порядку, каждую в транзакции. Если колонка уже существует (напр.
    full_text добавлена прошлой задачей 1.4 без реестра) — ALTER пропускается.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    print(f"[migrate_to] current={current}, target={target}")  # DEBUG
    for ver, sql, migration_type in MIGRATIONS:
        if ver > target or ver <= current:
            print(f"[migrate_to] Skipping migration {ver}")  # DEBUG
            continue
        print(f"[migrate_to] Applying migration {ver} ({migration_type})")  # DEBUG
        try:
            conn.execute("BEGIN")
            if migration_type == 'add_column':
                # Идемпотентность к уже существующей колонке (например full_text
                # добавлена прошлой задачей 1.4 ДО реестра) — не падать, пропустить ALTER.
                # Таблица и колонка парсятся из SQL (pages/embeddings — разные).
                table = sql.split("ADD COLUMN ")[0].replace("ALTER TABLE ", "").strip()
                col = sql.split("ADD COLUMN ")[1].split(" ")[0]
                if _column_exists(conn, table, col):
                    print(f"[migrate_to] Column {col} already exists in {table}, skipping")  # DEBUG
                    conn.execute(f"PRAGMA user_version = {ver}")
                    conn.execute("COMMIT")
                    continue
                print(f"[migrate_to] Executing: {sql}")  # DEBUG
                conn.execute(sql)
                conn.execute(f"PRAGMA user_version = {ver}")
                conn.execute("COMMIT")
            elif migration_type == 'rebuild':
                # Для rebuild миграции проверяем идемпотентность внутри функции
                _rebuild_embeddings_v4(conn)
                conn.execute(f"PRAGMA user_version = {ver}")
                conn.execute("COMMIT")
            elif migration_type == 'add_table':
                print(f"[migrate_to] Executing: {sql}")  # DEBUG
                conn.execute(sql)
                conn.execute(f"PRAGMA user_version = {ver}")
                conn.execute("COMMIT")
        except sqlite3.OperationalError as e:
            print(f"[migrate_to] OperationalError for migration {ver}: {e}")  # DEBUG
            conn.rollback()
            # Если ALTER не удался (колонка уже есть из-за гонки) — просто двигаем версию
            conn.execute(f"PRAGMA user_version = {ver}")
            conn.commit()


def _migrate(conn):
    """Add new columns to existing tables (idempotent, safe on old DBs)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    if "content_hash" not in cols:
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN content_hash TEXT DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()

    # full_text/full_content_hash для pages — теперь через РЕЕСТР (migrate_to).
    # Здесь НЕ добавляем их напрямую — это делает migrate_to в __init__.
    # (full_text мог быть добавлен старой задачей 1.4 — реестр это учтёт.)


class IndexDB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        _migrate(self.conn)
        migrate_to(self.conn, len(MIGRATIONS))  # реестр миграций (этап 1.2)
        self.conn.commit()

    def upsert_page(self, slug, title, section, path, content_hash,
                    summary="", quality="ok", full_text=""):
        now = time.time()
        self.conn.execute(
            """INSERT INTO pages (slug, title, section, path, content_hash,
                                  summary, quality, full_text, created, updated)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(slug) DO UPDATE SET
                title=excluded.title, section=excluded.section,
                path=excluded.path, content_hash=excluded.content_hash,
                summary=excluded.summary, quality=excluded.quality,
                full_text=excluded.full_text,
                updated=excluded.updated""",
            (slug, title, section, path, content_hash, summary, quality, full_text, now, now))
        self.conn.commit()
        logger.debug("[DB] upsert_page slug=%s quality=%s", slug, quality)

    def get_page(self, slug):
        r = self.conn.execute("SELECT * FROM pages WHERE slug=? ", (slug,)).fetchone()
        return dict(r) if r else None

    def all_pages(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM pages")]

    def delete_page(self, slug: str):
        """Удалить страницу и её эмбеддинги из индекса."""
        self.conn.execute("DELETE FROM pages WHERE slug=?", (slug,))
        self.conn.execute("DELETE FROM embeddings WHERE slug=?", (slug,))
        self.conn.commit()

    def update_page_hash(self, slug: str, content_hash: str):
        """Фаза 2 двухфазного коммита: обновить content_hash после os.replace."""
        self.conn.execute(
            "UPDATE pages SET content_hash=? WHERE slug=?",
            (content_hash, slug))
        self.conn.commit()

    def get_pending_pages(self):
        """Вернуть строки со content_hash='PENDING' (незавершённый коммит)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT slug, path FROM pages WHERE content_hash='PENDING'")]

    def set_embedding(self, slug, vector: np.ndarray, kind="page", model_id=""):
        """Сохранить вектор. model_id — id embed-модели (S2.5.4: одна на всю базу)."""
        if vector is not None and len(vector) != config.EMBED_DIM:
            raise ValueError(f"Embedding dimension mismatch: expected {config.EMBED_DIM}, got {len(vector)}")
        self.conn.execute(
            """INSERT OR REPLACE INTO embeddings (slug, kind, vector, embed_model_id)
              VALUES (?,?,?,?)""",
            (slug, kind, vector.astype(np.float32).tobytes(), model_id))
        self.conn.commit()
        logger.debug("[DB] set_embedding slug=%s kind=%s dim=%d", slug, kind, len(vector))

    def get_embed_model_ids(self) -> set:
        """Множество embed_model_id в базе (должно быть {одна модель})."""
        return {r[0] for r in self.conn.execute(
            "SELECT DISTINCT embed_model_id FROM embeddings WHERE embed_model_id != ''")}

    def get_all_embeddings(self):
        """Возвращает dict: {slug: vector} (берется первый вектор для каждого slug)."""
        out = {}
        for r in self.conn.execute("SELECT slug, kind, vector FROM embeddings"):
            slug = r["slug"]
            if slug not in out:
                vector = np.frombuffer(r["vector"], dtype=np.float32)
                out[slug] = vector
        return out

    def get_all_embeddings_by_kind(self):
        """Возвращает dict: {kind: {slug: vector}} из таблицы embeddings."""
        out = {}
        for r in self.conn.execute("SELECT slug, kind, vector FROM embeddings"):
            kind = r["kind"]
            slug = r["slug"]
            vector = np.frombuffer(r["vector"], dtype=np.float32)
            out.setdefault(kind, {})[slug] = vector
        return out

    def get_page_chunk_embeddings(self, slug) -> dict:
        """Векторы чанков ОДНОЙ страницы. {kind: np.ndarray}.

        Фикс 2026-08-24: принимает ОБЕ семьи — текущую chunk:N (sweep-путь)
        и легаси page_chunk:N. Раньше LIKE 'page_chunk:%' оставлял новые
        страницы без чанк-векторов → fallback «начало файла» в плагине.
        """
        out = {}
        for r in self.conn.execute(
            "SELECT kind, vector FROM embeddings WHERE slug=? "
            "AND (kind LIKE 'page_chunk:%' OR kind LIKE 'chunk:%')",
            (slug,)):
            out[r["kind"]] = np.frombuffer(r["vector"], dtype=np.float32)
        return out

    def get_session_chunk_embeddings(self, slug) -> dict:
        """Векторы чанков СЫРОГО текста сессий страницы (2026-08-25).

        kind='session_chunk:N' — нарезка session_raw_text() первичной сессии
        страницы; N совпадает с индексом span в split_text_spans(raw).
        """
        out = {}
        for r in self.conn.execute(
            "SELECT kind, vector FROM embeddings WHERE slug=? "
            "AND kind LIKE 'session_chunk:%'",
            (slug,)):
            out[r["kind"]] = np.frombuffer(r["vector"], dtype=np.float32)
        return out

    def save_entities(self, slug, entities, concepts):
        """Очистить старые сущности страницы, вставить новые (kind='entity'/'concept')."""
        self.conn.execute("DELETE FROM entities WHERE slug=?", (slug,))
        for ent in (entities or []):
            self.conn.execute("INSERT INTO entities (slug, entity, kind) VALUES (?,?,?)", (slug, str(ent), 'entity'))
        for c in (concepts or []):
            self.conn.execute("INSERT INTO entities (slug, entity, kind) VALUES (?,?,?)", (slug, str(c), 'concept'))
        self.conn.commit()

    def save_links(self, from_slug, links, strength=1.0):
        """Вставить связи from_slug->to_slug. Двунаправленность: A->B означает и B->A."""
        # Удаляем ТОЛЬКО исходящие связи from_slug. НЕ удаляем по to_slug —
        # иначе save_links('b',...) затрёт обратную связь (b->a), добавленную
        # save_links('a',['b']). Двунаправленность живёт как отдельные строки.
        self.conn.execute("DELETE FROM links WHERE from_slug=?", (from_slug,))
        for to in (links or []):
            self.conn.execute("INSERT INTO links (from_slug, to_slug, strength) VALUES (?,?,?)", (from_slug, str(to), strength))
            self.conn.execute("INSERT INTO links (from_slug, to_slug, strength) VALUES (?,?,?)", (str(to), from_slug, strength))
        self.conn.commit()

    def get_graph(self):
        """Вернуть (entities_dict, links_dict). entities_dict={slug:[entity...]}, links_dict={slug:set(to_slugs)} (неориентированный)."""
        entities_dict = {}
        for r in self.conn.execute("SELECT slug, entity, kind FROM entities"):
            if r["kind"] == "entity":
                entities_dict.setdefault(r["slug"], []).append(r["entity"])
        links_dict = {}
        for r in self.conn.execute("SELECT from_slug, to_slug FROM links"):
            links_dict.setdefault(r["from_slug"], set()).add(r["to_slug"])
        return entities_dict, links_dict

    def save_edges(self, slug, triplets):
        """Очистить старые рёбра страницы, вставить новые.

        triplets: list[dict] с ключами subject/predicate/object (или list[tuple]).
        from=slug (текущая страница), to=object, rel=predicate.
        Дедуп по (from_slug, rel, to). strength=1.0.
        """
        self.conn.execute("DELETE FROM edges WHERE from_slug=?", (slug,))
        seen = set()
        for t in (triplets or []):
            if isinstance(t, dict):
                s, p, o = str(t.get("subject", "")), str(t.get("predicate", "")), str(t.get("object", ""))
            elif isinstance(t, (list, tuple)) and len(t) >= 3:
                s, p, o = str(t[0]), str(t[1]), str(t[2])
            else:
                continue
            key = (slug, p, o)
            if key in seen:
                continue
            seen.add(key)
            self.conn.execute(
                "INSERT INTO edges (from_slug, to_slug, rel, strength) VALUES (?,?,?,?)",
                (slug, o, p, 1.0))
        self.conn.commit()

    def get_edges(self):
        """Вернуть {from_slug: [(rel, to_slug), ...]} — направленные рёбра."""
        edges_dict = {}
        for r in self.conn.execute("SELECT from_slug, rel, to_slug FROM edges"):
            edges_dict.setdefault(r["from_slug"], []).append((r["rel"], r["to_slug"]))
        return edges_dict

    def get_page_entities(self, slug):
        """Список entity (kind='entity') для страницы."""
        return [r["entity"] for r in self.conn.execute(
            "SELECT entity FROM entities WHERE slug=? AND kind='entity'", (slug,))]

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

    def get_page_slug_for_session(self, session_id) -> str:
        """Return the page_slug associated with a session ('' if none)."""
        r = self.conn.execute(
            "SELECT page_slug FROM sessions WHERE session_id=?",
            (session_id,)).fetchone()
        return (r["page_slug"] or "") if r else ""

    def close(self):
        self.conn.close()


def default_fact_confidence(facts) -> list[float]:
    """S4.1 — Return a confidence value for each fact.

    If facts is list[dict] with 'text'/'confidence' keys → [clamped conf per fact].
    If facts is list[str] → [WIKI_FACT_CONFIDENCE_DEFAULT]*len(facts).
    Empty or None → [].
    """
    from .extract import clamp_confidence

    if not facts:
        return []

    # Check if any item is a dict (LLM-provided confidence)
    first = facts[0]
    if isinstance(first, dict):
        return [clamp_confidence(f.get("confidence")) for f in facts]

    # Plain list[str] — use default for all
    from . import config as _config
    return [_config.WIKI_FACT_CONFIDENCE_DEFAULT] * len(facts)
