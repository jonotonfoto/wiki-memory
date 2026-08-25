"""Wiki chunker: разбиение текста на смысловые чанки (S2.5.8)."""
import re


_META_HEADER_RE = re.compile(
    r"^#{1,6}\s*(?:темы|сущности|концепции|теги|tags|keywords)\s*:?\s*$", re.I
)


def is_meta_block(block):
    """Мета-блок (облако тегов): заголовок «Темы/Сущности/Концепции/Теги» +
    только буллеты без прозы. Нужен ИНДЕКСАЦИИ (расширение поиска через темы),
    но не инжекту в контекст модели."""
    lines = [l for l in block.splitlines() if l.strip()]
    if not lines:
        return True
    if not _META_HEADER_RE.match(lines[0].strip()):
        return False
    return all(l.lstrip().startswith(("-", "*", "•")) for l in lines[1:] if l.strip())


def trim_meta_blocks(text):
    """Вырезать мета-блоки из текста чанка, сохранив порядок остальных.

    Пустая строка = чанк состоял только из облаков тегов (для инжекта бесполезен).
    """
    kept = [b.strip() for b in re.split(r"\n\n+", text.strip())
            if b.strip() and not is_meta_block(b)]
    return "\n\n".join(kept)


def page_intro(text):
    """ВЕРХНЕУРОВНЕВЫЙ КОНЦЕПТ страницы: всё до первой секции «## »
    (обычно H1-заголовок + резюме разговора). YAML-фронтматтер отбрасывается.
    Это обязательная шапка инжекта: модель должна понимать, ЧТО за страница,
    даже если релевантный чанк — фрагмент из середины."""
    t = text.strip()
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end != -1:
            t = t[end + 4:].lstrip("\n")
    out = []
    for line in t.splitlines(keepends=True):
        if line.lstrip().startswith("##"):
            break
        out.append(line)
    return "".join(out).strip()


def _split_block_inner(body, bs, be, chunk_size, ov):
    """Нарезка одного ОГРОМНОГО блока (> chunk_size) по "\\n"/". " с overlap."""
    seg = body[bs:be]
    m = len(seg)
    out = []
    start = 0
    while start < m:
        end = min(start + chunk_size, m)
        if end < m:
            cut = -1
            for sep in ("\n", ". "):
                cut = max(cut, seg.rfind(sep, start + 1, end))
            if cut <= start:
                cut = end
            end = cut
        out.append((bs + start, bs + end))
        if end >= m:
            break
        nxt = end - ov
        if nxt <= start:
            nxt = start + 1
        start = nxt
    return out


def _split_text_spans_core(body, chunk_size, overlap):
    """Интервалы [start, end) по ОЧИЩЕННОМУ телу (без ведущих/хвостовых пробелов).

    Фикс 2026-08-25 (финал): атомарная единица — БЛОК до "\\n\\n" (заголовок секции
    со своими пунктами никогда не разрывается и не отрывается от содержимого).
    Блоки пакуются в окна ≤ chunk_size; блок-гигант режется внутри по "\\n"/". "
    с overlap. Межблочного overlap нет — каждый чанк состоит из целых секций.
    """
    n = len(body)
    if n <= chunk_size:
        return [(0, n)]
    blocks = []
    i = 0
    while i < n:
        j = body.find("\n\n", i)
        e = n if j == -1 else j
        if body[i:e].strip():
            blocks.append((i, e))
        if j == -1:
            break
        i = j + 2
    if not blocks:
        return [(0, n)]
    ov = max(1, int(chunk_size * overlap))
    spans = []
    cs = ce = None

    def flush():
        nonlocal cs, ce
        if cs is not None:
            spans.append((cs, ce))
        cs = ce = None

    for bs, be in blocks:
        if be - bs > chunk_size:
            flush()
            spans.extend(_split_block_inner(body, bs, be, chunk_size, ov))
            continue
        if cs is None:
            cs, ce = bs, be
        elif be - cs <= chunk_size:
            ce = be
        else:
            flush()
            cs, ce = bs, be
    flush()
    return spans


def _split_text_core(text, chunk_size, overlap):
    """Тексты чанков одного прохода разбивки (делегат к спанам)."""
    if not text or not text.strip():
        return [text or ""]
    body = text.strip()
    return [body[s:e].strip() for s, e in _split_text_spans_core(body, chunk_size, overlap)]


