# indexer.py
"""Wiki indexer v3: sessions -> validated pages -> embeddings -> SQLite index."""
import hashlib
import json
import os
import sqlite3
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiki_v2 import config
from wiki_v2.atomic import atomic_write
from wiki_v2.chunker import split_text
from wiki_v2.extract import _reset_llm_budget, extract_content, map_chunk_tags, reduce_chunk_tags
from wiki_v2.index_db import IndexDB
from wiki_v2.index_lock import IndexLock
from wiki_v2.logging_setup import logger, setup_logging
from wiki_v2.gateway import chat_available, embed, embed_api_available
from wiki_v2.pages import (
    find_merge_target,
    merge_content,
    parse_page,
    render_page,
)
from wiki_v2.quality import is_junk_chunk
from wiki_v2.session_status import is_session_finished, last_message_ts
from wiki_v2.slug import make_unique_slug, slugify

WIKI_PATH = str(config.WIKI_PATH)
STATE_DB = str(config.STATE_DB)
INDEX_DB = os.path.join(WIKI_PATH, ".index_v2.db")
LOCK_PATH = os.path.join(WIKI_PATH, ".index.lock")
STOP_FLAG = os.path.join(WIKI_PATH, ".stop_request")  # мягкий останов между сессиями
# Лимит сессий за прогон: env WIKI_MAX_SESSIONS_PER_RUN (ставят cron-обёртка
# и кнопка «Запустить» дашборда с полем «Страниц за запуск»), по умолчанию 5.
# fail-open: мусор в env → дефолт 5 (иначе битое значение роняло импорт).
try:
    MAX_SESSIONS_PER_RUN = max(1, int(os.environ.get("WIKI_MAX_SESSIONS_PER_RUN", "5")))
except (TypeError, ValueError):
    MAX_SESSIONS_PER_RUN = 5
MAX_SESSION_MESSAGES = int(os.environ.get("WIKI_MAX_SESSION_MESSAGES", "2000"))  # сессии крупнее — в список на анализ, не индексируем
CHUNK_LIMIT = 8000
IDLE_MINUTES = int(os.environ.get("WIKI_IDLE_MINUTES", "32"))
PENDING_HASH = "PENDING"
# Файл со списком «слишком больших» сессий для отдельного анализа (не индексируются)
OVERSIZED_LOG = os.path.join(WIKI_PATH, "oversized_sessions.log")
# Реестр «слишком больших» сессий, уже занесённых в OVERSIZED_LOG (session_id -> ts ISO),
# чтобы не логировать один и тот же session_id при каждом фоновом прогоне.
OVERSIZED_REGISTRY = os.path.join(WIKI_PATH, "oversized_sessions.registry.json")


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


