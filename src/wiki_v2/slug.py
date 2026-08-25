# slug.py
"""Slug generation with uniqueness guarantees."""
import re
from uuid import uuid4


def slugify(text: str, max_len: int = 60) -> str:
    text = (text or "").lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:max_len].strip("-")


def make_unique_slug(base: str, existing: set, session_id: str = "") -> str:
    """Return base or base-N (or base-<session prefix>) not present in existing."""
    base = base or "page"
    if base not in existing:
        return base
    if session_id:
        cand = f"{base}-{session_id[:8]}"
        if cand not in existing:
            return cand
    n = 2
    while n < 10000 and f"{base}-{n}" in existing:
        n += 1
    
    if n >= 10000:
        return uuid4().hex[:8]
        
    return f"{base}-{n}"
