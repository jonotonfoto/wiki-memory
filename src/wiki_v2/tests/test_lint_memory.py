import sqlite3

import pytest
from wiki_v2.lint_memory import lint_facts, lint_report


def create_test_db(tmp_path):
    """Helper to create a test SQLite database."""
    db_path = tmp_path / "test_wiki.db"
    return db_path

def setup_db_schema(db_path, columns=None):
    """Helper to set up the pages table with specific columns."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    if columns is None:
        columns = ["slug TEXT PRIMARY KEY", "title TEXT", "contested INTEGER DEFAULT 0", "contradictions TEXT"]
    cols_str = ", ".join(columns)
    cursor.execute(f"CREATE TABLE pages ({cols_str})")
    conn.commit()
    conn.close()

def test_lint_contested(tmp_path):
    """1. test_lint_contested: создай БД с таблицей pages (slug,title,contested,contradictions) 
    и 1 строкой contested=1, contradictions='["a","b"]'. lint_facts(db) → в result["contested"] есть эта строка, contradictions==['a','b']."""
    db_path = create_test_db(tmp_path)
    setup_db_schema(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pages (slug, title, contested, contradictions) VALUES (?, ?, ?, ?)", 
                   ("test-slug", "Test Title", 1, '["a", "b"]'))
    conn.commit()
    conn.close()

    result = lint_facts(str(db_path))
    assert len(result["contested"]) == 1
    assert result["contested"][0]["slug"] == "test-slug"
    assert result["contested"][0]["title"] == "Test Title"
    assert result["contested"][0]["contradictions"] == ["a", "b"]

def test_lint_no_contested(tmp_path):
    """2. test_lint_no_contested: БД без contested строк → result["contested"]==[]."""
    db_path = create_test_db(tmp_path)
    setup_db_schema(db_path)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pages (slug, title, contested) VALUES (?, ?, ?)", ("no-contested", "No Contested", 0))
    conn.commit()
    conn.close()

    result = lint_facts(str(db_path))
    assert result["contested"] == []

def test_lint_report_empty(tmp_path):
    """3. test_lint_report_empty: lint_report({"contested":[],"stale":[],"total_pages":0}) → содержит \"пуста\" (или \"Проверено\")."""
    # Based on code, it returns "База данных пуста или недоступна." if total_pages == 0.
    result = {"contested": [], "stale": [], "total_pages": 0}
    report = lint_report(result)
    assert "пуста" in report or "недоступна" in report

def test_lint_fail_open_nonexistent_db():
    """4. test_lint_fail_open_nonexistent_db: lint_facts(\"/nonexistent/x.db\") → не бросает, возвращает dict (total_pages==0)."""
    result = lint_facts("/nonexistent/path_to_nothing_at_all_12345.db")
    assert isinstance(result, dict)
    assert result["total_pages"] == 0
    assert "contested" in result
    assert "stale" in result

def test_lint_stale_no_dates(tmp_path):
    """5. test_lint_stale_no_dates: БД с pages без колонок дат → result["stale"] содержит все (days=None)."""
    db_path = create_test_db(tmp_path)
    # Create table WITHOUT date columns
    setup_db_schema(db_path, columns=["slug TEXT PRIMARY KEY", "title TEXT"])
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO pages (slug, title) VALUES (?, ?)", ("no-date-page", "No Date Page"))
    conn.commit()
    conn.close()

    result = lint_facts(str(db_path))
    assert len(result["stale"]) == 1
    assert result["stale"][0]["slug"] == "no-date-page"
    assert result["stale"][0]["days"] is None

def test_lint_report_with_data(tmp_path):
    """Extra: Verify report formatting with some data."""
    result = {
        "contested": [{"slug": "s1", "title": "T1", "contradictions": ["c1"]}],
        "stale": [{"slug": "s2", "title": "T2", "days": 45}],
        "total_pages": 2,
        "checked_at": 123456789.0
    }
    report = lint_report(result)
    assert "Проверено страниц: 2" in report
    assert "s1: ['c1']" in report
    assert "s2 (45 дней без подтверждения)" in report

if __name__ == "__main__":
    pytest.main([__file__])
