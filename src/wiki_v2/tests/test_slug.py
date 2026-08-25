import pytest
from uuid import uuid4
from wiki_v2.slug import make_unique_slug, slugify

def test_make_unique_slug_free():
    assert make_unique_slug("base", set()) == "base"

def test_make_unique_slug_taken():
    assert make_unique_slug("base", {"base"}) == "base-2"

def test_make_unique_slug_limit_reached():
    # Create 10000 existing slugs: base and base-2 to base-10000 (wait, loop is n < 10000)
    # The code says: while n < 10000 and f"{base}-{n}" in existing: n += 1
    # If n reaches 10000, it returns uuid4().hex[:8]
    existing = {"base"} | {f"base-{i}" for i in range(2, 10001)}
    result = make_unique_slug("base", existing)
    assert len(result) == 8
    assert result != "base-10000" # It should be the uuid fallback

def test_make_unique_slug_with_session():
    # Test session prefix if base is taken but session candidate is free
    existing = {"base"}
    session_id = "abcde123456789"
    result = make_unique_slug("base", existing, session_id=session_id)
    assert result == "base-abcde123"

def test_slugify():
    assert slugify("Hello World!") == "hello-world"
    # The current implementation of slugify preserves existing hyphens: 
    # re.sub(r"[\s_]+", "-", text) only replaces whitespace/underscores.
    assert slugify("  Too   Many--Spaces  ") == "too-many--spaces"
