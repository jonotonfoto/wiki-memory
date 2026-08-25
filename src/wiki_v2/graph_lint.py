"""Wiki graph lint: аудит целостности графа связей (S2.5.7)."""

import sys


def lint_graph(db) -> dict:
    """Проверить целостность графа связей. Возвращает dict отчёта. Никогда не бросает."""
    try:
        entities_dict, links_dict = db.get_graph()
        pages = {p["slug"] for p in db.all_pages()}

        # broken_links: ссылки на несуществующую страницу
        broken = sorted(
            f"{a}->{b}" for a, targets in links_dict.items() for b in targets if b not in pages
        )

        # orphan: страницы без ВХОДЯЩИХ связей (не цель ни одной ссылки)
        in_degree = set()
        for targets in links_dict.values():
            in_degree.update(targets)
        orphan = sorted(pages - in_degree)

        # not_in_index: страницы в графе, которых нет в индексе
        not_in_index = sorted(set(links_dict.keys()) - pages)

        return {
            "total_pages": len(pages),
            "total_links": sum(len(v) for v in links_dict.values()) // 2,
            "broken_links": broken,
            "orphan": orphan,
            "not_in_index": not_in_index,
        }
    except Exception:
        return {"total_pages": 0, "total_links": 0, "broken_links": [], "orphan": [], "not_in_index": []}


def main():
    import os

    from wiki_v2.index_db import IndexDB
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".index_v2.db")
    if len(sys.argv) > 1 and sys.argv[1] == "--fix":
        print("--fix: пока только отчёт (применение — по подтверждению пользователя).")
    db = IndexDB(db_path)
    report = lint_graph(db)
    print(f"Страниц: {report['total_pages']}, связей: {report['total_links']}")
    print(f"Orphan (нет входящих ссылок): {report['orphan']}")
    print(f"Битые ссылки: {report['broken_links']}")
    print(f"Не в индексе: {report['not_in_index']}")
    db.close()


if __name__ == "__main__":
    main()
