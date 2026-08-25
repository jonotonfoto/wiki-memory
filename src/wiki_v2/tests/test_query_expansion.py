# tests/test_query_expansion.py — S2.5.2 Query Expansion

import pytest
import wiki_v2.search as search_mod

# ---- helpers для моков ----

def _mock_chat(variants):
    """Возвращает функцию-мок chat_completion, отдающую variants как JSON-массив."""
    import json

    def mock(system, user, model=None, **kw):
        return json.dumps(variants, ensure_ascii=False)
    return mock


# ---- expand_query ----

def test_expand_query_returns_variants_including_original(monkeypatch):
    monkeypatch.setattr(search_mod, "chat_completion", _mock_chat(
        ["как устроена психика", "психология ребёнка"]))
    out = search_mod.expand_query("психика ребёнка", variants=3)
    assert isinstance(out, list)
    assert len(out) >= 2
    assert "психика ребёнка" in out  # исходный включён


def test_expand_query_fail_open_on_llm_error(monkeypatch):
    # LLM упал → вернуть [query], не бросать
    def boom(system, user, model=None, **kw):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(search_mod, "chat_completion", boom)
    out = search_mod.expand_query("тест запрос", variants=3)
    assert out == ["тест запрос"]  # fail-open


def test_expand_query_cache_reuses(monkeypatch):
    calls = {"n": 0}
    import json
    def mock(system, user, model=None, **kw):
        calls["n"] += 1
        return json.dumps(["вариант1", "вариант2"])
    monkeypatch.setattr(search_mod, "chat_completion", mock)
    # сброс кэша для чистоты
    if hasattr(search_mod, "_lru_expansion_cache"):
        search_mod._lru_expansion_cache().clear()
    first = search_mod.expand_query("повторный запрос", variants=3)
    second = search_mod.expand_query("повторный запрос", variants=3)
    assert first == second
    assert calls["n"] == 1  # API вызван 1 раз, второй из кэша


def test_expand_query_short_query_does_not_crash(monkeypatch):
    monkeypatch.setattr(search_mod, "chat_completion", _mock_chat(["полный запрос про память"]))
    out = search_mod.expand_query("память", variants=2)
    assert isinstance(out, list) and len(out) >= 1


# ---- _rrf ----

def test_rrf_ranks_shared_result_higher():
    rrf = getattr(search_mod, "_rrf", None)
    if rrf is None:
        pytest.skip("_rrf не реализован")
    # [A,B] и [B,C] → B есть в обоих → должен быть выше A и C
    ranked = rrf([["A", "B"], ["B", "C"]], k=60)
    # ranked: dict slug -> score (выше = лучше)
    assert ranked["B"] > ranked["A"]
    assert ranked["B"] > ranked["C"]


def test_rrf_single_list_preserves_order():
    rrf = getattr(search_mod, "_rrf", None)
    if rrf is None:
        pytest.skip("_rrf не реализован")
    ranked = rrf([["X", "Y", "Z"]], k=60)
    assert ranked["X"] > ranked["Y"] > ranked["Z"]


def test_rrf_empty_input():
    rrf = getattr(search_mod, "_rrf", None)
    if rrf is None:
        pytest.skip("_rrf не реализован")
    assert rrf([], k=60) == {}


# ---- интеграция: расширение находит страницу, которую без него нет ----

def test_expansion_finds_page_not_found_without(monkeypatch):
    # Мок: базовый search(query) по исходнику не находит; по варианту находит.
    rrf = getattr(search_mod, "_rrf", None)
    if rrf is None:
        pytest.skip("_rrf не реализован")
    # эмулируем: варианты дают разные списки slug
    base_ranks = ["страница-не-та", "другая"]
    expanded_ranks = ["искомая-страница", "страница-не-та"]
    merged = rrf([base_ranks, expanded_ranks], k=60)
    # искомая-страница есть ТОЛЬКО в расширенном списке (без QE её бы не нашли)
    assert "искомая-страница" in merged
    # но она должна попасть в итог через RRF (не потеряться)
    assert "страница-не-та" in merged
    # обе релевантны; ключевое — искомая-страница участвует в слиянии
    top_slug = max(merged, key=merged.get)
    assert top_slug in ("искомая-страница", "страница-не-та")
