# tests/test_relevance_gate.py
import numpy as np
import pytest

import wiki_v2.relevance_gate as rg
from wiki_v2.index_db import IndexDB
from wiki_v2.indexer import _is_transient_task


def test_gate_offdomain_skip():
    # корпус про память/индексацию — «пирог» в нём не встречается → A==0 → skip
    lex = frozenset({"памят", "индек", "сесси", "поиск"})
    assert rg.gate_decision("как испечь пирог яблочный", lex) == "skip"
    assert rg.gate_decision("погода в москве сегодня", lex) == "skip"


def test_gate_relevant_show():
    lex = frozenset({"индек", "памят", "сесси"})
    # «работает» и «как» — стоп-слова; остаются индексация/память (корни «индек»/«памят»)
    assert rg.gate_decision("как работает индексация памяти", lex) == "show"


def test_gate_cross_morphology_show():
    # «сессиями» и «сессии» — общий корень «сесси» → A>=1
    lex = frozenset({"сесси", "индек"})
    assert rg.gate_decision("как работает индексация сессиями", lex) == "show"


def test_gate_short_low_confidence():
    lex = frozenset({"памят"})
    assert rg.gate_decision("hi", lex) == "low_confidence"
    assert rg.gate_decision("ок", lex) == "low_confidence"
    assert rg.gate_decision("привет", lex) == "low_confidence"


def test_gate_failopen_null_lexicon_is_show():
    # словарь недоступен → не роняем, ведём себя как «показать»
    assert rg.gate_decision("как испечь пирог", None) == "show"


def test_a_count():
    lex = frozenset({"индек", "памят"})
    assert rg.A_count("как испечь пирог", lex) == 0
    assert rg.A_count("как работает индексация", lex) == 1


def test_lexicon_built_from_md(tmp_path):
    rg._lexicon_cache = None
    md = tmp_path / "page.md"
    md.write_text("в сессии обсуждали индексацию и память проекта по эмбеддингам",
                  encoding="utf-8")
    db = IndexDB(str(tmp_path / "i.db"))
    db.upsert_page("p", "P", "entities", str(md), "h")
    db.close()
    db = IndexDB(str(tmp_path / "i.db"))
    try:
        lex = rg.get_lexicon(db)
    finally:
        db.close()
    assert lex is not None
    assert "индек" in lex
    assert "сесси" in lex
    assert "памят" in lex


def test_hub_correction_reduces_hub():
    # у «hub» много векторов → высокий h(p); у «peer» — низкий.
    # query равноценна обоим по «сырому» косинусу, но после коррекции hub проигрывает.
    q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    store = {
        "peer": np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
        "hub": np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
    }
    hubness = {"peer": 0.5, "hub": 1.0}
    res = dict(rg.top_k_cosine_hub(q, store, hubness, beta=1.0, k=5))
    assert res["peer"] == pytest.approx(0.5)   # 1.0 - 1.0*0.5
    assert res["hub"] == pytest.approx(0.0)    # 1.0 - 1.0*1.0
    assert res["hub"] < res["peer"]


def test_hub_correction_failopen_empty_hubness():
    q = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
    store = {"a": np.array([0.8, 0.1, 0.0, 0.0], dtype=np.float32)}
    res = rg.top_k_cosine_hub(q, store, {}, beta=1.0, k=5)
    assert res[0][0] == "a"
    assert res[0][1] > 0


# ── Политика индексации: отсев одноразовых task/test-сессий ────────────────
def test_transient_task_true():
    for t in ("Написать тесты для S4.2 Write-Gate",
              "Выполни задачу из файла-брифа task-4b-junk-filter",
              "Создай новый модуль wiki_v2/dashboard_charts.py (live-копия)",
              "Ресерч: библиотеки графиков для дашборда",
              "продолжи C:\\Users\\...",
              "hi",
              "проверь работает ли у тебя wiki память ?"):
        assert _is_transient_task(t), t


def test_transient_task_false_keeps_memory():
    # реальная память пользователя — НЕ матчится
    assert not _is_transient_task("при загрузке компьютера у меня открываются окна")
    assert not _is_transient_task("Инициализация нового проекта")

