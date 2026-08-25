"""Wiki v3 — догонка эмбеддингов для страниц с vecs=0.

Проблема: при сбое embed-эндпоинта (`embed connection refused`) страница сохраняется
в БД с vecs=0 и остаётся без векторов -> не находится семантическим поиском.
Этот скрипт находит такие страницы и заново считает для них эмбеддинги
через единый эндпоинт embed из endpoints.yaml (сейчас — llamaserver/CPU).

Использование (живой каталог, откуда импортируется wiki_v2):
    python -m wiki_v2.embed_backfill --dry-run   # показать, что догнать (ничего не пишет)
    python -m wiki_v2.embed_backfill             # реальная догонка
    python -m wiki_v2.embed_backfill --limit 2   # догнать не больше N страниц

fail-open: страницу, которую не удалось эмбеддить (эндпоинт снова не ответил)
или в которой нет текста для эмбеддинга, пропускает и логирует — скрипт
не падает и продолжает со следующей.

Создан: 2026-08-17. Модель: та же embed-модель, что при индексации
(эндпоинт берётся из endpoints.yaml через wiki_v2.gateway).
"""
from __future__ import annotations

import argparse
import os
import sys

# Живой каталог может быть не в sys.path при запуске как `python -m` из другого места.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Гарантируем, что пакет wiki_v2 находится в sys.path (когда запускали из родителя live-каталога).
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from wiki_v2 import config  # noqa: E402
from wiki_v2.index_db import IndexDB  # noqa: E402
from wiki_v2.logging_setup import logger  # noqa: E402
from wiki_v2.gateway import embed  # noqa: E402
from wiki_v2.pages import parse_page  # noqa: E402

INDEX_DB = str(config.WIKI_PATH / ".index_v2.db")


def _read_key_topics(slug: str) -> list:
    """Прочитать key_topics из md-файла страницы, если он есть."""
    try:
        page = IndexDB(INDEX_DB).get_page(slug)
    except Exception:
        return []
    if not page:
        return []
    path = page.get("path") or ""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            md = f.read()
        return parse_page(md).get("key_topics", []) or []
    except Exception as e:
        logger.warning("не смог прочитать key_topics для slug=%s: %s", slug, e)
        return []


def find_pages_without_embeddings(db: IndexDB) -> list:
    """Список slug, у которых нет core-эмбеддинга (title или summary)."""
    conn = db.conn
    rows = conn.execute(
        """
        SELECT p.slug, p.title, p.summary, p.path
        FROM pages p
        LEFT JOIN embeddings e ON e.slug = p.slug AND e.kind IN ('title', 'summary')
        WHERE e.slug IS NULL
        """
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "slug": r["slug"],
            "title": r["title"],
            "summary": r["summary"],
            "path": r["path"],
        })
    return out


def embed_one(db: IndexDB, page: dict) -> tuple[bool, str]:
    """Применить догонку для одной страницы. Возвращает (успех, сообщение)."""
    slug = page["slug"]
    title = (page.get("title") or "").strip()
    summary = (page.get("summary") or "").strip()
    topics = _read_key_topics(slug)

    if not title and not summary and not topics:
        return False, "нет текста для эмбеддинга"

    texts = []
    if title:
        texts.append(title)
    texts.append(f"{title}\n{summary}".strip() if title else summary)
    for t in topics:
        texts.append(f"{title}\n{t}".strip() if title else t)

    vecs = embed(texts, input_type="passage")
    if vecs is None or len(vecs) == 0:
        return False, "эмбеддер вернул None (LM Studio недоступен?)"

    idx = 0
    results = {}
    if title:
        results["title"] = vecs[idx]; idx += 1
    if idx < len(vecs):
        results["summary"] = vecs[idx]; idx += 1
    for t in topics:
        if idx < len(vecs):
            results[f"tag:{t}"] = vecs[idx]; idx += 1

    model_id = db.get_embed_model_ids()
    model_id = next(iter(model_id)) if model_id else config.LMSTUDIO_MODEL
    import numpy as np
    written = 0
    for kind, vec in results.items():
        if vec is not None:
            db.set_embedding(slug, np.array(vec, dtype=np.float32), kind=kind, model_id=model_id)
            written += 1
    if written:
        return True, f"записано {written} векторов (title/summary/tags)"
    return False, "нечего записать (все None)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Догонка эмбеддингов для страниц с vecs=0")
    ap.add_argument("--dry-run", action="store_true",
                    help="показать, какие страницы нужно догнать, но НЕ писать в БД")
    ap.add_argument("--limit", type=int, default=0,
                    help="догнать не больше N страниц (0 = все)")
    args = ap.parse_args(argv)

    db = IndexDB(INDEX_DB)
    pages = find_pages_without_embeddings(db)
    if not pages:
        print("Нет страниц без эмбеддингов. Все ок.")
        return 0

    print(f"Найдено страниц без core-эмбеддингов: {len(pages)}\n")
    for p in pages:
        print(f"  {p['slug']}\n    title={p['title'][:60]}")
    print()

    if args.dry_run:
        print("DRY-RUN: ничего не пишу в БД.")
        return 0

    todo = pages if not args.limit else pages[:args.limit]
    ok = fail = 0
    for p in todo:
        success, msg = embed_one(db, p)
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
