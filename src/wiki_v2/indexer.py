# indexer.py
"""Wiki indexer v2: sessions -> validated pages -> embeddings -> SQLite index."""
import hashlib
import os
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiki_v2.extract import extract_content
from wiki_v2.facts_bridge import queue_facts
from wiki_v2.index_db import IndexDB
from wiki_v2.index_lock import IndexLock
from wiki_v2.nvidia_client import embed
from wiki_v2.pages import (
    find_merge_target,
    merge_content,
    parse_page,
    render_page,
)
from wiki_v2.session_status import is_session_finished, last_message_ts
from wiki_v2.slug import make_unique_slug, slugify

from wiki_v2 import config

WIKI_PATH = str(config.WIKI_PATH)
STATE_DB = str(config.STATE_DB)
INDEX_DB = os.path.join(WIKI_PATH, ".index_v2.db")
LOCK_PATH = os.path.join(WIKI_PATH, ".index.lock")
MAX_SESSIONS_PER_RUN = 5
CHUNK_LIMIT = 8000
IDLE_MINUTES = int(os.environ.get("WIKI_IDLE_MINUTES", "32"))


def get_unindexed_sessions(db: IndexDB, limit=MAX_SESSIONS_PER_RUN, include_indexed=False):
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT DISTINCT s.id, s.title, s.started_at
           FROM sessions s JOIN messages m ON m.session_id = s.id
           WHERE m.role IN ('user','assistant')
           ORDER BY s.started_at DESC LIMIT 200""").fetchall()
    conn.close()
    if include_indexed:
        return [dict(r) for r in rows][:limit]
    return [dict(r) for r in rows if not db.is_session_indexed(r["id"])][:limit]


def get_session_text(session_id: str) -> str:
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT role, content FROM messages
           WHERE session_id=? AND role IN ('user','assistant')
           ORDER BY timestamp ASC""", (session_id,)).fetchall()
    conn.close()
    parts = []
    for r in rows:
        role = "👤" if r["role"] == "user" else "🤖"
        parts.append(f"{role}: {(r['content'] or '')[:500]}")
    text = "\n\n".join(parts)
    # Не теряем конец разговора (выводы/решения обычно в конце):
    # если превышен лимит, берём начало (70%) + конец (30%)
    if len(text) > CHUNK_LIMIT:
        head = text[: int(CHUNK_LIMIT * 0.7)]
        tail = text[-int(CHUNK_LIMIT * 0.3):]
        text = head + "\n\n[...пропущена середина...]\n\n" + tail
    return text


def session_content_hash(session_id: str) -> str:
    """sha256 от текста сессии — используется для контроля изменений."""
    text = get_session_text(session_id)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def page_candidates(db: IndexDB):
    """Candidates for merge: slug/title/topics parsed from existing md files."""
    out = []
    for p in db.all_pages():
        topics = []
        if os.path.exists(p["path"]):
            with open(p["path"]) as f:
                topics = parse_page(f.read()).get("key_topics", [])
        out.append({"slug": p["slug"], "title": p["title"], "key_topics": topics})
    return out


def embed_text_for_page(title: str, summary: str, topics: list):
    text = f"{title}\n{summary}\n{' '.join(topics)}"[:1000]
    vecs = embed([text], input_type="passage")
    return np.array(vecs[0], dtype=np.float32) if vecs else None


