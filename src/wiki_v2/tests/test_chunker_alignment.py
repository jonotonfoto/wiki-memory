# tests/test_chunker_alignment.py — границы чанков не режут слова (фикс 2026-08-25).
# Раньше старт следующего окна считался голым смещением без привязки к границе —
# швы вида "_9f2189", "обходимостью", "риант коллизии" в инжекте.
import itertools

from wiki_v2.chunker import merge_span_groups, split_text, split_text_spans

PAGE = (
    "---\n"
    'title: "Тестовая страница"\n'
    "sources: [20260821_231049_9f2189]\n"
    "---\n"
    "\n"
    "# Тестовая страница\n"
    "\n"
    "Разговор о создании ролика на юбилей с темой супергеройки и необходимостью коллизии для сценария.\n"
    "\n"
    "## Темы\n"
    "- мультфильм\n"
    "- супергеройская тематика\n"
    "- музыкальные видео\n"
    "- анимация\n"
    "- сценарий\n"
    "\n"
    "## Решения\n"
    "- Вариант коллизии: потеря голоса как метафора\n"
    '- Вариант коллизии: "Голос стих"\n'
    "\n"
    "## Факты\n"
    "- Исполнитель родился 30 апреля 1967 года в Варне\n"
    "- Народный артист РФ (2008)\n"
    "\n"
    "## Сущности\n"
    "- Известный певец\n"
    "- Человек-паук\n"
    "\n"
    "## Источники\n"
    "- 20260821_231049_9f2189\n"
)


def _boundary_ok(src, chunk):
    """Чанк начинается на границе источника (начало текста или после \\n/пробела)
    и заканчивается перед границей (конец текста или \\n/пробел)."""
    p = src.find(chunk)
    if p == -1:
        return False
    if p > 0 and src[p - 1] not in "\n ":
        return False
    q = p + len(chunk)
    return not (q < len(src) and src[q] not in "\n ")


def test_no_midword_seams_on_page_like_text():
    chunks = split_text(PAGE)
    assert len(chunks) > 1
    for c in chunks:
        assert _boundary_ok(PAGE, c), f"шов у чанка: {c[:60]!r}"


def test_section_headers_stay_intact():
    chunks = split_text(PAGE)
    joined = "\n".join(chunks)
    for header in ("## Темы", "## Решения", "## Факты", "## Сущности", "## Источники"):
        assert header in joined, f"{header!r} разрезан между чанками"


def test_chunk_size_respected():
    chunks = split_text(PAGE)
    for c in chunks:
        assert len(c) <= 500


def test_overlap_keeps_context():
    txt = ("- Пункт перечисления номер %d с подробным описанием содержания.\n" * 60
           % tuple(range(60)))
    chunks = split_text(txt)
    assert len(chunks) > 2
    # соседние окна перекрываются: хвост предыдущего встречается в следующем
    for a, b in itertools.pairwise(chunks):
        tail = a[-80:]
        assert tail[:40].strip() and (tail.strip() in b or b[:120].strip() in a + b)


def test_hard_cut_without_separators():
    # один длинный токен без границ — детерминированный жёсткий рез;
    # окна идут монотонно с overlap-перекрытием, дыр в покрытии нет
    txt = "х" * 1300
    chunks = split_text(txt, chunk_size=500, overlap=0.15)
    assert 1 < len(chunks) <= 4
    assert all(set(c) == {"х"} for c in chunks)
    covered = sum(map(len, chunks)) - 75 * (len(chunks) - 1)
    assert covered >= len(txt)


def test_empty_and_none_fail_open_unchanged():
    assert split_text("") == [""]
    assert split_text(None) == [""]


def test_spans_match_split_text_slicing():
    """split_text_spans даёт ту же нарезку, что split_text (1:1 индексы)."""
    spans = split_text_spans(PAGE)
    texts = [PAGE[s:e].strip() for s, e in spans]
    assert texts == split_text(PAGE)


def test_merge_all_chunks_single_headers_no_glue():
    """Слияние ВСЕХ окон страницы: заголовки секций по одному, швов нет."""
    import re

    spans = split_text_spans(PAGE)
    merged = merge_span_groups(PAGE, spans, range(len(spans)))
    assert not re.search(r"\S##", merged)
    for header in ("## Темы", "## Решения", "## Факты", "## Сущности"):
        assert merged.count(header) == 1


def test_sections_stay_whole_in_one_chunk():
    """Заголовок секции не отрывается от содержимого и не дублируется."""
    import re

    chunks = split_text(PAGE)
    joined = "\n".join(chunks)
    for h in ("## Темы", "## Решения", "## Факты", "## Сущности", "## Источники"):
        assert joined.count(h) == 1, f"{h} дублируется или потерян"
    for c in chunks:
        assert not re.search(r"## [^\n]+\Z", c), f"голый заголовок в хвосте: {c[-40:]!r}"


def test_page_intro_is_top_level_concept():
    """page_intro = H1 + резюме (без YAML и секций) — шапка инжекта по АР-6."""
    from wiki_v2.chunker import page_intro

    intro = page_intro(PAGE)
    assert intro.startswith("# Тестовая страница")
    assert "Разговор о создании ролика" in intro
    assert "title:" not in intro          # YAML отрезан
    assert "## Темы" not in intro         # до первой секции
