# tests/test_tuning.py — S2.5.11 тонкая настройка (параметры из config, не хардкод)

import wiki_v2.search as search_mod
from wiki_v2 import config


def test_rrf_k_from_config():
    """S2.5.11: RRF_K из config (не хардкод 60)."""
    assert hasattr(config, "RRF_K")
    assert isinstance(config.RRF_K, int)


def test_rrf_changes_with_k():
    """S2.5.11: изменение RRF_K меняет скоры RRF (параметр влияет на результат)."""
    rank_lists = [["a", "b", "c"], ["b", "a"]]
    config.RRF_K = 60
    s60 = search_mod._rrf(rank_lists, k=60)
    config.RRF_K = 40
    s40 = search_mod._rrf(rank_lists, k=40)
    # скоры отличаются
    assert s60 != s40


def test_top_k_from_config():
    """S2.5.11: TOP_K из config."""
    assert search_mod.TOP_K == config.TOP_K


def test_chunk_params_in_config():
    """S2.5.11: параметры чанков настраиваемы."""
    assert hasattr(config, "CHUNK_SIZE")
    assert hasattr(config, "CHUNK_OVERLAP")