def session_raw_text(session_id: str) -> str:
    """ВСЕ сообщения сессии целиком (role+content), БЕЗ [:500] и БЕЗ head/tail.

    Используется ТОЛЬКО для хэша (этап 1.2, АР-1): правка в середине длинной
    сессии должна менять хэш → переиндексация. Для LLM-текста есть
    get_session_text() (режет — это нормально, он для промпта).
    """
    conn = sqlite3.connect(STATE_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT role, content FROM messages
           WHERE session_id=? AND role IN ('user','assistant')
           ORDER BY timestamp ASC""", (session_id,)).fetchall()
    conn.close()
    parts = []
    for r in rows:
        role = "user" if r["role"] == "user" else "assistant"
        parts.append(f"{role}: {r['content'] or ''}")
    return "\n\n".join(parts)


def _streaming_sha256(text: str) -> str:
    """sha256 от текста ПОТОКОВО (куски 8KB) — не грузит гигабайты в RAM."""
    h = hashlib.sha256()
    for i in range(0, len(text), 8192):
        h.update(text[i:i + 8192].encode())
    return h.hexdigest()[:16]


def session_content_hash(session_id: str) -> str:
    """sha256 от ПОЛНОГО текста сессии (session_raw_text) — контроль изменений.

    Этап 1.2 (АР-1): раньше считался от get_session_text() (режет [:500] и
    CHUNK_LIMIT) → правка в середине длинной сессии НЕ меняла хэш. Теперь —
    от полного текста, потоково.
    """
    text = session_raw_text(session_id)
    return _streaming_sha256(text)


def page_candidates(db: IndexDB):
    """Candidates for merge: slug/title/topics parsed from existing md files."""
    out = []
    for p in db.all_pages():
        topics = []
        if os.path.exists(p["path"]):
            with open(p["path"], encoding="utf-8", errors="replace") as f:
                topics = parse_page(f.read()).get("key_topics", [])
        out.append({"slug": p["slug"], "title": p["title"], "key_topics": topics})
    return out


def embed_text_for_page(title: str, summary: str, topics: list):
    text = f"{title}\n{summary}\n{' '.join(topics)}"[:1000]
    vecs = embed([text], input_type="passage")
    return np.array(vecs[0], dtype=np.float32) if vecs else None


def embed_multivector(title: str, summary: str, topics: list) -> dict:
    """Multi-vector embedding: title, summary, and one per topic — single batch call.

    Returns ``{kind: vector}`` where *kind* is ``'title'``, ``'summary'``, or
    ``'tag:<topic>'``.  All texts are sent in ONE ``embed()`` call with
    ``input_type="passage"``.  Missing/None vectors get value ``None`` (key
    stays, does not raise).
    """
    texts = [title, f"{title}\n{summary}"]
    for t in topics:
        texts.append(f"{title}\n{t}")

    vecs = embed(texts, input_type="passage")

    result = {}
    if vecs is None or len(vecs) == 0:
        # embed failed entirely — return all keys with None
        result["title"] = None
        result["summary"] = None
        for t in topics:
            result[f"tag:{t}"] = None
        return result

    idx = 0
    def _next():
        nonlocal idx
        if idx < len(vecs):
            v = vecs[idx]
            idx += 1
            return np.array(v, dtype=np.float32)
        return None

    result["title"] = _next()
    result["summary"] = _next()
    for t in topics:
        result[f"tag:{t}"] = _next()

    return result


def embed_chunks(title: str, chunks: list, kind_prefix: str = "chunk") -> dict:
    """S2.5.8d: эмбеддинги на ЧАНКИ (kind='chunk').

    Каждый чанк эмбеддится отдельно (одним batch-вызовом embed), kind='chunk'.
    Мусорные чанки (is_junk_chunk) НЕ эмбеддятся, но индекс kind соответствует
    ИСХОДНОМУ индексу чанка в списке (чтобы 'chunk:N' — это тот же N, что в split_text).
    Возвращает {f"chunk:{i}": vector}. fail-open: на любой ошибке → {} (чанки без вектора).
    """
    if not chunks:
        return {}
    try:
        # Фильтруем мусорные чанки ДО эмбеддинга, сохраняя исходные индексы.
        embedding_targets = [(i, c) for i, c in enumerate(chunks) if not is_junk_chunk(c)]
        texts = [f"{title}\n{c}" for i, c in embedding_targets]
        vecs = embed(texts, input_type="passage")
        if vecs is None or len(vecs) == 0:
            return {}
        result = {}
        for (orig_i, _c), v in zip(embedding_targets, vecs[:len(embedding_targets)]):
            if v is not None:
                result[f"{kind_prefix}:{orig_i}"] = np.array(v, dtype=np.float32)
        return result
    except Exception:
        return {}


def _strip_wikilinks(links):
    """Убрать [[ ]] из ссылок Obsidian-паттерна."""
    out = []
    for l in (links or []):
        s = str(l).strip()
        if s.startswith("[[") and s.endswith("]]"):
            s = s[2:-2].strip()
        if s:
            out.append(s)
    return out


def _inc_extract(quality: str) -> None:
    """Метрика качества экстракции (fail-open)."""
    try:
        from wiki_v2 import metrics as _m
        if quality == "ok":
            _m.inc("extract_valid_total")
        else:
            _m.inc("extract_fallback_total")
    except Exception:
        pass


_SERVICE_TITLE_PREFIXES = (
    "we need to produce a title",
    "produce a title",
)

# S-R (2026-08-21): не превращать одноразовые task/тестовые сессии в страницы памяти.
# Эфемерные сессии («напиши тесты S4.x», «выполни файл-бриф task-4b», «создай модуль
# dashboard_...», «продолжи...») засоряли корпус (белые страницы на ~80-85%). Отсев
# идёт по маркерам в заголовке (после фолбэка на first_user). Флагом можно отключить.
SKIP_TRANSIENT = os.environ.get("WIKI_SKIP_TRANSIENT_SESSIONS", "True") in ("1", "true", "True")

_TRANSIENT_TASK_MARKERS = (
    "sandbox",
    "файл-бриф", "из файла-брифа", "прочитай бриф", "прочитай файл-бриф",
    "task-", "task_", "выполни задачу из файла-брифа", "выполни файл-бриф",
    "написать тест", "напиши тест", "написать pytest", "написать тесты", "напиши тест-файл",
    "тесты для s4", "тест-файл", "тест-файлы", "pytest",
    "документацию по 5 модулям", "документации по модулю",
    "создай новый модуль", "создать модуль", "создай модуль", "live-копия",
    "dashboard_charts", "dashboard_render", "dashboard_analysis", "dashboard_data",
    "dashboard_control", "dashboard_ts", "инкрементально", "добавь встроенный http",
    "ресерч", "исследовать", "исследовани", "этап 0.5",
    "обнови hermes", "run end-to-end", "продолжи", "продолжаем", "continue hermes",
    "bot chat", "конфиг эндпоинтов", "наличие дашборда", "group:", "группа kritik",
    "проверить наличие", "проверь работает ли у тебя wiki память",
)


def _is_junk_title(title: str) -> bool:
    """Служебные/шаблонные заголовки сессий, не несущие темы.

    Отсев консервативный (precision over recall): только явные системные
    шаблоны Hermes + пустые/дефолтные. Осмысленные заголовки НЕ трогаем.
    """
    t = (title or "").strip().lower()
    if t in ("", "untitled", "без названия"):
        return True
    return t.startswith(_SERVICE_TITLE_PREFIXES)


def _is_transient_task(title: str) -> bool:
    """True, если сессия — одноразовая task/тестовая/dev-задача (не память).

    Консервативно по маркерам в заголовке. «при загрузке компьютера...» (реальный
    баг пользователя) НЕ матчится — сохраняется.
    """
    t = (title or "").strip().lower()
    if t.strip() == "hi":
        return True
    t = t.replace(" ", "")
    for m in _TRANSIENT_TASK_MARKERS:
        if m.replace(" ", "") in t:
            return True
    return False


def process_session(db: IndexDB, session: dict) -> str:
    logger.info("[INDEX] начата обработка сессии id=%s title=%r", session["id"], (session.get("title") or "")[:40])
    text = get_session_text(session["id"])
    if not text.strip():
        return ""
    title = session["title"] or "Untitled"
    if _is_junk_title(title):
        # Безымянная/служебная сессия — пытаемся назвать по первой реплике пользователя
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

    # S-R (2026-08-21): одноразовая task/test-сессия → не создаём страницу памяти.
    # Сессия помечается обработанной выше ([processed]), но без page_slug — мусор
    # не накапливается. Fail-open: ошибка предиката → индекс (не терять).
    if SKIP_TRANSIENT:
        try:
            if _is_transient_task(title):
                print(f"[SKIP-TRANSIENT] {session['id']}: одноразовая task/test-сессия — не индексируем")
                return ""
        except Exception:
            pass  # fail-open: ошибка фильтра → индексируем (не ронять)

    # S2.5.9: map-reduce для длинных сессий (>8KB полного текста).
    # get_session_text() уже обрезает до 8KB head/tail, поэтому проверяем
    # session_raw_text() — полный текст без ограничений.
    long_chunks = None  # нарезанные чанки длинной сессии (для embed_chunks в S2.5.8d)
    try:
        # Бюджет LLM-вызовов — свежий на КАЖДУЮ сессию, иначе счётчик дрейфует между
        # сессиями (после 3 коротких по 2 вызова 4-я падает в fallback «из-за исчерпания»).
        _reset_llm_budget()
        full_text = session_raw_text(session["id"])
        if len(full_text) > 8000:
            # Приоритет у core-контента (summary/facts): extract идёт ПЕРВЫМ и получает
            # весь бюджет (6 вызовов). Теги чанков (MAP) имеют собственный бюджет
            # (map_chunk_tags сам делает _reset_llm_budget) — они улучшение поиска,
            # а не основа памяти. Раньше MAP съедал весь бюджет, и финальный extract
            # возвращал fallback по построению -> boilerplate-мусор у длинных страниц.
            long_chunks = split_text(full_text)
            content = extract_content(title, text)
            chunk_tags = map_chunk_tags(title, long_chunks)
            merged_topics = reduce_chunk_tags(title, chunk_tags)
            content["key_topics"] = merged_topics if merged_topics else content.get("key_topics", [])
        else:
            content = extract_content(title, text)
    except Exception as e:
        # fail-open: чанкинг/reduce упал → обычный extract (не хуже, чем было)
        print(f"[WARN] map-reduce failed for {title}: {e}, falling back to normal extraction")
        content = extract_content(title, text)
        long_chunks = None

    # Merge into existing topic page?
    # Приоритет: если у сессии есть связь со страницей — MERGE в неё, независимо от качества.
    target_slug = None
    ps = db.get_page_slug_for_session(session["id"])
    if ps:
        # Та же сессия уже обработана → MERGE в существующую страницу (не CREATE),
        # независимо от качества. Fallback при ok-экстракции поднимется до ok сам
        # (логика ниже: old.quality==fallback and content.quality==ok -> ok).
        target_slug = ps
    if target_slug is None:
        target_slug = find_merge_target(content["key_topics"], page_candidates(db), new_title=title)
    date_str = time.strftime("%Y-%m-%d", time.localtime(session.get("started_at") or time.time()))
    today = time.strftime("%Y-%m-%d")

    if target_slug:
        old_page = db.get_page(target_slug)
        with open(old_page["path"], encoding="utf-8", errors="replace") as f:
            old_md = f.read()
        old = parse_page(old_md)
        merged = merge_content(old, content)
        merged["summary"] = old.get("summary") or content["summary"]
        # quality: если экстракция теперь дала ok — поднимаем (fallback не вечен).
        # Если и новая экстракция fallback — остаётся fallback.
        if old.get("quality") == "fallback" and content.get("quality") == "ok":
            merged["quality"] = "ok"
        else:
            merged["quality"] = content["quality"] if old.get("quality") != "fallback" else "fallback"
        sources = sorted(set(old["sources"] + [session["id"]]))
        md = render_page(old_page["title"], merged, date_str=date_str,
                         updated=today, sources=sources)
        title_out = old_page["title"]
        path = old_page["path"]
        slug = target_slug
        # Two-phase commit: DB PENDING → atomic write+rename → finalize hash
        db.upsert_page(slug=slug, title=title_out, section="entities", path=path,
                       content_hash=PENDING_HASH,
                       summary=merged.get("summary", "")[:500],
                       quality=merged.get("quality", "ok"))
        with atomic_write(path, on_commit=None) as f:
            f.write(md)
        # Phase 2: compute real hash, finalize DB
        content_hash = hashlib.sha256(md.encode()).hexdigest()[:16]
        db.update_page_hash(slug, content_hash)
        _inc_extract(content.get("quality", "ok"))
        # Зона C (жёсткое прерывание): закрыть просвет между записью страницы и
        # mark_session_indexed в main. Помечаем сессию СРАЗУ после финализации
        # хэша (страница уже durable, двухфазный коммит закрыт) — kill во время
        # эмбеддинга и после не переиндексирует её повторно (иначе дубль-MERGE).
        db.mark_session_indexed(session["id"], page_slug=slug,
                                content_hash=session_content_hash(session["id"]))
        logger.info("[INDEX] страница сохранена slug=%s hash=%s (MERGE)", slug, content_hash)
        # S2.5.7: сохраняем граф связей (entities/links)
        db.save_entities(slug, merged.get("entities"), merged.get("concepts"))
        db.save_links(slug, _strip_wikilinks(merged.get("links")))

        # S4.12: write meta.json if enabled
        if config.WIKI_META_ENABLED:
            from wiki_v2.pages import write_meta
            write_meta(path, {
                "source": merged.get("sources", [])[0] if merged.get("sources") else "",
                "created": date_str,
                "updated": today,
                "tags": merged.get("key_topics", [])
            })

        print(f"[MERGE] {slug} <- {title}")
        # Эмбеддинг — ПОСЛЕ финализации хэша (двухфазный коммит закрыт).
        # Fail-open: если embed упал (NVIDIA лимиты) — страница уже сохранена,
        # сессия помечена обработанной; эмбеддинг добьём отдельным проходом.
        try:
            vecs = embed_multivector(title_out, merged.get("summary", ""),
                                     merged.get("key_topics", []))
            for kind, vec in vecs.items():
                if vec is not None:
                    db.set_embedding(slug, vec, kind=kind)
            # S4v1: эмбеддинг чанков от готовой md-страницы (kind='page_chunk:N')
            n_page_chunk_vecs = 0
            try:
                from wiki_v2.chunker import split_text as _split
                page_chunks = _split(md)
                chunk_vecs = embed_chunks(title_out, page_chunks, kind_prefix="page_chunk")
                for kind, vec in chunk_vecs.items():
                    if vec is not None:
                        db.set_embedding(slug, vec, kind=kind)
                n_page_chunk_vecs = len(chunk_vecs)
            except Exception:
                pass  # fail-open, страница уже сохранена
            # Счётчик отдельно: прежнее `len(vecs) + len(chunk_vecs) if long_chunks
            # else ...` при сбое внутреннего блока ДО присвоения chunk_vecs давало
            # NameError (длинная сессия), и честный лог «эмбеддинги записаны» терялся.
            logger.info("[EMBED] эмбеддинги записаны slug=%s (vecs=%d)",
                        slug, len(vecs) + n_page_chunk_vecs)
        except Exception as e:
            print(f"[WARN] embed failed for {slug}: {e} (page kept, embedding missing)")
            logger.warning("[EMBED] сбой эмбеддинга slug=%s: %s (страница сохранена, вектор добьётся позже)", slug, e)
        return slug
    else:
        existing = {p["slug"] for p in db.all_pages()}
        base = slugify(title) or "page"
        slug = make_unique_slug(base, existing, session_id=session["id"])
        target_dir = os.path.join(WIKI_PATH, "entities")
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, f"{slug}.md")
        title_out = title
        md = render_page(title, content, date_str=date_str,
                         updated=today, sources=[session["id"]])
        # Two-phase commit: DB PENDING → atomic write+rename → finalize hash
        db.upsert_page(slug=slug, title=title_out, section="entities", path=path,
                       content_hash=PENDING_HASH,
                       summary=content.get("summary", "")[:500],
                       quality=content["quality"])
        with atomic_write(path, on_commit=None) as f:
            f.write(md)
        # Phase 2: compute real hash, finalize DB
        content_hash = hashlib.sha256(md.encode()).hexdigest()[:16]
        db.update_page_hash(slug, content_hash)
        _inc_extract(content.get("quality", "ok"))
        # Зона C: помечаем сессию обработанной СРАЗУ после финализации хэша
        # (страница уже durable) — см. ветку MERGE выше.
        db.mark_session_indexed(session["id"], page_slug=slug,
                                content_hash=session_content_hash(session["id"]))
        logger.info("[INDEX] страница создана slug=%s hash=%s (CREATE)", slug, content_hash)
        # S2.5.7: сохраняем граф связей (entities/links)
        db.save_entities(slug, content.get("entities"), content.get("concepts"))
        db.save_links(slug, _strip_wikilinks(content.get("links")))
        # S4.12: write meta.json if enabled (ветка CREATE — как в MERGE)
        if config.WIKI_META_ENABLED:
            from wiki_v2.pages import write_meta
            write_meta(path, {
                "source": session["id"],
                "created": date_str,
                "updated": today,
                "tags": content.get("key_topics", [])
            })
        print(f"[CREATE] {slug} (quality={content['quality']})")
        # Эмбеддинг — ПОСЛЕ финализации хэша (двухфазный коммит закрыт).
        # Fail-open: если embed упал (NVIDIA лимиты) — страница уже сохранена,
        # сессия помечена обработанной; эмбеддинг добьём отдельным проходом.
        try:
            vecs = embed_multivector(title_out, content.get("summary", ""),
                                     content.get("key_topics", []))
            for kind, vec in vecs.items():
                if vec is not None:
                    db.set_embedding(slug, vec, kind=kind)
            # S4v1: эмбеддинг чанков от готовой md-страницы (kind='page_chunk:N')
            n_page_chunk_vecs = 0
            try:
                from wiki_v2.chunker import split_text as _split
                page_chunks = _split(md)
                chunk_vecs = embed_chunks(title_out, page_chunks, kind_prefix="page_chunk")
                for kind, vec in chunk_vecs.items():
                    if vec is not None:
                        db.set_embedding(slug, vec, kind=kind)
                n_page_chunk_vecs = len(chunk_vecs)
            except Exception:
                pass  # fail-open, страница уже сохранена
            logger.info("[EMBED] эмбеддинги записаны slug=%s (vecs=%d)",
                        slug, len(vecs) + n_page_chunk_vecs)
        except Exception as e:
            print(f"[WARN] embed failed for {slug}: {e} (page kept, embedding missing)")
            logger.warning("[EMBED] сбой эмбеддинга slug=%s: %s (страница сохранена, вектор добьётся позже)", slug, e)
        return slug


def _session_message_count(session_id: str) -> int:
    """Число сообщений сессии в state.db (user+assistant+tool). Ошибка → 0."""
    try:
        conn = sqlite3.connect(STATE_DB)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id=?", (session_id,)).fetchone()[0]
            return int(n)
        finally:
            conn.close()
    except Exception:
        return 0



def _load_oversized_registry() -> dict:
    """Прочитать реестр oversized-сессий (session_id -> ts ISO). fail-open: повреждённый файл -> {}."""
    try:
        if os.path.exists(OVERSIZED_REGISTRY):
            with open(OVERSIZED_REGISTRY, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
    except Exception:
        pass  # fail-open — не роняем индексатор из-за битого реестра
    return {}


def _save_oversized_registry(registry: dict) -> None:
    """Атомарно записать реестр oversized-сессий. fail-open."""
    try:
        with atomic_write(OVERSIZED_REGISTRY) as f:
            f.write(json.dumps(registry, ensure_ascii=False, indent=2))
    except Exception:
        pass  # fail-open — реестр не критичен (страховка от дублей лога)


def _is_oversized_known(session_id: str) -> bool:
    """Сессия уже занесена в лог oversized (есть в реестре). fail-open -> False."""
    try:
        return session_id in _load_oversized_registry()
    except Exception:
        return False


def _log_oversized(session_id: str, n_msgs: int) -> None:
    """Занести «слишком большую» сессию в отдельный лог для анализа."""
    # Страховка от дублей: если сессия уже в реестре -> не пишем повторно.
    if _is_oversized_known(session_id):
        return
    try:
        title = ""
        try:
            conn = sqlite3.connect(STATE_DB)
            try:
                r = conn.execute("SELECT title FROM sessions WHERE id=?", (session_id,)).fetchone()
                title = (r[0] or "") if r else ""
            finally:
                conn.close()
        except Exception:
            pass
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} | session={session_id} | msgs={n_msgs} | title={(title or '')[:60]!r}\n"
        with open(OVERSIZED_LOG, "a", encoding="utf-8") as f:
            f.write(line)
        logger.info("[INDEX] сессия %s пропущена: %d сообщений (>%d) — занесена в %s",
                    session_id, n_msgs, MAX_SESSION_MESSAGES, OVERSIZED_LOG)
        # Отметить в реестре, чтобы следующий прогон не логировал повторно.
        reg = _load_oversized_registry()
        reg[session_id] = time.strftime("%Y-%m-%d %H:%M:%S")
        _save_oversized_registry(reg)
    except Exception:
        pass  # fail-open



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
        # Лимит размера сессии: «мастодонты» (> MAX_SESSION_MESSAGES) не индексируем,
        # заносим в отдельный лог для анализа (иначе одна гигантская сессия застревает).
        n_msgs = _session_message_count(s["id"])
        if n_msgs > MAX_SESSION_MESSAGES:
            _log_oversized(s["id"], n_msgs)
            continue
        # hash-контроль: если сессия уже индексирована и содержимое не изменилось — пропускаем,
        # НО только если страница не в fallback. Fallback (сломанная экстракция) пробуем
        # переиндексировать при каждом фоновом проходе, пока качество не поднимется до ok.
        prev_hash = db.get_session_hash(s["id"])
        ps = db.get_page_slug_for_session(s["id"])
        if not prev_hash and not ps and db.is_session_indexed(s["id"]):
            # Строка в sessions есть, но без страницы и без хэша — сессия
            # обработана без результата (skip/транзиент): mark_session_indexed
            # пишет content_hash="" и page_slug="". Раньше пустой hash был
            # falsy, guard не срабатывал, и сессия гонялась заново при каждом
            # прогоне (мёртвый цикл повторной обработки, баг 2026-08-24).
            # Важно: свежая (ни разу не индексированная) сессия строки НЕ имеет
            # — is_session_indexed различает эти случаи.
            continue
        if prev_hash:
            cur_hash = session_content_hash(s["id"])
            if cur_hash == prev_hash:
                # не пропускаем, если страница сессии в fallback — пытаемся поднять качество
                if ps:
                    pg = db.get_page(ps)
                    if pg and pg.get("quality") != "fallback":
                        continue  # нормальная страница, не изменилась — пропуск
                    # fallback → не пропускаем, переиндексируем
                else:
                    continue  # нет страницы — сессия обработана без результата, пропуск
        out.append(s)
        if len(out) >= MAX_SESSIONS_PER_RUN:
            break
    return out


def cleanup_pending(db: IndexDB) -> int:
    """Защита от hard reboot: найти PENDING-записи и устранить сирот.

    Сирота = страница с content_hash='PENDING' — процесс прервался во время
    двухфазного коммита (после upsert PENDING, но до update_page_hash).

    Правило восстановления (НЕ удаления валидного контента!):
    - если финальный ``.md`` существует И его sha256 совпадает с хэшем,
      который ДОЛЖЕН был быть записан (реконструируем: содержимое файла) →
      это валидная завершённая запись (краш был между os.replace и
      update_page_hash) → просто финализируем хэш в БД, контент НЕ трогаем.
    - если финального ``.md`` нет (краш до os.replace) → удаляем .tmp +
      запись из БД (это настоящая сирота: файла нет, запись PENDING).
    - если финальный .md есть, но хэш не совпадает → удаляем и файл, и запись
      (содержимое непроверяемо — могло быть записано частично).

    Возвращает количество обработанных PENDING-записей.
    """
    pending = db.get_pending_pages()
    if not pending:
        return 0
    removed = 0
    for p in pending:
        slug = p["slug"]
        path = p["path"]
        final_md = os.path.exists(path)
        if final_md:
            try:
                with open(path, encoding="utf-8", newline="") as f:
                    content = f.read()
                real_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
            except OSError:
                real_hash = ""
            # Финальный .md существует и читается → это валидная запись
            # (краш был между os.replace и update_page_hash). Финализируем
            # хэш из реального содержимого — контент НЕ удаляем.
            if real_hash:
                db.update_page_hash(slug, real_hash)
                print(f"[CLEANUP] recovered valid page slug={slug} (hash finalized)")
                continue
            # .md есть, но не читается (пустой/битый) → непроверяемое содержимое
            try:
                os.remove(path)
            except OSError:
                pass
        # .md нет (или удалён) → удаляем .tmp + запись из БД
        tmp = path + ".tmp"
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        db.delete_page(slug)
        removed += 1
        print(f"[CLEANUP] removed orphan page slug={slug} (was PENDING)")
    return removed


def _clear_stop_flag() -> None:
    """Снять флаг .stop_request (одноразовая остановка). fail-open.

    P3 (2026-08-21): если флаг остался на диске (создан вручную, краш, ошибка
    stop_extraction до удаления) — он блокирует индексацию навсегда (main видит
    его и сразу break). Делаем флаг одноразовым: новый прогон стартует С ЧИСТОГО
    флага, а при срабатывании во время прогона флаг снимается сразу.
    """
    try:
        if os.path.exists(STOP_FLAG):
            os.remove(STOP_FLAG)
            logger.info("[STOP] флаг .stop_request снят (stop одноразовый)")
    except Exception:
        pass  # fail-open


def main(session_id: str = None) -> int:
    setup_logging()  # v3: включить файл-лог wiki/logs/wiki_v2.log (иначе логи мёртвые)
    print(f"=== Wiki Indexer v2 === {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lock = IndexLock(LOCK_PATH)
    if not lock.acquire():
        print("[LOCK] Другой процесс индексирует — пропускаем.")
        return 0
    try:
        # P3: новый прогон стартует с чистого .stop_request. Если флаг остался от
        # прошлого запуска (краш/руками/ошибка) — снимаем, чтобы он не блокировал
        # индексацию бесконечно. Дашборд пишет флаг ВО ВРЕМЯ прогона (см. цикл ниже).
        _clear_stop_flag()
        db = IndexDB(INDEX_DB)
        # Cleanup PENDING orphans (защита от hard reboot)
        cleanup_pending(db)
        # Этап 7: health-gate — embed API недоступен → прогон пропущен
        # (иначе страницы пишутся с vecs=0 и требуют embed_backfill).
        if not embed_api_available():
            print("[SKIP] Embed API (LM Studio) недоступен — прогон пропущен.")
            logger.warning("Health-gate: embed API недоступен, прогон пропущен")
            db.close()
            return 0
        # Chat health-gate (облако NVIDIA): проверяем доступность chat/extract
        # модели ДО экстракции. Если модель недоступна или в rate-limit/блокировке —
        # НЕ запускаем прогон: иначе каждая сессия уйдёт в fallback (fallback-страницы)
        # и продолжит долбить заблокированную модель. chat_available() кэшируется
        # на процесс (один probe). Fail-open: probe сбоит → пропуск прогона.
        from wiki_v2.gateway import chat_available
        if not chat_available():
            print("[SKIP] Chat-модель (Nemotron NVIDIA) недоступна/в rate-limit — прогон пропущен.")
            logger.warning("Chat health-gate: extract-модель недоступна, прогон пропущен")
            db.close()
            return 0
        sessions = _resolve_sessions(db, session_id)
        if not sessions:
            print("[OK] No new sessions.")
            db.close()
            return 0
        print(f"Found {len(sessions)} session(s)")
        processed = 0
        for s in sessions:
            # Мягкий останов: если запрошен стоп (файл-флаг), прекращаем
            # ПОСЛЕ завершения текущей сессии, не обрывая её посредине.
            if os.path.exists(STOP_FLAG):
                print("[STOP] Запрошена остановка — прекращаю после текущей сессии")
                _clear_stop_flag()  # одноразовый стоп: снимаем флаг сразу
                break
            try:
                slug = process_session(db, s)
                if slug:
                    processed += 1
                else:
                    # Skip-случай (транзиентная/без названия/oversized): сессия
                    # обработана без страницы — помечаем, чтобы фоновые прогоны
                    # не гоняли её по предикату заново. Успешные сессии уже
                    # помечены внутри process_session (закрытый просвет зоны C).
                    db.mark_session_indexed(s["id"], page_slug=slug, content_hash="")
            except Exception as e:
                # logger обязателен: print уходит в stdout=DEVNULL при запуске
                # из дашборда, и ошибка сессии становится невидимой (баг 2026-08-24).
                logger.error("[ERROR] session %s: %s", s["id"], e, exc_info=True)
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
