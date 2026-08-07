# tests/test_slug.py
from wiki_v2.slug import slugify, make_unique_slug

def test_slugify_basic():
    assert slugify("Проблема подключения немотрона!") == "проблема-подключения-немотрона"

def test_slugify_strips_and_truncates():
    s = slugify("  " + "слово " * 30)
    assert len(s) <= 60


def test_unique_slug_no_collision():
    existing = {"my-page"}
    assert make_unique_slug("my-page", existing) == "my-page-2"

def test_unique_slug_multiple_collisions():
    existing = {"a", "a-2", "a-3"}
    assert make_unique_slug("a", existing) == "a-4"


def test_untitled_gets_suffix():
    existing = {"untitled"}
    slug = make_unique_slug("untitled", existing, session_id="abc123def456")
    assert slug == "untitled-abc123de"
