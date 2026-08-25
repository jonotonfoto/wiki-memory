"""Wiki v3 — догонка page_chunk:N для существующих страниц.

Проблема: до 4в.1 индексатор не эмбеддил чанки готовой md-страницы (kind='page_chunk:N').
Поиск (4д.1) и плагин wiki-context (4в.3) уже ищут page_chunk:, поэтому для страниц,
созданных ДО 4в.1, «релевантный чанк» не находился. Этот скрипт для всех страниц, у
которых ещё НЕТ page_chunk:N, читает готовый .md, режет split_text(body) и эмбеддит с
kind='page_chunk:N' в index_db.

ВАЖНО (консистентность индексов): чанки режутся из ПОЛНОГО md-файла (включая frontmatter)
— ТОТ ЖЕ источник, что у indexer.embed_chunks (4в.1) и плагина wiki-context (4в.3),
которые тоже вызывают split_text(полный_md). Поэтому номер N в 'page_chunk:N' совпадает
1:1 с индексом чанка у плагина при подстановке текста по вектору.

Использование (живой каталог, откуда импортируется wiki_v2):
    python -m wiki_v2.page_chunk_backfill --dry-run   # показать, какие страницы догнать (ничего не пишет)
    python -m wiki_v2.page_chunk_backfill             # реальная догонка
    python -m wiki_v2.page_chunk_backfill --limit 2   # догнать не больше N страниц

fail-open: страница, которую не удалось эмбеддить (LM Studio не ответил) или у которой
нет файла/текста, пропускается и логируется — скрипт не падает и продолжает.

Создан: 2026-08-18 (подэтап 4в.4). Модель: та же embed-модель, что и при индексации.
"""
from __future__ import annotations

import argparse
import os
import sys

# Живой каталог может быть не в sys.path при запуске как `python -m` из другого места.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

# Ручной запуск `python -m` на Windows-консоли (cp1251) падал на юникод-символах
# в заголовках (стрелки/эмодзи). Переводим вывод в UTF-8 с replace — fail-open.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from wiki_v2 import config  # noqa: E402
from wiki_v2.chunker import split_text  # noqa: E402
from wiki_v2.index_db import IndexDB  # noqa: E402
from wiki_v2.indexer import embed_chunks  # noqa: E402
from wiki_v2.logging_setup import logger  # noqa: E402

INDEX_DB = str(config.WIKI_PATH / ".index_v2.db")


def find_pages_missing_page_chunk(db: IndexDB) -> list:
    """Список страниц (slug/title/path), у которых ещё НЕТ page_chunk:N. Дедуп по slug."""
    rows = db.conn.execute(
        """
        SELECT p.slug, p.title, p.path
        FROM pages p
        LEFT JOIN embeddings e ON e.slug = p.slug AND e.kind LIKE 'page_chunk:%'
        WHERE e.slug IS NULL
        """
    ).fetchall()
    seen = set()
    out = []
    for r in rows:
        slug = r["slug"]
        if slug in seen:
            continue
        seen.add(slug)
        out.append({"slug": slug, "title": r["title"], "path": r["path"]})
    return out


def backfill_one(db: IndexDB, page: dict) -> tuple[bool, str]:
    """Догнать page_chunk:N для одной страницы. Возвращает (успех, сообщение)."""
    slug = page["slug"]
    title = (page.get("title") or "").strip()
    path = page.get("path") or ""
    if not path or not os.path.exists(path):
        return False, "нет файла md"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            md = f.read()
    except Exception as e:
        logger.warning("slug=%s не прочитался md: %s", slug, e)
        return False, f"не прочитался md: {e}"
    if not md.strip():
        return False, "файл пуст (нет текста)"

    # Режем ПОЛНЫЙ md (как indexer 4в.1 и плагин 4в.3) — индексы 1:1 совпадают.
    chunks = split_text(md)
    if not chunks:
        return False, "split_text пуст"

    try:
        vecs = embed_chunks(title, chunks, kind_prefix="page_chunk")
    except Exception as e:
        logger.warning("slug=%s embed_chunks упал: %s", slug, e)
        return False, f"embed_chunks упал: {e}"
    if not vecs:
        return False, "нет векторов (LM Studio недоступен / все чанки мусорные)"

    model_id = db.get_embed_model_ids()
    model_id = next(iter(model_id)) if model_id else config.LMSTUDIO_MODEL
    written = 0
    for kind, vec in vecs.items():
        if vec is None:
            continue
        try:
            db.set_embedding(slug, vec, kind=kind, model_id=model_id)
            written += 1
        except Exception as e:
            logger.warning("slug=%s kind=%s не записан: %s", slug, kind, e)
    if written:
        return True, f"записано {written} page_chunk"
    return False, "нечего записать (все None)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Догонка page_chunk:N для страниц без чанк-эмбеддингов")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать, какие страницы догнать, но НЕ писать в БД")
    ap.add_argument("--limit", type=int, default=0,
                    help="догнать не больше N страниц (0 = все)")
    args = ap.parse_args(argv)

    # Резолвим путь при вызове (не модульную константу), чтобы тесты с config.reload()
    # изолированно писали в tmp-WIKI_PATH, а не в реальную базу.
    db = IndexDB(str(config.WIKI_PATH / ".index_v2.db"))
    pages = find_pages_missing_page_chunk(db)
    if not pages:
        print("Нет страниц без page_chunk:N. Все ок.")
        return 0

    print(f"Найдено страниц без page_chunk:N: {len(pages)}\n")
    for p in pages:
        print(f"  {p['slug']}\n    title={(p.get('title') or '')[:60]}")
    print()

    if args.dry_run:
        print("DRY-RUN: ничего не пишу в БД.")
        return 0

    todo = pages if not args.limit else pages[:args.limit]
    ok = fail = 0
    for p in todo:
        success, msg = backfill_one(db, p)
        if success:
            ok += 1
            print(f"  [OK]      {p['slug']}: {msg}")
        else:
            fail += 1
            print(f"  [ПРОПУСК] {p['slug']}: {msg}")
    print(f"\nИтог: догнано {ok}, пропущено {fail} (из {len(todo)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
