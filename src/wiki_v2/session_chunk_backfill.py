"""Wiki v3 — догонка session_chunk:N: чанки СЫРОГО ТЕКСТА сессий.

Зачем (2026-08-25): page_chunk:N — чанки СЖАТОЙ md-страницы (Темы/Решения/Факты).
Богатый нарратив переписки (сценарии, развёрнутые ответы) в них не попадает.
Этот скрипт режет session_raw_text() каждой сессии и эмбеддит как
kind='session_chunk:N' с slug=СТРАНИЦА (привязка к странице через таблицу
sessions). Для страницы берётся ОДНА — самая длинная — сессия (детерминизм:
повторный запуск перезаписывает те же kind).

Лимиты: --max-chunks на сессию; при превышении индексы выбираются РАВНОМЕРНО
по всему тексту (начало/середина/конец представлены, исходный индекс N
сохраняется → плагин нарезает тот же span байт-в-байт).

Использование (живой каталог):
    python -m wiki_v2.session_chunk_backfill --dry-run
    python -m wiki_v2.session_chunk_backfill [--limit 5] [--max-chunks 48]
    python -m wiki_v2.session_chunk_backfill --missing-only  # только страницы
                                                             # без session_chunk

fail-open: сессия без текста/без страницы/сбой эмбеда — пропуск с логом.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_PARENT = os.path.dirname(_HERE)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from wiki_v2.chunker import split_text_spans  # noqa: E402
from wiki_v2.index_db import IndexDB  # noqa: E402
from wiki_v2.indexer import session_raw_text  # noqa: E402
from wiki_v2.logging_setup import logger  # noqa: E402

# Суб-батч эмбеддингов: сколько текстов в ОДИН embed()-вызов. Батч больше дефолтного
# 8 не укладывался в клиентский таймаут 60с на CPU (проверено 2026-08-26: батч 24
# на странице 270KB ~= 531 токен/спан → таймаут и пустой ответ). Зеркалит indexer.
try:
    EMBED_SUBBATCH = max(1, int(os.environ.get("WIKI_EMBED_SUBBATCH", "8")))
except (TypeError, ValueError):
    EMBED_SUBBATCH = 8


def _primary_sessions(db: IndexDB) -> dict:
    """{page_slug: самая длинная session_id} — детерминированный выбор."""
    groups = {}
    for sid, slug in db.conn.execute(
        "SELECT session_id, page_slug FROM sessions WHERE page_slug != ''"
    ):
        groups.setdefault(slug, []).append(sid)
    return {slug: max(sids, key=lambda s: len(session_raw_text(s)))
            for slug, sids in groups.items()}


def _even_sample(total: int, cap: int) -> list:
    """Равномерная выборка indexов из [0,total): начало+шаг+конец."""
    if total <= cap:
        return list(range(total))
    step = (total - 1) / (cap - 1)
    idxs = sorted({int(round(i * step)) for i in range(cap)})
    if idxs[0] != 0:
        idxs[0] = 0
    if idxs[-1] != total - 1:
        idxs[-1] = total - 1
    return idxs


def backfill(limit=None, max_chunks=48, min_raw=2000, dry=False,
             missing_only=False):
    from wiki_v2 import config
    db = IndexDB(str(config.WIKI_PATH / ".index_v2.db"))
    try:
        primary = _primary_sessions(db)
        slugs = sorted(primary)
        # missing_only (2026-08-26): пропускать страницы, у которых нарративные
        # векторы session_chunk:* УЖЕ есть (повторный прогон не переэмбеддит
        # залитое — экономит время и держит результат детерминированным).
        if missing_only:
            have = {r[0] for r in db.conn.execute(
                "SELECT DISTINCT slug FROM embeddings WHERE kind LIKE 'session_chunk:%'")}
            slugs = [s for s in slugs if s not in have]
        if limit:
            slugs = slugs[:limit]
        done = skipped = 0
        for slug in slugs:
            sid = primary[slug]
            raw = session_raw_text(sid)
            if len(raw) < min_raw:
                skipped += 1
                continue
            spans = split_text_spans(raw)
            pick = _even_sample(len(spans), max_chunks)
            texts = [raw[spans[i][0]:spans[i][1]].strip() for i in pick]
            pairs = [(i, t) for i, t in zip(pick, texts) if t]
            if dry:
                print(f"  [DRY] {slug[:40]}: {len(pairs)} чанков (raw {len(raw)//1024}KB)")
                continue
            from wiki_v2.gateway import embed
            written = 0
            for b0 in range(0, len(pairs), EMBED_SUBBATCH):
                batch = pairs[b0:b0 + EMBED_SUBBATCH]
                vecs = embed([t for _, t in batch], input_type="passage")
                if not vecs:
                    # 2026-08-26: молчаливый break маскировал недоступность
                    # embed-бэкенда — прогон печатал «записано 0» без причины.
                    logger.warning(
                        "session_chunk_backfill: embed вернул пусто "
                        "(бэкенд недоступен/таймаут?) slug=%s batch=%s",
                        slug, b0 // EMBED_SUBBATCH)
                    continue
                for (i, _t), v in zip(batch, vecs):
                    if v is not None:
                        db.set_embedding(slug, np.asarray(v, dtype=np.float32),
                                         kind=f"session_chunk:{i}")
                        written += 1
            done += 1
            print(f"  [OK] {slug[:44]}: записано {written} session_chunk (sid={sid})")
        print(f"Итог: обработано {done}, пропущено {skipped} (из {len(slugs)})")
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-chunks", type=int, default=48)
    ap.add_argument("--min-raw", type=int, default=2000)
    ap.add_argument("--missing-only", action="store_true",
                    help="только страницы без session_chunk-векторов")
    a = ap.parse_args()
    backfill(limit=a.limit, max_chunks=a.max_chunks, min_raw=a.min_raw,
             dry=a.dry_run, missing_only=a.missing_only)


if __name__ == "__main__":
    main()