def process_session(db: IndexDB, session: dict) -> str:
    text = get_session_text(session["id"])
    if not text.strip():
        return ""
    title = session["title"] or "Untitled"
    if title.strip().lower() in ("untitled", "", "без названия"):
        # Безымянная сессия — пытаемся назвать по первой реплике пользователя
        first_user = ""
        for line in text.split("\n"):
            if line.startswith("👤"):
                first_user = line.lstrip("👤: ").strip()[:80]
                break
        if first_user:
            title = first_user
        else:
            print(f"[SKIP] {session['id']}: сессия без названия и без реплик — пропуск")
            return ""
    content = extract_content(title, text)

    # Merge into existing topic page?
    target_slug = find_merge_target(content["key_topics"], page_candidates(db))
    date_str = time.strftime("%Y-%m-%d", time.localtime(session.get("started_at") or time.time()))
    today = time.strftime("%Y-%m-%d")

    if target_slug:
        old_page = db.get_page(target_slug)
        with open(old_page["path"]) as f:
            old_md = f.read()
        old = parse_page(old_md)
        merged = merge_content(old, content)
        merged["summary"] = old.get("summary") or content["summary"]
        merged["quality"] = content["quality"] if old.get("quality") != "fallback" else "fallback"
        sources = sorted(set(old["sources"] + [session["id"]]))
        md = render_page(old_page["title"], merged, date_str=date_str,
                         updated=today, sources=sources)
        with open(old_page["path"], "w") as f:
            f.write(md)
        slug, title_out, path = target_slug, old_page["title"], old_page["path"]
        print(f"[MERGE] {slug} <- {title}")
    else:
        existing = {p["slug"] for p in db.all_pages()}
        base = slugify(title) or "page"
        slug = make_unique_slug(base, existing, session_id=session["id"])
        target_dir = os.path.join(WIKI_PATH, "entities")
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{slug}.md")
        md = render_page(title, content, date_str=date_str,
                         updated=today, sources=[session["id"]])
        with open(path, "w") as f:
            f.write(md)
        title_out = title
        print(f"[CREATE] {slug} (quality={content['quality']})")

    with open(path) as f:
        content_hash = hashlib.sha256(f.read().encode()).hexdigest()[:16]
    db.upsert_page(slug=slug, title=title_out, section="entities", path=path,
                   content_hash=content_hash,
                   summary=content.get("summary", "")[:500],
                   quality=content["quality"])
    vec = embed_text_for_page(title_out, content.get("summary", ""),
                              content.get("key_topics", []))
    if vec is not None:
        db.set_embedding(slug, vec)
    queue_facts(session["id"], title_out, content)
    return slug


def _resolve_sessions(db: IndexDB, session_id: str = None):
    """Выбор сессий для индексации.

    - session_id задан → только эта сессия (режим одной сессии, хук /new):
      индексируем сразу (сессия уже закрыта /new), пропуская фильтр завершённости.
    - иначе (фоновая/cron) → до MAX_SESSIONS_PER_RUN сессий, которые:
      (1) завершены (простой >= IDLE_MINUTES),
      (2) содержимое ИЗМЕНИЛОСЬ с прошлой индексации (hash-контроль) или не индексированы.
    """
    if session_id:
        # Проверяем, что сессия существует в state.db и у неё есть сообщения
        if not os.path.exists(STATE_DB):
            return []
        conn = sqlite3.connect(STATE_DB)
        try:
            row = conn.execute(
                "SELECT id, title, started_at FROM sessions WHERE id=?",
                (session_id,)).fetchone()
        finally:
            conn.close()
        if not row:
            return []
        return [{"id": row[0], "title": row[1], "started_at": row[2]}]

    candidates = get_unindexed_sessions(db, limit=200, include_indexed=True)
    out = []
    for s in candidates:
        last_ts = last_message_ts(STATE_DB, s["id"])
        if not is_session_finished(last_ts, idle_minutes=IDLE_MINUTES):
            continue  # активная сессия — пропускаем
        # hash-контроль: если сессия уже индексирована и содержимое не изменилось — пропускаем
        prev_hash = db.get_session_hash(s["id"])
        if prev_hash:
            cur_hash = session_content_hash(s["id"])
            if cur_hash == prev_hash:
                continue  # не изменилась
        out.append(s)
        if len(out) >= MAX_SESSIONS_PER_RUN:
            break
    return out


def main(session_id: str = None) -> int:
    print(f"=== Wiki Indexer v2 === {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lock = IndexLock(LOCK_PATH)
    if not lock.acquire():
        print("[LOCK] Другой процесс индексирует — пропускаем.")
        return 0
    try:
        db = IndexDB(INDEX_DB)
        sessions = _resolve_sessions(db, session_id)
        if not sessions:
            print("[OK] No new sessions.")
            db.close()
            return 0
        print(f"Found {len(sessions)} session(s)")
        processed = 0
        for s in sessions:
            try:
                slug = process_session(db, s)
                # hash пишется ПОСЛЕ успешной записи карточки + эмбеддинга:
                # прерванная индексация не «застревает» как обработанная.
                chash = session_content_hash(s["id"]) if slug else ""
                db.mark_session_indexed(s["id"], page_slug=slug, content_hash=chash)
                processed += 1
            except Exception as e:
                print(f"[ERROR] session {s['id']}: {e}")
        total = len(db.all_pages())
        db.close()
        print(f"[DONE] {processed} processed, {total} pages total")
        return processed
    finally:
        lock.release()


if __name__ == "__main__":
    import argparse
    _p = argparse.ArgumentParser(description="Wiki indexer v2")
    _p.add_argument("--session", dest="session_id", default=None,
                    help="Индексировать только эту сессию (режим одной сессии)")
    _args = _p.parse_args()
    main(session_id=_args.session_id)
