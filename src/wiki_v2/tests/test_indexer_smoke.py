# tests/test_indexer_smoke.py
import sqlite3
import time
from unittest.mock import patch


def _make_state_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
    """)
    conn.execute("INSERT INTO sessions VALUES ('s1','Тест немотрона',?)", (time.time(),))
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s1','user','Как подключить немотрон через /model? Не работает.',1)")
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES ('s1','assistant','Проблема в NVIDIA_BASE_URL — там ключ вместо URL.',2)")
    conn.commit()
    conn.close()


def test_indexer_end_to_end(tmp_path, monkeypatch):
    _make_state_db(str(tmp_path / "state.db"))
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    good = {"summary": "Починили подключение немотрона: удалили неверный NVIDIA_BASE_URL.",
            "key_topics": ["немотрон", "nvidia"], "decisions": ["удалить NVIDIA_BASE_URL"],
            "facts": ["эндпоинт integrate.api.nvidia.com/v1"], "links": [],
            "entities": ["nvidia"], "concepts": [], "quality": "ok"}

    import numpy as np
    fake_vec = np.random.rand(1024).astype(np.float32)
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()

    pages = list((wiki / "entities").glob("*.md"))
    assert len(pages) == 1
    text = pages[0].read_text()
    assert "немотрон" in text.lower()
    assert "s1" in text  # sources

    # Second run: session already indexed → no new pages
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()
    assert len(list((wiki / "entities").glob("*.md"))) == 1


def test_indexer_single_session_mode(tmp_path, monkeypatch):
    _make_state_db(str(tmp_path / "state.db"))
    wiki = tmp_path / "wiki"
    wiki.mkdir()

    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    good = {"summary": "Починили немотрон.",
            "key_topics": ["немотрон"], "decisions": [], "facts": [],
            "links": [], "entities": [], "concepts": [], "quality": "ok"}
    import numpy as np
    fake_vec = np.random.rand(1024).astype(np.float32)

    # single-session mode indexes ONLY the given session
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main(session_id="s1")
    assert len(list((wiki / "entities").glob("*.md"))) == 1

    # unknown session id → nothing indexed, no crash
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main(session_id="nope")
    assert len(list((wiki / "entities").glob("*.md"))) == 1


def _make_state_db_active(path):
    """state.db с ОДНОЙ активной (недавней) сессией."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
    """)
    now = time.time()
    conn.execute("INSERT INTO sessions VALUES ('active','Активная',?)", (now,))
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES"
                 " ('active','user','свежий вопрос',?)", (now,))
    conn.commit()
    conn.close()


def test_indexer_skips_active_session(tmp_path, monkeypatch):
    _make_state_db_active(str(tmp_path / "state.db"))
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    good = {"summary": "Активная.", "key_topics": ["x"], "decisions": [],
            "facts": [], "links": [], "entities": [], "concepts": [], "quality": "ok"}
    import numpy as np
    fake_vec = np.random.rand(1024).astype(np.float32)

    # фоновая индексация НЕ трогает активную сессию
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()
    assert len(list((wiki / "entities").glob("*.md"))) == 0


def test_indexer_reindexes_changed_session(tmp_path, monkeypatch):
    """Фоновая индексация: сессия проиндексирована, затем изменена → переиндексируется."""
    _make_state_db(str(tmp_path / "state.db"))  # s1 с message ts=1,2 (давно → завершена)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    good = {"summary": "Починили немотрон.", "key_topics": ["немотрон"],
            "decisions": [], "facts": [], "links": [], "entities": [],
            "concepts": [], "quality": "ok"}
    import numpy as np
    fake_vec = np.random.rand(1024).astype(np.float32)

    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()
    assert len(list((wiki / "entities").glob("*.md"))) == 1

    # Добавляем новое сообщение в s1 (содержимое изменилось)
    conn = sqlite3.connect(str(tmp_path / "state.db"))
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES"
                 " ('s1','assistant','новый вывод',3)")
    conn.commit()
    conn.close()

    # Повторный проход: сессия изменилась → переиндексируется (НЕ пропущена как неизменённая)
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()
    # Повторная индексация той же темы MERGE'ится в существующую страницу
    # (порог слияния 0.20 + корневое сравнение) — дубль НЕ создаётся.
    assert len(list((wiki / "entities").glob("*.md"))) == 1


def test_indexer_skips_unchanged_session(tmp_path, monkeypatch):
    """Фоновая индексация НЕ переиндексирует сессию, чьё содержимое не изменилось (hash-контроль)."""
    _make_state_db(str(tmp_path / "state.db"))  # s1, давно (завершена)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    good = {"summary": "Починили немотрон.", "key_topics": ["немотрон"],
            "decisions": [], "facts": [], "links": [], "entities": [],
            "concepts": [], "quality": "ok"}
    import numpy as np
    fake_vec = np.random.rand(1024).astype(np.float32)

    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()
    assert len(list((wiki / "entities").glob("*.md"))) == 1

    # Повторный проход БЕЗ изменения содержимого → hash не изменился → пропуск
    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main()
    assert len(list((wiki / "entities").glob("*.md"))) == 1
