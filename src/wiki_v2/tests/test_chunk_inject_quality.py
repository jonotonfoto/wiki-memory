# tests/test_chunk_inject_quality.py — фикс 2026-08-26 «пропали чанки»
"""Инжект главной должен содержать ЖИВОЙ текст, а не служебные блоки.

Режимы отказа, найденные на живой системе 2026-08-26:
  1. page_chunk:0 = YAML-фронтматтер: получал вектор, выигрывал конкурс по
     совпадению запроса с title, а после _strip_frontmatter в сборке
     превращался в пустоту → инжект «шапка без чанка».
  2. Осколок гигантского облака тегов (блок > chunk_size разрезан overlap'ом,
     первая строка — хвост слова, заголовка «## Темы» нет) не считался
     мета-блоком, выигрывал косинус (облако перенасыщено словами запроса)
     и вставлял список тегов вместо прозы.

Фиксы под тестами:
  - chunker.is_meta_block: осколок без заголовка (≥80% буллетов, короткие
    строки) → мета-блок;
  - quality.is_junk_chunk: чистый YAML-фронтматтер → мусор (не эмбеддится);
  - плагин _build_context_main: конкурс по ИНЖЕКТИРУЕМОМУ тексту;
  - events.log_event: chunk_cos — честная релевантность 0–1 для дашборда;
  - dashboard_analysis._inject_relevance_series: предпочитает chunk_cos.
"""
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from wiki_v2.chunker import is_meta_block, split_text_spans, trim_meta_blocks
from wiki_v2.quality import is_junk_chunk


# ── chunker.is_meta_block ───────────────────────────────────────────────────

def test_meta_block_full_tag_cloud_with_header():
    block = "## Темы\n- cinema 4d\n- hermes\n- blender"
    assert is_meta_block(block) is True


def test_meta_block_headerless_fragment():
    # Осколок гигантского облака: первая строка — хвост слова, заголовка нет.
    frag = "визуализации\n- анимация\n- процедурная генерация\n- python в 3d\n- 3d объекты"
    assert is_meta_block(frag) is True


def test_meta_block_fragment_prose_tail_plus_bullets():
    # Осколок с длинной строкой-хвостом от overlap-разреза + буллеты глоссария.
    frag = ("связи, например, «MCP-сервер» в контексте связи Hermes с Blender/Cinema 4D)\n"
            "- mcp_server_plugin.pyp\n- MCP (Micro Controller Protocol)\n"
            "- MCP (Middleware Communication Protocol)\n- MCP (Mythical Computer Protocol)\n"
            "- blender-mcp плагин")
    assert is_meta_block(frag) is True


def test_not_meta_solutions_list_with_header():
    # «## Решения» — содержательный список (заголовок не из мета-набора).
    block = "## Решения\n- Перезапустить Cinema 4D для активации плагина\n- Использовать blender-mcp как альтернативу"
    assert is_meta_block(block) is False


def test_not_meta_prose():
    prose = ("Разговор о подключении Hermes к Cinema 4D и Blender "
             "для создания и модификации 3D-объектов.")
    assert is_meta_block(prose) is False


def test_meta_headerless_dense_bullet_list_even_long_lines():
    # Беззаголовочный ПЛОТНЫЙ список буллетов (пусть и длинных) — мета:
    # в сгенерированных страницах списки без заголовка не встречаются,
    # а осколки облаков тегов с длинными тегами — встречаются.
    block = ("- Перезапустить Cinema 4D для активации установленного плагина "
             "и проверить соединение\n- Использовать blender-mcp как временную альтернативу\n"
             "- Проверить логи сервера на предмет ошибок подключения")
    assert is_meta_block(block) is True


def test_not_meta_prose_with_bulleted_example():
    # Проза + короткий список (доля буллетов < 80%) — контент, не мета.
    block = ("Итог обсуждения: выбрали протокол MCP как основной.\n"
             "- пункт один\n- пункт два")
    assert is_meta_block(block) is False


def test_trim_meta_blocks_kills_fragment():
    frag = ("визуализации\n- анимация\n- процедурная генерация\n"
            "- python в 3d\n- 3d объекты")
    assert trim_meta_blocks(frag) == ""


# ── quality.is_junk_chunk ───────────────────────────────────────────────────

_FRONTMATTER = (
    '---\ntitle: "посмотри можно ли подключить hermes к cinema 4d"\n'
    "created: 2026-08-25\nupdated: 2026-08-26\ntype: entity\n"
    "tags: [discussion, decision, fact]\nconfidence: medium\n"
    "sources: [20260825_223407_955cb8]\n---"
)


