"""Tests for S4.7: edges table + triplets + bfs_edges."""
import os

from wiki_v2.graph import bfs_edges
from wiki_v2.index_db import IndexDB


def _make_db(tmp_path):
    path = os.path.join(str(tmp_path), "test_edges.db")
    db = IndexDB(path)
    db.upsert_page("a", "A", "entities", "/a.md", "h1", summary="A")
    db.upsert_page("b", "B", "entities", "/b.md", "h2", summary="B")
    db.upsert_page("c", "C", "entities", "/c.md", "h3", summary="C")
    return db


def test_save_edges(tmp_path):
    db = _make_db(tmp_path)
    db.save_edges("a", [
        {"subject": "a", "predicate": "связан_с", "object": "b"},
        {"subject": "a", "predicate": "использует", "object": "c"},
    ])
    edges = db.get_edges()
    assert "a" in edges
    assert ("связан_с", "b") in edges["a"]
    assert ("использует", "c") in edges["a"]
    db.close()


def test_save_edges_dedup(tmp_path):
    db = _make_db(tmp_path)
    db.save_edges("a", [
        {"subject": "a", "predicate": "p", "object": "b"},
        {"subject": "a", "predicate": "p", "object": "b"},  # дубликат
    ])
    edges = db.get_edges()
    assert edges["a"].count(("p", "b")) == 1  # дедуп
    db.close()


def test_get_edges_empty(tmp_path):
    db = _make_db(tmp_path)
    assert db.get_edges() == {}
    db.close()


def test_save_edges_tuples(tmp_path):
    db = _make_db(tmp_path)
    db.save_edges("a", [("a", "p1", "b"), ("a", "p2", "c")])
    edges = db.get_edges()
    assert ("p1", "b") in edges["a"]
    assert ("p2", "c") in edges["a"]
    db.close()


def test_bfs_edges_list_format():
    edges = {"a": [("p", "b")], "b": [("p", "c")]}
    result = bfs_edges(["a"], edges, depth=2)
    assert "b" in result
    assert "c" in result


def test_bfs_edges_set_format():
    edges = {"a": {"b"}, "b": {"c"}}
    result = bfs_edges(["a"], edges, depth=2)
    assert "b" in result
    assert "c" in result


def test_bfs_edges_fail_open():
    assert bfs_edges(["a"], None, depth=2) == []
    assert bfs_edges(["a"], {}, depth=2) == []


# --- S4.7 additional coverage ---

def test_save_edges_empty_list(tmp_path):
    """save_edges с пустым списком — не падает, get_edges возвращает пустой slug."""
    db = _make_db(tmp_path)
    db.save_edges("a", [])
    edges = db.get_edges()
    assert "a" not in edges  # пустой список → нет записей в БД
    db.close()


def test_save_edges_none_input(tmp_path):
    """save_edges с None — не падает."""
    db = _make_db(tmp_path)
    db.save_edges("a", None)
    edges = db.get_edges()
    assert "a" not in edges
    db.close()


def test_bfs_edges_multi_start():
    """BFS с несколькими стартовыми slug."""
    edges = {"a": [("p", "b")], "c": [("p", "d")]}
    result = bfs_edges(["a", "c"], edges, depth=1)
    assert "b" in result
    assert "d" in result


def test_bfs_edges_depth_0():
    """BFS с глубиной 0 — возвращает пустой список."""
    edges = {"a": [("p", "b")]}
    result = bfs_edges(["a"], edges, depth=0)
    assert result == []


def test_save_edges_overwrite(tmp_path):
    """save_edges перезаписывает старые рёбра страницы (DELETE + INSERT)."""
    db = _make_db(tmp_path)
    # Сначала одно ребро
    db.save_edges("a", [{"subject": "a", "predicate": "p1", "object": "b"}])
    edges = db.get_edges()
    assert ("p1", "b") in edges["a"]

    # Теперь другое — p1 должно исчезнуть
    db.save_edges("a", [{"subject": "a", "predicate": "p2", "object": "c"}])
    edges = db.get_edges()
    assert ("p1", "b") not in edges.get("a", [])
    assert ("p2", "c") in edges["a"]
    db.close()
