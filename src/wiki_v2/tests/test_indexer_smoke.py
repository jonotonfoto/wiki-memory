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
    text = pages[0].read_text(encoding="utf-8")
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


def test_is_junk_title():
    import wiki_v2.indexer as idx
    # Служебные/шаблонные заголовки → мусор
    assert idx._is_junk_title("We need to produce a title for this session")
    assert idx._is_junk_title("Produce a title for the conversation")
    assert idx._is_junk_title("untitled")
    assert idx._is_junk_title("")
    assert idx._is_junk_title("без названия")
    # Осмысленные заголовки НЕ трогаем
    assert not idx._is_junk_title("Починили немотрон")
    assert not idx._is_junk_title("Как подключить LM Studio")
    assert not idx._is_junk_title("We need to test embedding properly")


def _make_state_db_service_title(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,
                               role TEXT, content TEXT, timestamp REAL);
    """)
    conn.execute("INSERT INTO sessions VALUES "
                 "('srv','We need to produce a title for this session',?)", (time.time(),))
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES"
                 " ('srv','user','Настроим семантический поиск через LM Studio',1)")
    conn.commit()
    conn.close()


def test_indexer_service_title_falls_back_to_first_user(tmp_path, monkeypatch):
    """Сессия со служебным заголовком → страница называется по первой реплике пользователя."""
    _make_state_db_service_title(str(tmp_path / "state.db"))
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    good = {"summary": "Настроили поиск.",
            "key_topics": ["семантический поиск"], "decisions": [],
            "facts": [], "links": [], "entities": [], "concepts": [], "quality": "ok"}
    import numpy as np
    fake_vec = np.random.rand(1024).astype(np.float32)

    with patch("wiki_v2.indexer.extract_content", return_value=good), \
         patch("wiki_v2.indexer.embed", return_value=[fake_vec]):
        idx.main(session_id="srv")

    pages = list((wiki / "entities").glob("*.md"))
    assert len(pages) == 1
    text = pages[0].read_text(encoding="utf-8")
    # Название взято из первой реплики пользователя, НЕ из шаблона "we need to produce a title"
    assert "we-need-to-produce-a-title" not in pages[0].name
    assert "семантический" in text.lower()


def test_indexer_long_session_extracts_before_map(tmp_path, monkeypatch):
    """Длинная сессия (>8KB): core-экстракция идёт ДО map-тегов и получает свежий бюджет.

    Регрессия (P1): раньше map_chunk_tags съедал весь бюджет LLM-вызовов, и финальный
    extract_content возвращал fallback по построению (boilerplate-мусор у длинных страниц).
    Теперь _reset_llm_budget() вызывается на входе, extract_content — первым.
    """
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("WIKI_SKIP_TRANSIENT_SESSIONS", "False")

    import importlib

    import wiki_v2.indexer as idx
    from wiki_v2 import config as _cfg
    _cfg.reload()
    importlib.reload(idx)

    long_text = ("дальний текст сессии о немотроне и индексации. " * 500)[:9000]

    good = {"summary": "Разобрались в длинной сессии.",
            "key_topics": ["немотрон"], "decisions": [], "facts": [],
            "links": [], "entities": [], "concepts": [], "quality": "ok"}

    calls = []
    with patch("wiki_v2.indexer.get_session_text", return_value=long_text), \
         patch("wiki_v2.indexer.session_raw_text", return_value=long_text), \
         patch("wiki_v2.indexer._reset_llm_budget",
               side_effect=lambda: calls.append("reset")), \
         patch("wiki_v2.indexer.split_text", return_value=["chunk0", "chunk1"]), \
         patch("wiki_v2.indexer.extract_content",
               side_effect=lambda *a, **k: (calls.append("extract"), good)[1]), \
         patch("wiki_v2.indexer.map_chunk_tags",
               side_effect=lambda *a, **k: (calls.append("map"), {0: ["т"]})[1]), \
         patch("wiki_v2.indexer.reduce_chunk_tags", return_value=["немотрон"]), \
         patch("wiki_v2.indexer.embed_multivector", return_value={}), \
         patch("wiki_v2.indexer.embed_chunks", return_value={}):
        db = idx.IndexDB(str(wiki / "db.sqlite"))
        try:
            slug = idx.process_session(db, {"id": "s1", "title": "Длинная сессия",
                                            "started_at": 1})
        finally:
            db.close()

    # EXTRACT обязан идти ДО MAP (приоритет core-контента над тегами)
    assert calls.index("extract") < calls.index("map")
    # На входе сессии бюджет сбрасывается (свежий для этой сессии)
    assert calls[0] == "reset"
    # Страница фервстала (CREATE), а не fallback-пропуск
    assert slug


def test_indexer_stale_stop_flag_does_not_block(tmp_path, monkeypatch):
    """P3: застойный .stop_request не блокирует индексацию бесконечно.

    Новый прогон main() снимает оставшийся флаг в начале → индексация идёт.
    Раньше залипший флаг (краш/руками/ошибка stop_extraction) заставлял main
    сразу break — никакая сессия не индексировалась до ручного удаления файла.
    """
    _make_state_db(str(tmp_path / "state.db"))  # s1 (далеко → завершена)
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    monkeypatch.setenv("WIKI_PATH", str(wiki))
    monkeypatch.setenv("HERMES_STATE_DB", str(tmp_path / "state.db"))

    # Оставляем «залипший» флаг остановки на диске ДО запуска
    stop_flag = wiki / ".stop_request"
    stop_flag.write_text("1", encoding="utf-8")

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

    # Сессия проиндексирована, НЕ остановлена залипшим флагом
    assert len(list((wiki / "entities").glob("*.md"))) == 1
    # Флаг снят (одноразовая остановка) — следующий прогон тоже не заблокирован
    assert not stop_flag.exists()

