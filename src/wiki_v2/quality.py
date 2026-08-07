# quality.py
"""Detection of corrupted model output (spaced-out letters, too-short text)."""
import re

_SINGLE_CHAR_RUN = re.compile(r"(?:\b\w\b[ ]+){4,}")  # 5+ single chars separated by spaces


def is_garbage_text(text: str, min_len: int = 30) -> bool:
    """True if text is corrupted extraction garbage.

    Garbage signatures:
    - empty / whitespace-only
    - shorter than min_len
    - contains runs of 5+ single characters separated by spaces
      ("П о л ь з в а т е", "N V I D I A")
    - single-char token ratio > 40% of all tokens
    """
    if not text or not text.strip():
        return True
    text = text.strip()
    if len(text) < min_len:
        return True
    if _SINGLE_CHAR_RUN.search(text):
        return True
    tokens = text.split()
    if tokens:
        single = sum(1 for t in tokens if len(t) == 1)
        if single / len(tokens) > 0.4:
            return True
    return False
