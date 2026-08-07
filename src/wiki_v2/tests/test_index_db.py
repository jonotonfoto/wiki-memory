# tests/test_index_db.py
import numpy as np

from wiki_v2.index_db import IndexDB


def test_schema_and_page_crud(tmp_path):
    db = IndexDB(str(tmp_path / "idx.db"))
    db.upsert_page(slug="nemotron-fix", title="Фикс немотрона", section="entities",
                   path="/x.md", content_hash="abc", summary="Как починили")
    row = db.get_page("nemotron-fix")
    assert row["title"] == "Фикс немотрона"
    assert row["section"] == "entities"
    db.close()


def test_embedding_roundtrip(tmp_path):
    db = IndexDB(str(tmp_path / "idx.db"))
    db.upsert_page(slug="p1", title="t", section="entities", path="/p",
                   content_hash="h", summary="s")
    vec = np.random.rand(1024).astype(np.float32)
    db.set_embedding("p1", vec)
    got = db.get_all_embeddings()
    assert "p1" in got
    assert np.allclose(got["p1"], vec, atol=1e-6)
    db.close()


def test_session_tracking(tmp_path):
    db = IndexDB(str(tmp_path / "idx.db"))
    assert db.is_session_indexed("sess-1") is False
    db.mark_session_indexed("sess-1", page_slug="p1")
    assert db.is_session_indexed("sess-1") is True
    db.close()


def test_session_hash_default_empty(tmp_path):
    db = IndexDB(str(tmp_path / "idx.db"))
    db.mark_session_indexed("sess-2", page_slug="p2")
    assert db.get_session_hash("sess-2") == ""
    db.close()


def test_session_hash_set_and_get(tmp_path):
    db = IndexDB(str(tmp_path / "idx.db"))
    db.mark_session_indexed("sess-3", page_slug="p3")
    db.set_session_hash("sess-3", "abc123")
    assert db.get_session_hash("sess-3") == "abc123"
    db.close()


def test_session_hash_unknown_session(tmp_path):
    db = IndexDB(str(tmp_path / "idx.db"))
    assert db.get_session_hash("does-not-exist") == ""
    db.close()
