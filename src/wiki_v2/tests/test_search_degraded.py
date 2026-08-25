# tests/test_search_degraded.py — этап 1.5b: circuit-breaker degraded mode в search()
"""Tests for stage 1.5b (wiki_v2): search() degraded mode.

Scenarios:
  1. test_degraded_skips_embed: nc._state == 'degraded' → embed() NOT called,
     straight to keyword-only.  Keyword path finds the page.
  2. test_embed_failure_falls_back_to_keyword: api_state() == 'normal' but
     embed() returns None → search() still finds page via keyword (embed
     called once, no semantic hit).
  3. test_normal_uses_semantic: api_state() == 'normal', embed() returns a
     real vector → search() returns a semantic hit (embed called, semantic
     source wins).

Setup uses temp WIKI_PATH + state.db via monkeypatch.setenv, then
config.reload() + importlib.reload(search) — the _setup pattern from
test_consistency.py.
"""
import importlib

import numpy as np
import pytest
from unittest.mock import patch

import wiki_v2.search as search_mod
import wiki_v2.config as config
import wiki_v2.nvidia_client as nc


def _setup(tmp_path, monkeypatch):
    """Temp WIKI_PATH + state.db + reloaded search module (test_consistency pattern)."""
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    config.reload()
    importlib.reload(search_mod)
    return wiki


def _seed_page(wiki_dir, slug="nemotron-fix", title="Фикс немотрона",
               full_text="Немотрон использует NVIDIA nv-embedqa. "
                         "Починка NVIDIA_BASE_URL и алиасы решает проблемы "
                         "подключения к серверу."):
    """Insert a single page with full_text into the index DB."""
    from wiki_v2.index_db import IndexDB
    db_path = str(wiki_dir / ".index_v2.db")
    db = IndexDB(db_path)
    db.upsert_page(
        slug=slug, title=title, section="entities", path=str(wiki_dir / f"{slug}.md"),
        content_hash="h1", summary="Починили NVIDIA_BASE_URL и алиасы",
        full_text=full_text,
    )
    db.close()
    return db_path


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Сброс circuit-breaker state между тестами (как в test_nvidia_client / _ap_resilience)."""
    nc._errors_consecutive = 0
    nc._state = "normal"
    yield
    nc._errors_consecutive = 0
    nc._state = "normal"


# ─────────────────────────────────────────────────────────────────────────
# 1. degraded → embed NOT called, keyword-only finds the page
# ─────────────────────────────────────────────────────────────────────────
def test_degraded_skips_embed(tmp_path, monkeypatch):
    """Этап 1.5b: при _state=='degraded' search() НЕ вызывает embed(), сразу
    keyword-only.  Страница найдена по ключевым словам → non-empty hits."""
    wiki = _setup(tmp_path, monkeypatch)
    _seed_page(wiki)

    # breaker в degraded — имитируем 3 реальные ошибки подряд
    nc._state = "degraded"
    nc._errors_consecutive = 3

    with patch("wiki_v2.search.embed") as mock_embed:
        # query длиной > MIN_QUERY_LEN (15) для пропуска early-return
        hits, pages = search_mod.search("как починить немотрон на сервере")

    mock_embed.assert_not_called()  # embed НЕ дёргается вообще
    assert hits, "keyword-only должен найти страницу"
    assert hits[0][0] == "nemotron-fix"
    assert hits[0][2] == "keyword"


# ─────────────────────────────────────────────────────────────────────────
# 2. normal + embed returns None → keyword fallback
# ─────────────────────────────────────────────────────────────────────────
def test_embed_failure_falls_back_to_keyword(tmp_path, monkeypatch):
    """Этап 1.5b: api_state()=='normal', но embed() возвращает None →
    search() падает back к keyword.  embed вызывается ровно один раз."""
    wiki = _setup(tmp_path, monkeypatch)
    _seed_page(wiki)

    assert nc.api_state() == "normal"  # breaker сброшен фитурой

    with patch("wiki_v2.search.embed", return_value=None) as mock_embed:
        hits, pages = search_mod.search("как починить немотрон на сервере")

    assert mock_embed.call_count == 1  # embed вызван, но вернул None
    assert hits, "keyword fallback должен найти страницу"
    assert hits[0][0] == "nemotron-fix"
    assert hits[0][2] == "keyword"


# ─────────────────────────────────────────────────────────────────────────
# 3. normal + embed returns vector → semantic hit
# ─────────────────────────────────────────────────────────────────────────
def test_normal_uses_semantic(tmp_path, monkeypatch):
    """Этап 1.5b: api_state()=='normal', embed() возвращает вектор →
    search() использует семантический поиск.  embed вызывается."""
    wiki = _setup(tmp_path, monkeypatch)
    _seed_page(wiki)

    # Сохраняем эмбеддинг страницы в индекс (чтобы top_k_cosine нашёл semantic hit)
    from wiki_v2.index_db import IndexDB
    db = IndexDB(str(wiki / ".index_v2.db"))
    page_vec = np.array([1.0] + [0.0] * 1023, dtype=np.float32)
    db.set_embedding("nemotron-fix", page_vec)
    db.close()

    assert nc.api_state() == "normal"

    # query vector почти совпадает с эмбеддингом страницы
    q = np.array([0.99, 0.01] + [0.0] * 1022, dtype=np.float32)

    with patch("wiki_v2.search.embed", return_value=[q]) as mock_embed:
        hits, pages = search_mod.search("как починить немотрон на сервере")

    assert mock_embed.call_count == 1
    assert hits, "semantic search должен найти страницу"
    assert hits[0][0] == "nemotron-fix"
    assert hits[0][2] == "semantic"
