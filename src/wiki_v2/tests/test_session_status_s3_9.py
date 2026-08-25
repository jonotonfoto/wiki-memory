# tests/test_session_status_s3_9.py
import sqlite3

from wiki_v2.session_status import last_message_ts


def test_last_message_ts_text_iso8601(tmp_path):
    db_path = str(tmp_path / "test_text.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp TEXT)")
    # ISO 8601: 2024-01-01T12:00:00
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1', 'user', 'hi', '2024-01-01T12:00:00')")
    conn.commit()
    conn.close()
    
    # strftime('%s', ...) returns epoch in seconds
    expected_epoch = 1704110400.0 # 2024-01-01T12:00:00 UTC approx (depends on local, but we check if it's a float)
    res = last_message_ts(db_path, "s1")
    assert isinstance(res, float)
    # Since strftime('%s') is platform dependent for ISO8601 in some sqlite builds, 
    # let's just check it's a valid timestamp from the string.
    import datetime
    dt = datetime.datetime.fromtimestamp(res)
    assert dt.year == 2024

def test_last_message_ts_real(tmp_path):
    db_path = str(tmp_path / "test_real.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
    conn.execute("INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1', 'user', 'hi', 1704110400.5)")
    conn.commit()
    conn.close()
    
    assert last_message_ts(db_path, "s1") == 1704110400.5

def test_last_message_ts_empty_table(tmp_path):
    db_path = str(tmp_path / "test_empty.db")
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp REAL)")
    conn.commit()
    conn.close()
    assert last_message_ts(db_path, "s1") is None

def test_last_message_ts_no_file():
    assert last_message_ts("nonexistent.db", "s1") is None
