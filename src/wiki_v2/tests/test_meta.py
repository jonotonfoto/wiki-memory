import json
from pathlib import Path

from wiki_v2.pages import write_meta


def test_write_meta_creates_json(tmp_path):
    """1. write_meta создаёт .json рядом с .md."""
    page_md = tmp_path / "test_page.md"
    page_md.write_text("This is a test page content.", encoding="utf-8")
    
    meta_data = {
        'source': 'x',
        'created': '2024-01-01',
        'updated': '2024-01-02',
        'tags': ['vps']
    }
    
    write_meta(str(page_md), meta_data)
    
    expected_json = tmp_path / "test_page.json"
    assert expected_json.exists(), "JSON file was not created next to the .md file."
    
    with open(expected_json, 'r', encoding='utf-8') as f:
        actual_meta = json.load(f)
    
    assert actual_meta == meta_data, f"Metadata mismatch. Expected {meta_data}, got {actual_meta}"

def test_write_meta_is_atomic(tmp_path):
    """2. write_meta атомарно: после записи нет .tmp файла."""
    page_md = tmp_path / "test_atomic.md"
    page_md.write_text("Atomic test content.", encoding="utf-8")
    
    meta_data = {"key": "value"}
    write_meta(str(page_md), meta_data)
    
    tmp_file = tmp_path / "test_atomic.json.tmp"
    assert not tmp_file.exists(), ".tmp file was left behind after write_meta."

def test_write_meta_fail_open(tmp_path):
    """3. write_meta fail-open: передай путь в несуществующую директорию -> не бросает."""
    # Создаём файл в папке, которой НЕ существует
    non_existent_dir = tmp_path / "no_such_dir"
    page_md = non_existent_dir / "test_fail.md"
    
    meta_data = {"key": "value"}
    
    # Функция должна поймать Exception и просто вывести [WARN]
    try:
        write_meta(str(page_md), meta_data)
    except Exception as e:
        pytest.fail(f"write_meta raised an exception instead of failing open: {e}")

def test_write_meta_structure():
    """4. (Интеграционный/Структурный) Проверка структуры метаданных."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        page_md = tmp_path / "test_struct.md"
        page_md.write_text("Structure test.", encoding="utf-8")
        
        meta_data = {
            'source': 'test',
            'created': '2024-01-01',
            'updated': '2024-01-01',
            'tags': ['tag1', 'tag2']
        }
        
        write_meta(str(page_md), meta_data)
        expected_json = tmp_path / "test_struct.json"
        
        assert expected_json.exists()
        with open(expected_json, 'r', encoding='utf-8') as f:
            actual = json.load(f)
        
        assert isinstance(actual['tags'], list)
        assert len(actual['tags']) == 2
        assert actual['source'] == 'test'

if __name__ == "__main__":
    import pytest
    pytest.main([__file__])
