"""Автоуборка дублей и мусорных страниц в wiki_v2.

Логика:
1. Группирует страницы по «корню» имени (без суффиксов -2, -3, -YYYYMMDD).
2. В каждой группе выбирает «лучшую»:
   - основная (без суффикса) с quality=ok — идеал
   - иначе любая с quality=ok
   - иначе основная (без суффикса)
   - иначе первая (оставляем хоть что-то)
3. Удаляет остальные: файл .md + запись в БД + эмбеддинг.
4. Fallback-страницы (quality=fallback) удаляются ВСЕГДА, если в группе
   есть страница получше.

Безопасность: печатает, что удаляет; сухой прогон с --dry-run.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from wiki_v2 import config
    WIKI_PATH = str(config.WIKI_PATH)
except Exception:
    WIKI_PATH = os.environ.get("WIKI_PATH", "/opt/data/wiki")

from wiki_v2.index_db import IndexDB

INDEX_DB = os.path.join(WIKI_PATH, ".index_v2.db")


def root_slug(slug: str) -> str:
    """Корень имени: убираем -2, -3, -20260804 и т.д."""
    base = re.sub(r"-\d{8}$", "", slug)
    base = re.sub(r"-\d+$", "", base)
    return base


def pick_best(group: list) -> str:
    """Выбираем slug для сохранения. group: list[dict] с slug, quality."""
    # 1) основная (без суффикса) с quality=ok
    for p in group:
        if p["slug"] == root_slug(p["slug"]) and p.get("quality") == "ok":
            return p["slug"]
    # 2) любая с quality=ok
    for p in group:
        if p.get("quality") == "ok":
            return p["slug"]
    # 3) основная (без суффикса), даже если fallback
    for p in group:
        if p["slug"] == root_slug(p["slug"]):
            return p["slug"]
    # 4) первая
    return group[0]["slug"]


def cleanup(dry_run: bool = True):
    db = IndexDB(INDEX_DB)
    pages = db.all_pages()

    # Группируем
    groups = {}
    for p in pages:
        base = root_slug(p["slug"])
        # «untitled» — это РАЗНЫЕ безымянные сессии, а не дубли.
        # Группировать их по имени нельзя — потеряем разговоры.
        if base == "untitled":
            continue
        groups.setdefault(base, []).append(p)

    to_delete = []
    for base, group in groups.items():
        if len(group) < 2:
            continue
        # Безопасность: удаляем только если у всех страниц группы ОДИН source
        # (это дубли одной сессии). Если source разные — это разные разговоры
        # с похожим именем, их не трогаем.
        sources = set()
        for p in group:
            path = p.get("path", "")
            src = ""
            if path and os.path.exists(path):
                try:
                    m = re.search(r"sources: \[([^\]]+)\]",
                                  open(path, encoding="utf-8").read())
                    src = m.group(1) if m else ""
                except (OSError, UnicodeDecodeError):
                    pass
            if src:  # пустой source не считаем — просто нет данных
                sources.add(src)
        if len(sources) > 1:
            print(f"  [SKIP] {base}: разные source ({len(sources)}), не дубли — пропуск")
            continue
        best = pick_best(group)
        for p in group:
            if p["slug"] != best:
                to_delete.append(p)

    print(f"Найдено групп с дублями: {len([g for g in groups.values() if len(g) >= 2])}")
    print(f"К удалению: {len(to_delete)} страниц")
    for p in to_delete:
        print(f"  [DEL] {p['slug']} (quality={p.get('quality')}) path={p.get('path')}")

    if dry_run:
        print("\n[DRY RUN] Ничего не удалено. Запусти с --apply для реального удаления.")
        db.close()
        return

    # Реальное удаление
    deleted_files = 0
    for p in to_delete:
        path = p.get("path", "")
        if path and os.path.exists(path):
            try:
                os.remove(path)
                deleted_files += 1
            except OSError as e:
                print(f"  [WARN] не удалось удалить файл {path}: {e}")
        db.delete_page(p["slug"])
        print(f"  [OK] удалена {p['slug']}")

    db.close()
    print(f"\nГотово: удалено {len(to_delete)} страниц, файлов удалено {deleted_files}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Уборка дублей вики")
    parser.add_argument("--apply", action="store_true",
                        help="Реально удалить (без него — сухой прогон)")
    args = parser.parse_args()
    cleanup(dry_run=not args.apply)
