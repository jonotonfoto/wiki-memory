# quality.py
"""Detection of corrupted model output (spaced-out letters, too-short text) + tag normalization."""
import re

_SINGLE_CHAR_RUN = re.compile(r"(?:\b\w\b[ ]+){4,}")  # 5+ single chars separated by spaces

# S3.4: all-caps abbreviation (2-5 chars) — не мусор ("API","VPS","CPU")
_ALL_CAPS_ABBR = re.compile(r"\b[A-ZА-Я]{2,5}\b")

_STOPWORDS = frozenset({
    "важно", "работа", "система", "вопрос", "проблема", "нужно", "можно",
    "тема", "основное", "дальше", "сейчас",
})
_ROOT_MIN_LEN = 5


def is_garbage_text(text: str, min_len: int = None) -> bool:
    """True if text is corrupted extraction garbage.

    Garbage signatures:
    - empty / whitespace-only
    - shorter than min_len (defaults to config.WIKI_GARBAGE_MIN_LEN)
    - contains runs of 5+ single characters separated by spaces
      ("П о л    ь з в а т е", "N V I D I A")
    - single-char token ratio > 40% of all tokens

    Exceptions (NOT garbage): all-caps abbreviations ("API", "VPS", "CPU")
    even when short.
    """
    if not text or not text.strip():
        return True
    text = text.strip()

    from . import config
    effective_min_len = min_len if min_len is not None else config.WIKI_GARBAGE_MIN_LEN

    # S3.4: all-caps abbreviation (2-5 chars) — НЕ мусор, даже если короткая.
    # Проверяем ДО length-check, иначе "API"/"VPS"/"CPU" отсеются как короткие.
    if _ALL_CAPS_ABBR.fullmatch(text):
        return False

    if len(text) < effective_min_len:
        return True
    if _SINGLE_CHAR_RUN.search(text):
        return True

    tokens = text.split()
    if tokens:
        single = sum(1 for t in tokens if len(t) == 1)
        if single / len(tokens) > 0.4:
            return True
    return False


def normalize_tag(tag: str) -> str:
    """Normalize a raw tag: lowercase, ё→е, ъ/ь→'', '_'→space, collapse spaces."""
    text = tag.lower()
    for old, new in {"ё": "е", "ъ": "", "ь": ""}.items():
        text = text.replace(old, new)
    text = text.replace("_", " ")
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


# ── Детектор мусорного чанка (эмпирика 2026-08-14) ────────────────────────
# Цель: не индексировать ЯВНО мусорные чанки. Выбран КОНСЕРВАТИВНЫЙ подход
# (решение пользователя): отсекать только то, что ТОЧНО мусор, чтобы не потерять
# полезные чанки. Обрывки текста и голые списки терминов НЕ трогаем.
# Срабатывает на: голый список Windows-путей (precision=1.0, 0 ложных на эмпирике)
# и слишком короткие чанки (<6 слов). Калибровка на большой выборке — позже.
_PATH_RE = re.compile(r"[A-Za-z]:\\[\\\w .-]")  # Windows-путь C:\...


def is_junk_chunk(text: str) -> bool:
    """True если чанк — ЯВНО мусорный (его не надо индексировать).

    Консервативно (по решению пользователя 2026-08-14):
    - слишком короткий (< 6 слов) — нет информации
    - голый список Windows-путей: >50% строк — пути, и вне путей почти нет смысла
    """
    if not text or not text.strip():
        return True
    words = text.split()
    if len(words) < 6:
        return True
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return True
    path_lines = sum(1 for l in lines if _PATH_RE.search(l))
    if path_lines / len(lines) > 0.5:
        # голый список путей: проверяем, есть ли связный смысл вне путей
        non_path = [w for w in words if not _PATH_RE.search(w)]
        return len(non_path) < 6
    return False


def is_stopword(tag: str) -> bool:
    """True for stop-words, overly generic words, empty or too-short (<3) tags."""
    if not tag:
        return True
    if len(tag) < 3:
        return True
    if tag in _STOPWORDS:
        return True
    return False


def root_match(a: str, b: str) -> bool:
    """Root comparison of two already-normalized tags.

    If both >= 5 chars — compare first 5 chars (a[:5] == b[:5]).
    Otherwise — exact match (a == b).
    """
    if len(a) >= _ROOT_MIN_LEN and len(b) >= _ROOT_MIN_LEN:
        return a[:_ROOT_MIN_LEN] == b[:_ROOT_MIN_LEN]
    return a == b


def dedup_tags(tags: list) -> list:
    """Normalize, drop stopwords/empty, remove duplicates and near-duplicates by root_match.

    When two tags share a root, the FIRST (by original order) is kept.
    Tags whose root matches a stopword are dropped too (e.g. "важное" ~ "важно").
    Returns a list of unique normalized tags.
    """
    result: list[str] = []
    for raw in tags:
        norm = normalize_tag(raw)
        if is_stopword(norm):
            continue
        # отбрасываем тег, чей корень совпадает со стоп-словом («важное» ≈ «важно»)
        if any(is_stopword(s) and root_match(norm, s) for s in _STOPWORDS):
            continue
        if any(root_match(norm, existing) for existing in result):
            continue
        result.append(norm)
    return result


def map_tag(tag: str, schema: list, synonyms: dict | None = None) -> str:
    """Map a tag to controlled taxonomy.

    Steps:
    1. normalize_tag(tag)
    2. if is_stopword(norm) -> return "" (discard)
    3. if root_match(norm, stopword) for any stopword -> return "" (discard;
       consistent with dedup_tags, e.g. "важное" ~ stopword "важно")
    4. if synonyms and norm in synonyms -> return synonyms[norm]
    5. if root_match(norm, s) for any s in schema -> return that s
    6. else -> return norm (normalized tag, not discarded)
    """
    norm = normalize_tag(tag)
    if is_stopword(norm):
        return ""
    # тег, чежей корень совпадает со стоп-словом («важное» ≈ «важно») — отбросить
    if any(root_match(norm, s) for s in _STOPWORDS if is_stopword(s)):
        return ""
    if synonyms and norm in synonyms:
        return synonyms[norm]
    for s in schema:
        if root_match(norm, s):
            return s
    return norm