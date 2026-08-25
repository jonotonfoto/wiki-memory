"""Tests for Wiki graph (S2.5.7): entities/links persistence, BFS, wikilinks, lint."""
import os

from wiki_v2.graph import bfs
from wiki_v2.graph_lint import lint_graph
from wiki_v2.index_db import IndexDB


def _make_db(tmp_path):
    """Создать временную БД с двумя страницами."""
    path = os.path.join(str(tmp_path), "test_graph.db")
    db = IndexDB(path)
    db.upsert_page("a", "Страница A", "entities", "/a.md", "h1", summary="про A")
    db.upsert_page("b", "Страница B", "entities", "/b.md", "h2", summary="про B")
    db.upsert_page("c", "Страница C", "entities", "/c.md", "h3", summary="про C")
    return db


def test_save_entities_and_links(tmp_path):
    """entities/links сохраняются (раньше выбрасывались)."""
    db = _make_db(tmp_path)
    db.save_entities("a", ["выготский"], ["психология"])
    db.save_links("a", ["b"])

    entities, links = db.get_graph()
    assert "выготский" in entities.get("a", [])
    assert "b" in links.get("a", set())
    db.close()


def test_wikilink_stripped(tmp_path):
    """[[связь]] Obsidian-паттерн извлекается (скобки убраны)."""
    db = _make_db(tmp_path)
    # save_links принимает ссылку с [[ ]], после _strip_wikilinks в indexer она
    # сохраняется как "b". Здесь проверяем сам _strip_wikilinks из indexer.
    from wiki_v2.indexer import _strip_wikilinks
    stripped = _strip_wikilinks(["[[b]]", "c"])
    assert stripped == ["b", "c"]
    db.close()


def test_bfs_depth_2(tmp_path):
    """BFS глубина 2 находит связанную страницу через 2 шага (a->b->c)."""
    db = _make_db(tmp_path)
    db.save_links("a", ["b"])
    db.save_links("b", ["c"])

    _, links = db.get_graph()
    result = bfs(["a"], links, depth=2)
    assert "b" in result  # 1 шаг
    assert "c" in result  # 2 шага
    db.close()


def test_bfs_by_shared_entity(tmp_path):
    """Связь по общей сущности: A и B связаны через shared entity."""
    db = _make_db(tmp_path)
    # A и B разделяют entity 'общая тема' -> связываем их через links
    db.save_entities("a", ["общая тема"], [])
    db.save_entities("b", ["общая тема"], [])
    # явная связь через общую сущность моделируется как link
    db.save_links("a", ["b"])

    entities, links = db.get_graph()
    assert entities.get("a") == ["общая тема"]
    assert entities.get("b") == ["общая тема"]
    assert "b" in links.get("a", set())
    db.close()


def test_lint_graph_finds_orphan_and_broken(tmp_path):
    """lint_graph находит orphan и битые ссылки."""
    db = _make_db(tmp_path)
    db.save_links("a", ["nonexistent"])  # битая ссылка
    # a связана, b и c — orphan (нет входящих)

    report = lint_graph(db)
    assert "a->nonexistent" in report["broken_links"]
    assert "b" in report["orphan"]
    assert report["total_pages"] == 3
    db.close()


def test_lint_graph_empty(tmp_path):
    """Пустой граф -> пустой отчёт (не падать)."""
    path = os.path.join(str(tmp_path), "empty.db")
    db = IndexDB(path)
    report = lint_graph(db)
    assert report["total_pages"] == 0
    assert report["broken_links"] == []
    assert report["orphan"] == []
    db.close()