def test_junk_pure_frontmatter():
    assert is_junk_chunk(_FRONTMATTER) is True


def test_not_junk_frontmatter_plus_body():
    assert is_junk_chunk(_FRONTMATTER + "\n\nРазговор о подключении Hermes к Cinema 4D.") is False


def test_not_junk_prose_with_hr_separator():
    # Проза с --- -разделителями не должна считаться фронтматтером.
    text = "Первая часть разговора про Cinema 4D и плагины\n---\nВторая часть с достаточным числом слов"
    assert is_junk_chunk(text) is False


def test_not_junk_short_yaml_like_without_close():
    text = "---\nкакой-то текст с достаточным количеством слов для проверки"
    assert is_junk_chunk(text) is False


def test_junk_conservative_rules_intact():
    assert is_junk_chunk("мало слов") is True
    assert is_junk_chunk("C:\\a\\b.py\nC:\\c\\d.py\nC:\\e\\f.py") is True
    assert is_junk_chunk("Обычный полезный чанк с достаточным количеством слов") is False


# ── indexer.embed_chunks: фронтматтер не эмбеддится ─────────────────────────

def test_embed_chunks_skips_frontmatter(monkeypatch):
    from wiki_v2 import indexer

    def _fake_embed(texts, input_type="passage"):
        return [np.ones(4, dtype=np.float32) for _ in texts]

    monkeypatch.setattr("wiki_v2.indexer.embed", _fake_embed)
    chunks = [_FRONTMATTER, "Полезный прозаический чанк с достаточным количеством слов"]
    res = indexer.embed_chunks("T", chunks, kind_prefix="page_chunk")
    assert "page_chunk:0" not in res
    assert "page_chunk:1" in res


# ── плагин: конкурс чанков по инжектируемому тексту ────────────────────────

_PLUGIN_PY = (Path(__file__).resolve().parents[3] / "plugins"
              / "wiki-context" / "__init__.py")
_WIKI_SCRIPTS = str(Path(__file__).resolve().parents[2])


