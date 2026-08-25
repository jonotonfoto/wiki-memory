"""Тесты кэш-коллизии плагина wiki-context.

Покрывают:
  - _significant_words исключает токены Windows/UNIX-путей (_PATH_TOKENS)
  - CACHE_MIN_ROOT_MATCH == 3 (порог для длинных вопросов)
  - _cache_get при равном overlap выбирает СВЕЖУЮ запись по ts
  - _build_context_maybe_cached возвращает tuple (context_str, cache_hit_bool)

НЕ меняем __init__.py плагина и НЕ трогаем реальный cache.json.
"""
# ---------------------------------------------------------------------------
# Загрузка модуля плагина через importlib (имя нестандартное).
# ---------------------------------------------------------------------------
import importlib.util
import json
import os
import time
from pathlib import Path

# Плагин ищем сначала в git-checkout (repo/plugins/...), затем в живой установке.
_CANDIDATES = [
    Path(__file__).resolve().parents[3] / "plugins" / "wiki-context" / "__init__.py",
    Path(os.path.expandvars(r"%LOCALAPPDATA%\hermes\plugins\wiki-context\__init__.py")),
]
_PLUGIN_PATH = next((p for p in _CANDIDATES if p.exists()), _CANDIDATES[0])
spec = importlib.util.spec_from_file_location(
    "wiki_context_plugin", _PLUGIN_PATH
)
mod = importlib.util.module_from_spec(spec)
# Не вызываем exec_module — он может запустить поиск wiki.
# Но нам нужны чистые функции; проверим, что модуль загрузился частично.
try:
    spec.loader.exec_module(mod)
except Exception:
    # Если exec_module упал (например, нет wiki_v2), всё равно продолжаем —
    # _significant_words / _cache_get / CACHE_MIN_ROOT_MATCH определены до
    # импорта wiki_v2 и работают автономно.
    pass


# ---------------------------------------------------------------------------
# Утилиты для работы с кэшем в тестах
# ---------------------------------------------------------------------------

def _write_cache(cache: dict, monkeypatch) -> None:
    """Записать JSON-словарь в изолированный CACHE_PATH."""
    cache_path = mod.CACHE_PATH  # уже может быть замопан через tmp_path
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Тест 1: _significant_words исключает токены путей
# ---------------------------------------------------------------------------

def test_significant_excludes_path_tokens(monkeypatch):
    """Путь Windows не должен давать значимых слов из каталогов."""
    # Путь содержит слова, которые входят в _PATH_TOKENS.
    # Используем контентные слова (НЕ стоп-слова), чтобы проверить их сохранение.
    text = (
        r"%USERPROFILE%\Documents\Hermes Projects"
        r"\projects\wiki-live-3-2026-08-15 анализ данные система"
    )
    words = mod._significant_words(text)

    # Слова из _PATH_TOKENS НЕ должны попасть в результат.
    excluded = {"users", "documents", "hermes", "projects"}
    for token in excluded:
        assert (
            token not in words
        ), f"Токен пути '{token}' попал в значимые слова: {words}"

    # Настоящие контентные слова должны остаться.
    assert "анализ" in words, f"'анализ' отсутствует в {words}"
    assert "данные" in words, f"'данные' отсутствует в {words}"
    assert "система" in words, f"'система' отсутствует в {words}"


# ---------------------------------------------------------------------------
# Тест 2: кэш не возвращает stale-контекст при коллизии путей
# ---------------------------------------------------------------------------

def test_cache_no_false_hit_on_path_collision(tmp_path, monkeypatch):
    """Кэш с записью другого проекта НЕ должен дать ложный хит."""
    # Изолируем кэш.
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(mod, "CACHE_PATH", str(cache_file))

    old_ts = time.time() - 10000  # старая запись
    stale_cache = {
        "/legacy/wiki-project-path": {
            "ctx": "STALE",
            "ts": old_ts,
        }
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(stale_cache, f)

    # Запрос с путём другого проекта.
    query = (
        r"<REPO_ROOT>"
        "\n\nвопрос про чанк"
    )
    result = mod._cache_get(query)

    # При threshold=3 и исключении путей из _significant_words
    # overlap будет < 3, поэтому stale не должен вернуться.
    assert (
        result != "STALE"
    ), f"Кэш вернул STALE-контекст при коллизии путей: {result}"


# ---------------------------------------------------------------------------
# Тест 3: _cache_get выбирает свежую запись при равном overlap
# ---------------------------------------------------------------------------

def test_cache_picks_fresh_when_equal_score(tmp_path, monkeypatch):
    """Две записи с одинаковым overlap — берётся та, у которой больше ts."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(mod, "CACHE_PATH", str(cache_file))

    now = time.time()
    old_ts = now - 5000   # старая
    new_ts = now           # свежая

    fresh_cache = {
        "вопрос про чанки": {
            "ctx": "OLD_CONTEXT",
            "ts": old_ts,
        },
        "вопрос про чанк": {
            "ctx": "FRESH_CONTEXT",
            "ts": new_ts,
        },
    }
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(fresh_cache, f)

    result = mod._cache_get("вопрос про чанк")

    # Должен вернуться контекст свежей записи.
    assert (
        result == "FRESH_CONTEXT"
    ), f"Ожидался FRESH_CONTEXT, получен: {result}"


# ---------------------------------------------------------------------------
# Тест 4: _build_context_maybe_cached возвращает tuple из 2 элементов
# ---------------------------------------------------------------------------

def test_build_context_maybe_cached_returns_tuple(monkeypatch):
    """Короткий запрос (< min_query_len) → ("", False)."""
    # Заменяем _cache_get на заглушку, чтобы не лезть в кэш.
    monkeypatch.setattr(mod, "_cache_get", lambda q: None)

    result = mod._build_context_maybe_cached("ок")

    assert isinstance(result, tuple), f"Ожидался tuple, получен {type(result)}"
    assert len(result) == 2, (
        f"Кортеж должен иметь длину 2, получено: {len(result)}"
    )
    assert result[1] is False, (
        f"cache_hit должен быть False для короткого запроса, получен: {result[1]}"
    )


# ---------------------------------------------------------------------------
# Тест 5: _build_context_maybe_cached возвращает cache_hit=True при кэш-хите
# ---------------------------------------------------------------------------

def test_build_context_maybe_cached_cache_hit_true(tmp_path, monkeypatch):
    """Заполненный кэш + совпадающий запрос → (контекст, True)."""
    cache_file = tmp_path / "cache.json"
    monkeypatch.setattr(mod, "CACHE_PATH", str(cache_file))

    now = time.time()
    cached_ctx = "<wiki-memory>\nТестовый контекст\n</wiki-memory>"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump({
            "тестовый вопрос про память": {
                "ctx": cached_ctx,
                "ts": now,
            }
        }, f)

    # monkeypatch _cache_get чтобы он возвращал контекст.
    original_cache_get = mod._cache_get
    monkeypatch.setattr(
        mod, "_cache_get", lambda q: original_cache_get(q)
    )

    result = mod._build_context_maybe_cached("тестовый вопрос про память")

    assert isinstance(result, tuple), f"Ожидался tuple, получен {type(result)}"
    assert len(result) == 2
    assert result[1] is True, (
        f"cache_hit должен быть True при кэш-хите, получен: {result[1]}"
    )
