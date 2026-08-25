"""Wiki memory lint: аудит фактов на противоречия и устаревание (S4.4)."""

import json
import os
import sqlite3
import sys
import time


def _now_epoch() -> float:
    return time.time()


def lint_facts(db_path: str) -> dict:
    """Аудит БД (.index_v2.db). Возвращает dict отчёта."""
    result = {
        "contested": [],
        "stale": [],
        "total_pages": 0,
        "checked_at": _now_epoch()
    }

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Проверка наличия колонок (fail-open approach)
        cursor.execute("PRAGMA table_info(pages)")
        cols = {r["name"] for r in cursor.fetchall()}

        result["total_pages"] = len(conn.execute("SELECT 1 FROM pages").fetchall())

        # 1. Contested facts (противоречия)
        if "contested" in cols:
            cursor.execute("SELECT slug, title, contradictions FROM pages WHERE contested = 1")
            for row in cursor.fetchall():
                contradictions_raw = row["contradictions"]
                try:
                    # Если это уже список/dict (в некоторых версиях), используем как есть
                    if isinstance(contradictions_raw, str):
                        contradictions = json.loads(contradictions_raw)
                    else:
                        contradictions = contradictions_raw
                except (json.JSONDecodeError, TypeError):
                    contradictions = [""]

                result["contested"].append({
                    "slug": row["slug"],
                    "title": row["title"],
                    "contradictions": contradictions
                })

        # 2. Stale facts (устаревшие или без дат)
        # Ищем колонки для проверки времени: last_confirmed_at, updated, created
        date_col = None
        for col in ["last_confirmed_at", "updated", "created"]:
            if col in cols:
                date_col = col
                break

        if date_col:
            # Факты без даты или очень старые (для примера - считаем все, где дата < сейчас)
            cursor.execute(f"SELECT slug, title, {date_col} FROM pages")
            for row in cursor.fetchall():
                ts = row[date_col]
                if ts is None:
                    result["stale"].append({"slug": row["slug"], "title": row["title"], "days": None})
                else:
                    # Считаем разницу в днях (упрощенно)
                    diff_seconds = _now_epoch() - ts
                    days = int(diff_seconds // 86400)
                    if days > 30:  # Порог "stale" для примера — 30 дней
                        result["stale"].append({"slug": row["slug"], "title": row["title"], "days": days})
        else:
            # Если колонок дат нет вообще — помечаем все как "no dates"
            cursor.execute("SELECT slug, title FROM pages")
            for row in cursor.fetchall():
                result["stale"].append({"slug": row["slug"], "title": row["title"], "days": None})

        conn.close()
    except Exception as e:
        # Fail-open: любая ошибка возвращает пустой результат (кроме total_pages)
        print(f"Error during linting: {e}", file=sys.stderr)
        return {
            "contested": [],
            "stale": [],
            "total_pages": 0,
            "checked_at": _now_epoch()
        }

    return result


def lint_report(result: dict) -> str:
    """Человекочитаемый отчёт."""
    if not result or result["total_pages"] == 0:
        return "База данных пуста или недоступна."

    lines = [f"Проверено страниц: {result['total_pages']}"]

    # Contested report
    contested_count = len(result["contested"])
    if contested_count > 0:
        lines.append(f"Противоречия (contested): {contested_count}")
        for item in result["contested"]:
            lines.append(f"  - {item['slug']}: {item['contradictions']}")
    else:
        lines.append("Противоречий нет.")

    # Stale report
    stale_count = len(result["stale"])
    if stale_count > 0:
        lines.append(f"Устаревшие/без дат: {stale_count}")
        for item in result["stale"]:
            days_str = f"{item['days']} дней без подтверждения" if item['days'] is not None else "без дат"
            lines.append(f"  - {item['slug']} ({days_str})")
    else:
        lines.append("Устаревших нет.")

    return "\n".join(lines)


def main():
    # По умолчанию ищем в текущей директории рядом с файлом, как graph_lint.py
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, ".index_v2.db")

    if len(sys.argv) > 1:
        db_path = sys.argv[1]

    if not os.path.exists(db_path):
        print(f"Error: Database not found at {db_path}")
        sys.exit(1)

    result = lint_facts(db_path)
    print(lint_report(result))


if __name__ == "__main__":
    main()
