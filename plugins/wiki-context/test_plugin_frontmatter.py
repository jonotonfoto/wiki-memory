# tests/test_plugin_frontmatter.py
"""Регрессия P-чанк (fallback-фронтматтер): актуальный (зеркальный/живой) плагин.

Проверяет, что _build_context_main больше НЕ вставляет в контекст YAML-шапку страницы
(раньше fallback `full[:limit]` тащил `---\ntitle: ...\ncreated: ...\n---` — мусор).
Этот тест грузит ТЕКУЩИЙ плагин из зеркала проекта (не архивный sandbox).
"""
import importlib.util
import os
import sys
from pathlib import Path
from unittest import mock

_PLUGIN = Path(r"<REPO_ROOT>\plugins\wiki-context\__init__.py")


def _load_plugin():
    sys.path.insert(0, r"<REPO_ROOT>\scripts")
    spec = importlib.util.spec_from_file_location("wiki_context_now", str(_PLUGIN))
    m = importlib.util.module_from_spec(spec)
    sys.modules["wiki_context_now"] = m
    spec.loader.exec_module(m)
    return m


def test_strip_frontmatter_removes_yaml():
    plugin = _load_plugin()
    text = '---\ntitle: "Тест"\ncreated: 2026-08-21\ntags: [a]\n---\n\n# Тест\n\n## Темы\n- один'
    out = plugin._strip_frontmatter(text)
    assert out.startswith("# Тест")
    assert "title:" not in out
    assert "created:" not in out


def test_strip_frontmatter_no_frontmatter():
    plugin = _load_plugin()
    assert plugin._strip_frontmatter("просто текст") == "просто текст"


def test_build_context_main_fallback_no_frontmatter(tmp_path, monkeypatch):
    plugin = _load_plugin()
    # Переопределяем WIKI_PATH на tmp, чтобы _is_within(path) пропустил наш файл.
    monkeypatch.setattr(plugin, "WIKI_PATH", str(tmp_path))
    md = tmp_path / "entities"
    md.mkdir(parents=True, exist_ok=True)
    page_path = md / "vps-page.md"
    page_path.write_text(
        '---\ntitle: "VPS"\ncreated: 2026-08-21\ntags: [vps]\n---\n\n# VPS\n\nПароль от VPS: 12345\n'
        "## Темы\n- vps\n- пароль\n",
        encoding="utf-8",
    )
    page = {"slug": "vps-page", "title": "Страница про VPS", "path": str(page_path)}

    # _cv пусто -> fallback. Fallback НЕ должен содержать YAML-шапку, но должен
    # содержать полезный контент.
    with mock.patch("wiki_v2.index_db.IndexDB.get_page_chunk_embeddings", return_value={}):
        ctx = plugin._build_context_main(page, query="какой пароль от vps")

    assert "--- Главная" in ctx
    assert "VPS" in ctx
    assert "12345" in ctx
    # не должно быть YAML-служебной шапки
    assert "\n---\ntitle:" not in ctx
    assert "updated:" not in ctx
    assert "sources:" not in ctx


def test_build_context_main_semantic_chunk0_strips_frontmatter(tmp_path, monkeypatch):
    """Семантический путь: топ-чанк N=0 (YAML-блок) должен вырезаться, контент сохранён.

    Эмбеддинги page_chunk:N делаются от split_text(md) С фронтматтером — индексы нарезки
    обязаны совпадать. Но выбранный чанк (в т.ч. N=0) при сборке очищается от YAML-шапки.
    """
    plugin = _load_plugin()
    monkeypatch.setattr(plugin, "WIKI_PATH", str(tmp_path))
    md = tmp_path / "entities"
    md.mkdir(parents=True, exist_ok=True)
    page_path = md / "vps-page.md"
    page_path.write_text(
        '---\ntitle: "VPS"\ncreated: 2026-08-21\ntags: [vps]\n---\n\n# VPS\n\nПароль от VPS: 99999\n'
        "## Темы\n- vps\n",
        encoding="utf-8",
    )
    page = {"slug": "vps-page", "title": "Страница про VPS", "path": str(page_path)}

    # Эмбеддинги: только page_chunk:0 (который соответствует началу файла = YAML-блок).
    fake_cv = {"page_chunk:0": [0.99, 0.01]}
    with mock.patch("wiki_v2.index_db.IndexDB.get_page_chunk_embeddings", return_value=fake_cv), \
         mock.patch("wiki_v2.nvidia_client.embed", return_value=[[1.0, 0.0]]):
        ctx = plugin._build_context_main(page, query="vps")

    assert "--- Главная" in ctx
    # YAML-шапка вырезана даже из чанка N=0
    assert "\n---\ntitle:" not in ctx
    assert "updated:" not in ctx
    assert "sources:" not in ctx
    # полезный контент страницы сохранён
    assert "99999" in ctx
    assert "# VPS" in ctx
