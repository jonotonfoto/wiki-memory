"""Подэтап 4г: триангуляция плагина wiki-context использует quality._STOPWORDS (не мок-список).

Проверяем:
  - _common_roots() строится из quality._STOPWORDS (нет захардкоженного «common»-списка)
  - 1 общий корень, если он общий (стоп-слово) -> НЕ принимаем страницу
  - 1 общий корень специфичного слова -> принимаем
  - 2+ общих корня -> принимаем независимо от стоп-слов
"""
import importlib.util
import os
import sys

PLUGIN_PATH = r"%LOCALAPPDATA%\hermes\plugins\wiki-context\__init__.py"
spec = importlib.util.spec_from_file_location("wiki_context_4g", PLUGIN_PATH)
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception:
    pass

_topic_match = mod._topic_match
_common_roots = mod._common_roots


def _page(topics_text: str) -> str:
    """Страница с секцией «## Темы»."""
    return (
        "---\ntitle: X\n---\n\n# X\n\n## Темы\n" + topics_text + "\n"
    )


def test_common_roots_derived_from_quality_stopwords():
    """Корни общих слов берутся из quality._STOPWORDS (5-символьные префиксы)."""
    from wiki_v2 import quality
    expected = {s[:5] for s in quality._STOPWORDS if len(s) >= 5}
    assert _common_roots() == frozenset(expected)
    # «работ» (работа) — из _STOPWORDS.
    assert "работ" in _common_roots()
    # Старый «инстр»/«модел» (из мок-списка) — НЕ из _STOPWORDS, отсутствует.
    assert "инстр" not in _common_roots()
    assert "модел" not in _common_roots()


def test_one_root_common_word_rejected():
    """1 общий корень (стоп-слово «работа») -> НЕ принимаем страницу."""
    assert _topic_match("обсуждение работы системы", _page("- работа\n")) is False
    assert _topic_match("важна тема", _page("- важно\n")) is False


def test_one_root_specific_word_accepted():
    """1 общий корень специфичного (не стоп-) слова -> принимаем."""
    assert _topic_match("аврора", _page("- аврора\n")) is True
    assert _topic_match("память", _page("- памяти\n")) is True


def test_two_roots_accepted_always():
    """2+ общих корня -> принимаем независимо от того, есть ли стоп-слова."""
    assert _topic_match("система работы", _page("- работа\n- система\n")) is True


def test_no_topics_rejected():
    """Без секции «## Темы» -> False."""
    assert _topic_match("аврора", "# X\n\nтекст без тем\n") is False
