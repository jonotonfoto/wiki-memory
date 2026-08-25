"""clear_wiki_cache.py — полностью очистить кэш плагина wiki-context.

ПРАВИЛО (пользователь 2026-08-14): перед/при тестировании wiki V3 (или любых
тестах, меняющих данные) ОБЯЗАТЕЛЬНО полностью очищать кэш плагина
wiki-context. Причина: кэш хранит устаревший контекст со старыми страницами
и отдаёт его вместо перечитывания .index_v2.db → проиндексированные страницы
не попадают в окно памяти системного промпта.

Использование:
    python clear_wiki_cache.py            # очистить кэш (сделать бэкап .bak)
    python clear_wiki_cache.py --nobackup # очистить без бэкапа
"""
import json
import os
import shutil
import sys
import time

HOME = os.environ.get("HERMES_HOME", os.path.join(os.environ.get("LOCALAPPDATA", ""), "hermes"))
CACHE = os.path.join(HOME, "plugins", "wiki-context", "cache.json")


def clear(backup: bool = True) -> str:
    if not os.path.exists(CACHE):
        return f"cache.json не найден ({CACHE}) — нечего чистить"
    if backup:
        bak = f"{CACHE}.bak.{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(CACHE, bak)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump({}, f)
    msg = f"кэш очищен: {CACHE}"
    if backup:
        msg += f" (бэкап: {bak})"
    return msg


if __name__ == "__main__":
    backup = "--nobackup" not in sys.argv
    print(clear(backup=backup))
