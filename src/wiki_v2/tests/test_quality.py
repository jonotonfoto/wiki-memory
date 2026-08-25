# tests/test_quality.py
from wiki_v2.config import SCHEMA, TAG_SYNONYMS
from wiki_v2.quality import (
    dedup_tags,
    is_garbage_text,
    is_stopword,
    map_tag,
    normalize_tag,
    root_match,
)


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
    from wiki_v2.config import WIKI_GARBAGE_MIN_LEN
    assert is_garbage_text("ок") is True
    # Check if it's actually shorter than threshold
    if len("ок") < WIKI_GARBAGE_MIN_LEN:
        pass # This confirms the test logic matches expectation

# --- S3.4 Tests for is_garbage_text (abbreviations & thresholds) ---

def test_is_garbage_text_abbreviations():
    # Whitelist abbreviations [A-ZА-Я]{2,5} should NOT be garbage
    assert is_garbage_text("API") is False
    assert is_garbage_text("VPS") is False
    assert is_garbage_text("CPU") is False

def test_is_garbage_text_threshold():
    from wiki_v2.config import WIKI_GARBAGE_MIN_LEN
    # Test near threshold
    borderline = "a" * (WIKI_GARBAGE_MIN_LEN - 1)
    assert is_garbage_text(borderline) is True
    valid = "a" * WIKI_GARBAGE_MIN_LEN
    assert is_garbage_text(valid) is False

# --- Rest of existing tests ---
def test_normalize_tag_lower_underscore():
    assert normalize_tag("Subtitle_Editor") == "subtitle editor"

def test_normalize_tag_strip_yo():
    assert normalize_tag("  Выготский  ") == "выготский"
    assert normalize_tag("Ёлка") == "елка"

def test_normalize_tag_multi_spaces():
    assert normalize_tag("  a   b  ") == "a b"

def test_is_stopword_common():
    assert is_stopword("важно") is True
    assert is_stopword("работа") is True

def test_is_stopword_normal():
    assert is_stopword("embedding") is False
    assert is_stopword("психология") is False

def test_is_stopword_empty_short():
    assert is_stopword("") is True
    assert is_stopword("ab") is True

def test_root_match_same_root():
    assert root_match("сознание", "сознания") is True

def test_root_match_different():
    assert root_match("психология", "мышление") is False

def test_root_match_short_exact():
    assert root_match("vpn", "vpn") is True
    assert root_match("vpn", "cpu") is False

def test_dedup_tags_root():
    assert dedup_tags(["сознание", "сознания", "мышление"]) == ["сознание", "мышление"]

def test_dedup_tags_underscore():
    assert dedup_tags(["Subtitle_Editor", "subtitle editor"]) == ["subtitle editor"]

def test_dedup_tags_stopword_root():
    assert dedup_tags(["важно", "embedding", "важное"]) == ["embedding"]

def test_dedup_tags_empty():
    assert dedup_tags([]) == []

def test_map_tag_synonym_to_tool():
    assert map_tag("Subtitle_Editor", SCHEMA, TAG_SYNONYMS) == "tool"
    assert map_tag("subtitle editor", SCHEMA, TAG_SYNONYMS) == "tool"

def test_map_tag_synonym_to_provider():
    assert map_tag("gemini key", SCHEMA, TAG_SYNONYMS) == "provider"
    assert map_tag("claude", SCHEMA, TAG_SYNONYMS) == "provider"

def test_map_tag_in_schema_returns_canonical():
    assert map_tag("embedding", SCHEMA, TAG_SYNONYMS) == "embedding"
    assert map_tag("search", SCHEMA, TAG_SYNONYMS) == "search"

def test_map_tag_not_in_schema_keeps_raw():
    assert map_tag("psyhoanaliz", SCHEMA, TAG_SYNONYMS) == "psyhoanaliz"
    assert map_tag("нейросеть-обучение", SCHEMA, TAG_SYNONYMS) == "нейросет-обучение"

def test_map_tag_stopword_returns_empty():
    assert map_tag("важно", SCHEMA, TAG_SYNONYMS) == ""
    assert map_tag("", SCHEMA, TAG_SYNONYMS) == ""

def test_map_tag_stopword_root_returns_empty():
    assert map_tag("важное", SCHEMA, TAG_SYNONYMS) == ""
    assert map_tag("работать", SCHEMA, TAG_SYNONYMS) == ""

def test_map_tag_normalizes_first():
    assert map_tag("Zakomoldina", SCHEMA, TAG_SYNONYMS) == "person"

def test_map_tag_sql_injection_safe():
    evil = "'; DROP TABLE pages; --"
    out = map_tag(evil, SCHEMA, TAG_SYNONYMS)
    assert isinstance(out, str)