def split_text(text, chunk_size=500, overlap=0.15, max_chunks: int = 200):
    """Разбить текст на смысловые чанки.

    text: исходный текст.
    chunk_size: целевой размер чанка (~500 символов/токенов).
    overlap: доля перекрытия между чанками (10-20%).
    max_chunks (Этап 4е): предел числа чанков. Хвост текста НЕ теряется — при
        превышении лимита chunk_size удваивается, пока чанков <= max_chunks
        (граница len(text): при chunk_size >= len(text) остаётся один чанк —
        защита от «взрыва» чанков на гигантском тексте).

    Разделители приоритета: "\\n\\n" → "\\n" → ". " (границы абзацев/предложений).

    Консистентность индексов (Этап 4е): для НОРМАЛЬНЫХ входов (< max_chunks)
    результат байт-в-байт совпадает с прежним — индексы page_chunk:N, что
    нарезают indexer/плагин/backfill, остаются 1:1 (все зовут тот же split_text).

    fail-open: на пустом/битом тексте возвращает [исходный_текст] (один чанк),
    никогда не бросает.
    """
    chunks = _split_text_core(text, chunk_size, overlap)
    # Пока число чанков больше лимита — удваиваем размер (без жёсткого предела
    # итераций, но граница len(text): при chunk_size >= len(text) чанк будет один,
    # т.е. гарантированно <= max_chunks; страховка 64 от любого патологического случая).
    it = 0
    while len(chunks) > max_chunks and chunk_size < len(text) and it < 64:
        chunk_size *= 2
        chunks = _split_text_core(text, chunk_size, overlap)
        it += 1
    return chunks


def split_text_spans(text, chunk_size=500, overlap=0.15, max_chunks: int = 200):
    """Интервалы [start, end) чанков В ИСХОДНОМ тексте (с учётом ведущих пробелов).

    Нарезка идентична split_text: len(spans) == len(split_text(text)) и
    text[s:e].strip() == split_text(text)[i] для каждого i.
    """
    if not isinstance(text, str) or not text.strip():
        return []
    lead = len(text) - len(text.lstrip())
    body = text.strip()
    spans = _split_text_spans_core(body, chunk_size, overlap)
    it = 0
    while len(spans) > max_chunks and chunk_size < len(body) and it < 64:
        chunk_size *= 2
        spans = _split_text_spans_core(body, chunk_size, overlap)
        it += 1
    return [(s + lead, e + lead) for s, e in spans]


def merge_span_groups(text, spans, picked_idxs):
    """Собрать выбранные чанк-интервалы в НЕПРЕРЫВНЫЕ куски исходного текста.

    Перекрывающиеся/смежные интервалы сливаются (исчезают overlap-дубли и швы
    «встык» вида «Керкоров## Темы», «## Факты## Факты»); разрозненные группы
    соединяются через "\\n\\n". Пустые куски отбрасываются.
    """
    ivs = sorted(spans[i] for i in picked_idxs if 0 <= i < len(spans))
    parts = []
    cur = None
    for s, e in ivs:
        if cur and s <= cur[1]:
            cur = (cur[0], max(cur[1], e))
        else:
            if cur:
                parts.append(cur)
            cur = (s, e)
    if cur:
        parts.append(cur)
    out = []
    for s, e in parts:
        piece = text[s:e].strip()
        if piece:
            out.append(piece)
    return "\n\n".join(out)


def chunk_contextual(text, title, tags=None, chunk_size=500, overlap=0.15):
    """Разбить текст на чанки и дать каждому контекст (Contextual Retrieval).

    text: текст страницы.
    title: название страницы.
    tags: теги страницы (list) — для контекста.
    chunk_size, overlap: параметры split_text.

    Возвращает list[str], где каждый чанк обёрнут:
    "Это часть страницы \"{title}\", которая про {теги}. Чанк: {текст_чанка}"

    fail-open: на пустом text/title возвращает [text] (один чанк без обёртки),
    никогда не бросает.
    """
    chunks = split_text(text, chunk_size, overlap)
    if not title:
        return chunks
    tags_str = ", ".join(tags) if tags else "разные темы"
    out = []
    for c in chunks:
        if not c:
            continue
        out.append(f'Это часть страницы "{title}", которая про {tags_str}. Чанк: {c}')
    return out
