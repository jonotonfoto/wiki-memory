# tests/test_session_status.py
import sqlite3

from wiki_v2.session_status import is_session_finished, last_message_ts


def test_active_session_not_finished():
    now = 1_000_000.0
    assert is_session_finished(last_msg_ts=now - 10, now=now, idle_minutes=32) is False


def test_idle_just_under_threshold_not_finished():
    now = 1_000_000.0
    assert is_session_finished(last_msg_ts=now - 31 * 60, now=now, idle_minutes=32) is False


def test_idle_at_threshold_finished():
    now = 1_000_000.0
    assert is_session_finished(last_msg_ts=now - 32 * 60, now=now, idle_minutes=32) is True


def test_long_idle_finished():
    now = 1_000_000.0
    assert is_session_finished(last_msg_ts=now - 3600, now=now, idle_minutes=32) is True


def test_no_message_ts_finished():
    # нет метки времени — считаем завершённой (безопасно)
    assert is_session_finished(last_msg_ts=None, now=1_000_000.0, idle_minutes=32) is True


def test_last_message_ts_from_db(tmp_path):
    db_path = str(tmp_path / "state.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT, started_at REAL)")
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT,"
                 " role TEXT, content TEXT, timestamp REAL)")
    conn.execute("INSERT INTO sessions VALUES ('s1','t',100)")
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES"
                 " ('s1','user','a',500)")
    conn.execute("INSERT INTO messages (session_id,role,content,timestamp) VALUES"
                 " ('s1','assistant','b',700)")
    conn.commit()
    conn.close()

    assert last_message_ts(db_path, "s1") == 700
    assert last_message_ts(db_path, "nonexistent") is None