def _load_plugin(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_SCRIPTS", _WIKI_SCRIPTS)
    monkeypatch.setenv("WIKI_PATH", str(tmp_path / "wiki"))
    spec = importlib.util.spec_from_file_location("wiki_context_inject_q", _PLUGIN_PY)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _page_with_chunks(tmp_path):
    """Страница как на живой системе: chunk:0 = «YAML + H1 + резюме»
    (мелкие блоки пакуются в окно ≤500), chunk:1 = «## Решения»,
    далее гигантское облако тегов, разрезанное на осколки."""
    wiki = tmp_path / "wiki" / "entities"
    wiki.mkdir(parents=True, exist_ok=True)
    tags = "\n".join(f"- тег номер {i} про 3d" for i in range(60))
    summary = "Разговор о подключении Hermes к Cinema 4D и Blender для 3D. " * 6
    full = (f'---\ntitle: "подключить hermes к cinema 4d"\ncreated: 2026-08-25\n---\n\n'
            f"# подключить hermes к cinema 4d\n\n{summary}\n\n"
            "## Решения\n"
            "- Подключить Hermes к Cinema 4D через плагин cinema4d-mcp и проверить порт 5555\n"
            "- Использовать blender-mcp как альтернативу для моделирования сцен\n"
            "- Перезапустить Cinema 4D для активации установленного плагина\n\n"
            f"## Темы\n{tags}\n")
    p = wiki / "hermes-cinema-4d.md"
    p.write_text(full, encoding="utf-8")
    return full, p


def _fake_db_factory(page_vectors):
    class _FakeDB:
        def __init__(self, _path):
            pass

        def get_page_chunk_embeddings(self, _slug):
            return dict(page_vectors)

        def get_session_chunk_embeddings(self, _slug):
            return {}

        def close(self):
            pass

    return _FakeDB


def _run_context_main(monkeypatch, mod, full, page, qv):
    """Патчит IndexDB/_embed_query и зовёт _build_context_main."""
    dim = 1024
    e0, e1 = np.zeros(dim, dtype=np.float32), np.zeros(dim, dtype=np.float32)
    e0[0], e1[1] = 1.0, 1.0
    spans = split_text_spans(full)
    vecs = {}
    for i, (s, e) in enumerate(spans):
        t = full[s:e]
        if "тег номер" in t and "## Темы" not in t.split("\n")[0]:
            vecs[f"page_chunk:{i}"] = e0.copy()
        elif t.strip().startswith("---"):
            vecs[f"page_chunk:{i}"] = (e0 * 0.99 + e1 * 0.1).astype(np.float32)
        else:
            vecs[f"page_chunk:{i}"] = (e0 * 0.3 + e1 * 0.9).astype(np.float32)
    fake = _fake_db_factory(vecs)
    monkeypatch.setattr("wiki_v2.index_db.IndexDB", fake)
    monkeypatch.setattr("wiki_v2.search.INDEX_DB", "fake.db", raising=False)
    monkeypatch.setattr(mod, "_embed_query", lambda _q: e0.copy())
    ctx = mod._build_context_main({"slug": "hermes-cinema-4d", "path": str(page),
                                   "title": "подключить hermes к cinema 4d"}, "подключить hermes к cinema 4d")
    return ctx


def test_intro_chunk_never_wins(monkeypatch, tmp_path):
    """chunk:0 = «YAML + H1 + резюме» с топ-косинусом (title-слова) не должен
    выигрывать конкурс: поверх интро он ничего не добавляет → инжект =
    шапка + содержательный чанк (Решения), а не «шапка без чанка»."""
    mod = _load_plugin(monkeypatch, tmp_path)
    full, p = _page_with_chunks(tmp_path)
    ctx = _run_context_main(monkeypatch, mod, full, p, None)
    assert ctx, "инжект не должен быть пуст"
    assert "title:" not in ctx, "YAML-фронтматтер не должен попадать в инжект"
    assert "cinema4d-mcp" in ctx, "в инжекте должен быть живой текст beyond интро"
    assert mod._LAST_CHUNK_COS["cos"] > 0.0, "косинус победителя должен фиксироваться"


def test_tag_fragment_chunk_never_wins(monkeypatch, tmp_path):
    """Осколок облака тегов с топ-косинусом → в инжекте проза, не теги."""
    mod = _load_plugin(monkeypatch, tmp_path)
    full, p = _page_with_chunks(tmp_path)
    ctx = _run_context_main(monkeypatch, mod, full, p, None)
    assert "- тег номер" not in ctx, "облако тегов не должно попадать в инжект"
    assert "cinema4d-mcp" in ctx


def test_no_injectable_chunks_falls_back_to_body(monkeypatch, tmp_path):
    """Все чанки-кандидаты мусорные → fallback на начало тела без YAML."""
    mod = _load_plugin(monkeypatch, tmp_path)
    wiki = tmp_path / "wiki" / "entities"
    wiki.mkdir(parents=True, exist_ok=True)
    tags = "\n".join(f"- тег {i}" for i in range(60))
    full = (f"---\ntitle: \"x\"\ncreated: 2026-08-25\n---\n\n# x\n\nкороткий\n\n## Темы\n{tags}\n")
    p = wiki / "x.md"
    p.write_text(full, encoding="utf-8")
    spans = split_text_spans(full)
    vecs = {f"page_chunk:{i}": np.ones(1024, dtype=np.float32) / 32
            for i in range(len(spans))}
    fake = _fake_db_factory(vecs)
    monkeypatch.setattr("wiki_v2.index_db.IndexDB", fake)
    monkeypatch.setattr("wiki_v2.search.INDEX_DB", "fake.db", raising=False)
    monkeypatch.setattr(mod, "_embed_query", lambda _q: np.ones(1024, dtype=np.float32) / 32)
    ctx = mod._build_context_main({"slug": "x", "path": str(p), "title": "x"}, "x запрос длиннее")
    assert "title:" not in ctx


# ── events.log_event: chunk_cos ─────────────────────────────────────────────

def test_log_event_writes_chunk_cos(monkeypatch, tmp_path):
    from wiki_v2 import events

    out = tmp_path / "events.jsonl"
    monkeypatch.setattr(events, "_events_path", lambda: out)
    events.log_event("запрос про cinema 4d", hits=3, top_slug="s",
                     top_score=0.038, chunk_cos=0.615)
    obj = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert obj["chunk_cos"] == pytest.approx(0.615)
    assert obj["top_score"] == pytest.approx(0.038)


# ── dashboard_analysis: график предпочитает chunk_cos ───────────────────────

def test_relevance_series_prefers_chunk_cos():
    from wiki_v2.dashboard_analysis import _inject_relevance_series

    evs = [
        {"ts": 100.0, "top_score": 0.038, "chunk_cos": 0.615},
        {"ts": 200.0, "top_score": 0.021},  # старое событие без chunk_cos
    ]
    res = _inject_relevance_series(evs, [100.0, 200.0])
    assert res[0]["value"] == pytest.approx(0.615)
    assert res[1]["value"] == pytest.approx(0.021)
