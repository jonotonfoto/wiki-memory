# tests/test_quality.py
from wiki_v2.quality import is_garbage_text

def test_spaced_cyrillic_is_garbage():
    assert is_garbage_text("П о л ь з в а т е   с к н у я") is True

def test_spaced_latin_is_garbage():
    assert is_garbage_text("N V I D I A _ B S E U R L") is True

def test_normal_russian_not_garbage():
    assert is_garbage_text("Пользователь спросил про подключение немотрона через /model") is False

def test_normal_english_not_garbage():
    assert is_garbage_text("The user asked about NVIDIA endpoints") is False

def test_empty_is_garbage():
    assert is_garbage_text("") is True
    assert is_garbage_text("   ") is True

def test_short_summary_is_garbage():
    assert is_garbage_text("ок") is True
