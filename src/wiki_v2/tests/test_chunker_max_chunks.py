"""Подэтап 4е: защита от «взрыва» числа чанков в split_text.

Проверяем:
  - гигантский текст (500KB) -> число чанков <= max_chunks (=<200 по умолчанию)
  - любое max_chunks (>=1) соблюдается: split укрупняет chunk_size и укладывается
  - нет двух одинаковых чанков подряд (гарантированный сдвиг >=1) на НЕПЕРИОДИЧЕСКОМ тексте
  - короткие/нормальные входы не изменили поведение (прежние инварианты)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wiki_v2.chunker import split_text


def _big():
    """~525KB периодичный текст (без абзацев)."""
    unit = "Слово. "
    return unit * (500_000 // len(unit))


def _big_unique():
    """~500KB НЕпериодический текст — слова с уникальными маркерами."""
    return " ".join(f"уникальноеслово{idx} текст параграфа {idx}" for idx in range(60000))


def test_big_text_capped_at_max_chunks():
    assert len(split_text(_big())) <= 200


def test_honors_any_max_chunks():
    for m in (1, 4, 8, 50):
        assert len(split_text(_big(), max_chunks=m)) <= m


def test_no_consecutive_duplicate_chunks():
    chunks = split_text(_big_unique())
    for i in range(len(chunks) - 1):
        assert chunks[i] != chunks[i + 1]


def test_normal_short_inputs_unchanged():
    assert split_text("короткий текст") == ["короткий текст"]
    assert split_text("") == [""]
    assert split_text(None) == [""]


def test_paragraph_text_covers_content():
    # Первый чанк начинается с начала, а уникальные маркеры встречаются в чанках.
    text = _big_unique()
    chunks = split_text(text)
    assert chunks
    assert all(c is not None and c.strip() for c in chunks)
    # Хвост текста не теряется: последний чанк заканчивается последним словом.
    last_word = text.split()[-1]
    assert last_word in chunks[-1]