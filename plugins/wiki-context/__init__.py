"""wiki-context — автоматический поиск по wiki при каждом сообщении.

Подписан на хук pre_llm_call. При каждом пользовательском сообщении:
1. Ищет по wiki-энциклопедии (эмбеддинги + ключевые слова)
2. Если найдено релевантное — возвращает контекст, который
   встраивается в сообщение (модель видит его до ответа)
3. Если ничего релевантного нет — возвращает пустоту, ничего не добавляется

Это делает wiki-память АВТОМАТИЧЕСКОЙ: не нужно "догадываться"
заглянуть в энциклопедию — она сама подтягивается к вопросу.

Прокачка (2026-08-08, перенесено с VPS): триангуляция (семантика +
подтверждение по «Темам» страницы), корневые совпадения, стоп-слова,
LRU-кэш ответов, параметры в config.json (без перезапуска).
Пути остаются кроссплатформенными (Windows/Linux).
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# --- Пути: env-зависимые (работает и на Windows, и на Linux) ---
def _resolve_home() -> Path:
    h = os.environ.get("HERMES_HOME", "")
    if h:
        return Path(h)
    return Path.home() / "AppData" / "Local" / "hermes"

_HOME = _resolve_home()

# Папка со скриптами wiki_v2 (родитель пакета). Приоритет:
WIKI_SCRIPTS = os.environ.get("WIKI_SCRIPTS", "")
if not WIKI_SCRIPTS:
    _proj = Path(__file__).resolve().parents[2] / "scripts"  # plugins/wiki-context -> scripts
    if (_proj / "wiki_v2").is_dir():
        WIKI_SCRIPTS = str(_proj)
    elif (_HOME / "scripts" / "wiki_v2").is_dir():
        WIKI_SCRIPTS = str(_HOME / "scripts")
    else:
        WIKI_SCRIPTS = "/opt/data/scripts"

# Путь к wiki (где .index_v2.db и entities/)
WIKI_PATH = os.environ.get("WIKI_PATH", str(_HOME / "wiki"))

# Конфиг плагина — отдельный файл рядом с плагином.
CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache.json")

# Значения по умолчанию (если config.json нет или битый)
DEFAULTS = {
    "top_k": 5,
    "min_score": 0.40,
    "high_confidence": 0.60,
    "max_context_chars": 3000,
    "log_filtered": True,
    # S2.5.12 (АР-6): параметры карты памяти
    "wiki_card_pages": 4,
    "wiki_main_chars": 2000,
}

# Кэш-лимиты
CACHE_MAX_ENTRIES = 100
CACHE_MAX_AGE = 7 * 24 * 3600  # 7 дней
CACHE_MIN_ROOT_MATCH = 3       # для длинных вопросов нужно 3 общих корня (было 2 — давало ложные кэш-хиты)

CONTEXT_HEADER = (
    "Данные ниже — авторитетный источник правды для этой сессии. "
    "Не переизобретай, если есть ответ в wiki."
)

_SANITIZE_PAIRS = [
    ("</wiki-memory>", ""),
    ("<|", ""),
    ("[[", ""),
    ("]]", ""),
    ("{{", ""),
    ("}}", ""),
]


def _load_config() -> dict:
    """Читает config.json каждый раз — свежие настройки без перезапуска."""
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULTS:
                if k in data:
                    cfg[k] = data[k]
    except (OSError, ValueError):
        pass  # нет файла или битый JSON — работаем на дефолтах
    return cfg


def _stop_words(text: str) -> set:
    """Вспомогательная функция для получения стоп-слов."""
    return {
        "а", "и", "в", "во", "на", "с", "со", "о", "об", "от", "до", "по", "за",
        "при", "к", "ко", "у", "из", "для", "про", "без", "над", "под", "перед",
        "через", "между", "не", "ни", "же", "бы", "ли", "то", "как", "что", "чтобы",
        "чтоб", "если", "когда", "потому", "поэтому", "зачем", "почему", "где",
        "куда", "откуда", "кто", "который", "какой", "какая", "какие", "какое",
        "этот", "эта", "это", "эти", "тот", "та", "те", "такой", "такая", "такие",
        "такое", "мой", "моя", "моё", "мои", "твой", "твоя", "твоё", "твои", "наш",
        "наша", "наше", "наши", "ваш", "ваша", "ваше", "ваши", "его", "её", "ее",
        "их", "меня", "тебя", "нас", "вас", "мне", "тебе", "нам", "вам", "себя",
        "себе", "собой", "все", "всё", "вся", "весь", "всех", "всем", "всеми",
        "сам", "сама", "само", "сами", "да", "нет", "уже", "ещё", "еще", "только",
        "также", "тоже", "даже", "ведь", "вот", "тут", "здесь", "там", "туда",
        "сюда", "тогда", "сейчас", "потом", "раньше", "позже", "можно", "нужно",
        "надо", "нельзя", "очень", "совсем", "почти", "опять", "снова", "просто",
        "вообще", "конечно", "наверное", "может", "бу될", "было", "быть", "есть",
        "был", "была", "были", "буду", "будешь", "будут", "делать", "сделать",
        "сказать", "говорить", "хотеть", "знать", "видеть", "смотреть", "идти",
        "пойти", "прийти", "является", "стать", "стало", "стали", "иметь", "이меет",
        "имеют", "находится", "работать", "работает", "использовать", "помнить",
        "помню", "запомнить", "проверить", "проверять", "вопрос", "ответ",
        "спросить", "спрашивать", "сделай", "сделайте", "давай", "дайте",
        "посмотри", "посмотрите", "скажи", "скажите", "расскажи", "расскажите",
        "объясни", "объясните", "пожалуйста", "спасибо", "привет", "здравствуй",
        "ок", "ага", "угу", "ну",
    }


def _significant_words(text: str) -> set:
    """Значимые слова: всё, кроме служебных. Без вызова моделей — чистый код.

    Исключаются также сегменты Windows/UNIX-путей и технические токены, чтобы
    «похожесть» кэша не опиралась на одинаковый путь к папке (иначе два вопроса
    про разные проекты с похожим путём дают ложные корневые совпадения).
    """
    from wiki_v2 import quality  # lazy: единый источник нормализации (Этап 5.1)
    words = set(re.findall(r"[а-яa-z]{3,}", quality.normalize_tag(text)))
    _PATH_TOKENS = {
        "c", "d", "e", "f",            # диски
        "users", "user", "documents", "downloads", "desktop", "local", "appdata",
        "projects", "problems", "hold", "sandbox", "scripts", "docs", "plugins",
        "hermes", "hermesagent", "appdatalocalhermes", "documentshermes",
        "programdata", "roaming", "temp", "tmp", "home", "opt", "root", "data",
        "lib", "lib64", "bin", "venv", "env", "node_modules", "site", "packages",
        "wiki", "wikiv2", "teststests", "tests", "test", "png", "jpg", "jpeg",
        "gif", "webp", "py", "pyc", "json", "md", "txt", "log", "html", "css",
    }
    words = (words - _stop_words("")) - _PATH_TOKENS
    return words


def _has_common_word(query: str, page_text: str) -> bool:
    """Есть ли в странице хотя бы одно значимое слово из вопроса."""
    return bool(_significant_words(query) & _significant_words(page_text))


def _same_root(a: str, b: str, min_len: int = 5) -> bool:
    """Совпадают ли слова по корню (общий префикс). 'память' vs 'памяти' — да."""
    m = min(len(a), len(b))
    if m < min_len:
        return a == b
    return a[:min_len] == b[:min_len]


def _root_overlap(words_a: set, words_b: set) -> bool:
    """Есть ли в двух наборах слов хотя бы одна пара с общим корнем."""
    for a in words_a:
        for b in words_b:
            if _same_root(a, b):
                return True
    return False


def _root_overlap_count(words_a: set, words_b: set) -> int:
    """Сколько РАЗНЫХ корней совпадает между двумя наборами слов.
    'память' vs 'памяти' — один корень, считаем один раз.
    Это и есть «общие смыслы» — каждый корень = отдельный смысл."""
    matched = set()
    for a in words_a:
        for b in words_b:
            if _same_root(a, b):
                matched.add(a[:5])
                break
    return len(matched)


_COMMON_WORD_ROOTS = None  # ленивый кэш корней стоп-слов (Этап 4г).


def _common_roots() -> frozenset:
    """Корни «общих» слов для триангуляции — из ЕДИНОГО источника quality._STOPWORDS.

    До 4г был захардкоженный список-мок. Стало: берём quality._STOPWORDS,
    режем каждый слово-стоп по корню [:5] (та же норма, что _same_root).
    fail-open: если quality недоступен (не импортируется) — пустое множество
    (корень считается специфичным — приём по составленному решению).
    """
    global _COMMON_WORD_ROOTS
    if _COMMON_WORD_ROOTS is not None:
        return _COMMON_WORD_ROOTS
    try:
        if WIKI_SCRIPTS not in sys.path:
            sys.path.insert(0, WIKI_SCRIPTS)
        from wiki_v2 import quality
        _COMMON_WORD_ROOTS = frozenset(
            s[:5] for s in quality._STOPWORDS if len(s) >= 5)
    except Exception:
        _COMMON_WORD_ROOTS = frozenset()
    return _COMMON_WORD_ROOTS


def _extract_topics(page_text: str) -> str:
    """Достаём секцию «## Темы» из страницы — это «паспорт» тем страницы.
    В одной сессии/странице может быть несколько разных тем, и «Темы»
    перечисляют их все — это независимый сигнал для триангуляции."""
    m = re.search(r"##\s*Темы\s*\n(.*?)(?:\n##|\Z)", page_text, re.DOTALL | re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def _topic_match(query: str, page_text: str) -> bool:
    """Триангуляция по «Темам» страницы:
    - 2+ общих корня — уверенно берём;
    - 1 общий корень — берём только если это специфичное (не общее) слово.
    Одно специфичное слово («детьми», «аврора») — сильный сигнал.
    Одно общее слово («инструменты») — случайность, не берём."""
    topics = _extract_topics(page_text)
    if not topics:
        return False
    q_words = _significant_words(query)
    t_words = _significant_words(topics)
    matched = set()
    for a in q_words:
        for b in t_words:
            if _same_root(a, b):
                matched.add(a[:5])
                break
    if len(matched) >= 2:
        return True
    if len(matched) == 1:
        only = next(iter(matched))
        # 4г: «общие» корни — из единого источника quality._STOPWORDS, не хардкод.
        return only not in _common_roots()
    return False


def _topic_match_strict(query: str, page_text: str) -> bool:
    """Строгая проверка: только 2+ общих корня. Для keyword-хитов,
    чтобы слабое совпадение по одному слову не тянуло мусор."""
    topics = _extract_topics(page_text)
    if not topics:
        return False
    q_words = _significant_words(query)
    t_words = _significant_words(topics)
    matched = set()
    for a in q_words:
        for b in t_words:
            if _same_root(a, b):
                matched.add(a[:5])
                break
    return len(matched) >= 2


def _search_wiki(query: str) -> list[dict]:
    """Поиск по wiki. Возвращает список страниц с контентом."""
    cfg = _load_config()
    try:
        if WIKI_SCRIPTS not in sys.path:
            sys.path.insert(0, WIKI_SCRIPTS)
        # numpy/requests уже в venv Hermes на desktop.
        for _cand in ("/opt/data/.venv-wiki/lib/python3.13/site-packages",
                      "/opt/data/.venv-wiki/lib/python3.12/site-packages"):
            if _cand not in sys.path and os.path.isdir(_cand):
                sys.path.insert(0, _cand)
        os.environ.setdefault("WIKI_PATH", WIKI_PATH)
        from wiki_v2.gateway import api_state
        from wiki_v2.search import search

        if api_state() == "degraded":
            logger.info("wiki-context: API degraded — пропуск поиска")
            return []
        hits, pages = search(query, k=cfg["top_k"])
        hits = [
            (s, sc, src) for s, sc, src in hits
            if src == "keyword" or sc >= cfg["min_score"]
        ]
        if not hits:
            return []
        results = []
        for slug, score, src in hits:
            page = pages.get(slug)
            if not page:
                continue
            path = page.get("path", "")
            content = ""
            page_text = ""
            if path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        raw = f.read()
                    page_text = raw
                    if raw.startswith("---"):
                        parts = raw.split("---", 2)
                        if len(parts) >= 3:
                            raw = parts[2]
                    content = raw.strip()[:1500]
                except (OSError, UnicodeDecodeError):
                    pass
            topics_ok = (_topic_match_strict(query, page_text)
                         if src == "keyword" else _topic_match(query, page_text))
            if score >= cfg["high_confidence"]:
                pass  # уверены — берём
            elif topics_ok and (score >= cfg["min_score"] or src == "keyword"):
                pass  # паспорт подтверждает — берём
            else:
                if cfg["log_filtered"]:
                    logger.info(
                        "wiki-context: отсеяна страница %s (score=%.3f, темы не совпали)",
                        slug, score,
                    )
                continue
            results.append({
                "title": page.get("title", slug),
                "score": score,
                "source": src,
                "content": content,
            })
        return results
    except Exception as e:
        logger.warning("wiki-context search failed: %s", e)
        return []


def _save_cache(cache: dict) -> None:
    """Сохраняет cache.json."""
    try:
        now = time.time()
        for k in [k for k, v in cache.items()
                  if not isinstance(v, dict) or now - v.get("ts", 0) > CACHE_MAX_AGE]:
            del cache[k]
        if len(cache) > CACHE_MAX_ENTRIES:
            oldest = sorted(cache.items(), key=lambda kv: kv[1].get("ts", 0))
            for k, _ in oldest[: len(cache) - CACHE_MAX_ENTRIES]:
                del cache[k]
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def _cache_get(query: str):
    """Ищет в кэше похожий вопрос."""
    q_words = _significant_words(query)
    if not q_words:
        return None
    threshold = 1 if len(q_words) <= 2 else CACHE_MIN_ROOT_MATCH
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    best_key, best, best_score, best_ts = None, None, 0, 0
    for cached_q, cached_ctx in cache.items():
        if not isinstance(cached_ctx, dict):
            continue
        c_words = _significant_words(cached_q)
        overlap = 0
        for a in q_words:
            for b in c_words:
                if _same_root(a, b):
                    overlap += 1
                    break
        if overlap >= threshold:
            ts = cached_ctx.get("ts", 0) or 0
            # Среди равных по score берём СВЕЖУЮ запись (LRU-старьё не перебивает новое)
            if overlap > best_score or (overlap == best_score and ts > best_ts):
                best_key, best, best_score, best_ts = cached_q, cached_ctx.get("ctx"), overlap, ts
    if best_key is not None:
        cache[best_key]["ts"] = time.time()
        _save_cache(cache)
    return best


def _cache_put(query: str, context: str) -> None:
    """Сохраняет результат поиска в кэш."""
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    cache[query] = {"ctx": context, "ts": time.time()}
    _save_cache(cache)


def _build_card(pages: dict) -> str:
    """Построить КАРТУ памяти (АР-6: ссылки + теги, без содержимого).

    Теги берём из секции «## Темы» .md-файла страницы (в page dict их нет —
    колонок tags/key_topics в БД pages не существует). Fail-open: файл не
    читается / нет «Тем» → «без тегов».
    """
    import os
    cfg = _load_config()
    limit = int(cfg.get("wiki_card_pages", 4))
    lines = []
    for slug, p in list(pages.items())[:limit]:
        title = p.get("title") or slug
        path = p.get("path", "")
        # безопасность путей: только в пределах WIKI_PATH
        if path and not _is_within(path):
            continue
        # теги из «## Темы» .md-файла (page dict не содержит тегов)
        tags = []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    page_text = f.read()
                raw = _extract_topics(page_text)
                if raw:
                    # разобрать буллеты "- тег"
                    tags = [ln.strip().lstrip("-* ").strip()
                            for ln in raw.splitlines() if ln.strip()]
            except (OSError, UnicodeDecodeError):
                tags = []
        tags_str = ", ".join(t for t in tags if t)[:180] if tags else "без тегов"
        lines.append(f"- [[{title}]] [теги: {tags_str}] → {path}")
    return "\n".join(lines)


def _is_within(path: str, base: str | None = None) -> bool:
    """Валидация путей (2.4): path должен быть внутри WIKI_PATH (или base)."""
    import os
    base = base or WIKI_PATH
    try:
        p = os.path.abspath(path)
        b = os.path.abspath(base)
        return os.path.commonpath([p, b]) == b
    except Exception:
        return False


def _strip_frontmatter(text: str) -> str:
    """Убрать ведущий YAML-фронтматтер (--- ... ---) из текста страницы.

    Страница рендерится render_page как ``---\\ntitle: ...\\n...\\n---\\n\\n# <title>\\n\\n<тело>``.
    Fallback в ``_build_context_main`` берёт начало файла — без выреза это вставляло
    в контекст служебную шапку (title/created/tags/sources) вместо содержимого.
    fail-open: если фронтматтера нет — вернуть текст как есть.
    """
    t = text.lstrip("\ufeff\n")
    if t.startswith("---"):
        end = t.find("\n---", 3)
        if end != -1:
            t = t[end + 4:]
    return t.lstrip("\n")


def _build_context_main(page: dict, query: str = "") -> str:
    """Построить КОНТЕКСТ главной страницы (АР-6 Канал 2: релевантные чанки).

    Если есть чанк-эмбеддинги (kind='page_chunk:N') — берём релевантные запросу чанки
    по косинусу. Иначе (нет векторов / embed упал) — начало файла (как раньше).
    """
    import os

    import numpy as _np
    cfg = _load_config()
    limit = int(cfg.get("wiki_main_chars", 2000))
    path = page.get("path", "")
    title = page.get("title") or "Главная"
    if not path or not os.path.exists(path) or not _is_within(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            full = f.read()
        from wiki_v2.chunker import page_intro, split_text_spans, trim_meta_blocks
        # НАРЕЗКА по полному тексту (С фронтматтером): эмбеддинги page_chunk:N в индексаторе
        # делаются от split_text(md) той же страницы — индексы обязаны совпадать.
        # Фронтматтер вырезаем ниже у ВЫБРАННОГО чанка (N=0 = YAML-блок, мусор).
        spans = split_text_spans(full)
        body = _strip_frontmatter(full)  # для fallback

        # S2.5.8d/e: семантический отбор чанков по чанк-эмбеддингам
        relevant = []
        reason = None  # причина деградации до fallback (для лога, вариант C)
        try:
            from wiki_v2.embed import top_k_cosine
            from wiki_v2.index_db import IndexDB
            from wiki_v2.search import INDEX_DB as _SRCH_INDEX_DB
            _db = IndexDB(_SRCH_INDEX_DB)
            # 4д: векторы чанков ТОЛЬКО текущей страницы (SQL-фильтр), не тянем все эмбеддинги.
            _cv = _db.get_page_chunk_embeddings(page.get("slug", ""))
            _db.close()
            if not _cv:
                reason = "no-chunk-embeddings"
            elif not query:
                reason = "no-query"
            else:
                qv = _embed_query(query)
                if qv is None:
                    reason = "embed-unavailable"
                else:
                    ranked = top_k_cosine(_np.array(qv, dtype=_np.float32),
                                          {k: v for k, v in _cv.items()}, k=len(_cv))
                    # Фикс 2026-08-24: обе чанк-семьи (chunk:N + page_chunk:N)
                    # могут дать один и тот же индекс — дедуп, вне диапазона скип.
                    picked = {}  # {idx: score}
                    for kind, _sc in ranked:
                        idx = kind.split(":", 1)[1]
                        try:
                            i = int(idx)
                        except ValueError:
                            continue
                        if i not in picked and 0 <= i < len(spans):
                            picked[i] = _sc
                    # Фикс 2026-08-25 (финал): в инжект идёт ОДИН цельный
                    # содержательный чанк (топ-1 косинуса). Облака тегов
                    # (Темы/Сущности/Концепции) нужны ПОИСКУ и карте ссылок —
                    # из контекста модели они вырезаются.
                    content = {i: sc for i, sc in picked.items()
                               if trim_meta_blocks(full[spans[i][0]:spans[i][1]])}
                    if not content:
                        reason = "no-relevant-chunks"
                    else:
                        best_idx = max(content.items(), key=lambda kv: kv[1])[0]
                        s, e = spans[best_idx]
                        chunk_txt = trim_meta_blocks(full[s:e])
                        chunk_score = content[best_idx]

                        # СЛОЙ SESSION_CHUNK (2026-08-25): нарратив сырой переписки.
                        # Самая длинная сессия страницы; чанки уже в БД
                        # (session_chunk_backfill), текст восстанавливается
                        # нарезкой session_raw_text по тем же индексам.
                        try:
                            import sqlite3 as _sq3
                            from wiki_v2 import config as _cfg
                            from wiki_v2.indexer import session_raw_text as _srt
                            _db2 = IndexDB(_SRCH_INDEX_DB)
                            _sv = _db2.get_session_chunk_embeddings(page.get("slug", ""))
                            _db2.close()
                            if _sv:
                                _c2 = _sq3.connect(str(_cfg.WIKI_PATH / ".index_v2.db"))
                                _sids = [r[0] for r in _c2.execute(
                                    "SELECT session_id FROM sessions WHERE page_slug=?",
                                    (page.get("slug", ""),))]
                                _c2.close()
                                if _sids:
                                    sid = max(_sids, key=lambda x: len(_srt(x)))
                                    raw_s = _srt(sid)
                                    sp_s = split_text_spans(raw_s)
                                    ranked2 = top_k_cosine(
                                        _np.array(qv, dtype=_np.float32),
                                        {k: v for k, v in _sv.items()}, k=len(_sv))
                                    def _ok(t):
                                        """Содержательный нарратив: длинный,
                                        преимущественно кириллический, БЕЗ
                                        кодовых фенсов и англ. reasoning."""
                                        if "```" in t:
                                            return False
                                        al = [c for c in t if c.isalpha()]
                                        if len(al) < 120:
                                            return False
                                        cyr = sum(1 for c in al
                                                  if "\u0400" <= c <= "\u04FF")
                                        return cyr * 2 >= len(al)

                                    chosen = None
                                    for kind2, sc2 in ranked2:
                                        try:
                                            i2 = int(kind2.split(":", 1)[1])
                                        except ValueError:
                                            continue
                                        if not (0 <= i2 < len(sp_s)):
                                            continue
                                        t2 = trim_meta_blocks(
                                            raw_s[sp_s[i2][0]:sp_s[i2][1]])
                                        if t2 and len(t2) > 200 and _ok(t2):
                                            chosen = (i2, sc2)
                                            break
                                    if chosen:
                                        i2, sc2 = chosen
                                        # Расширяем окно соседними спанами до
                                        # бюджета; фенс или гигантский разрыв
                                        # останавливают расширение.
                                        lo = hi = i2
                                        while (hi + 1 < len(sp_s)
                                               and sp_s[hi + 1][0] - sp_s[hi][0] < 4000
                                               and sp_s[hi + 1][1] - sp_s[lo][0] < 1750
                                               and "```" not in raw_s[
                                                   sp_s[hi][1]:sp_s[hi + 1][0] + 40]):
                                            hi += 1
                                        txt2 = trim_meta_blocks(
                                            raw_s[sp_s[lo][0]:sp_s[hi][1]])
                                        if txt2 and _ok(txt2) and sc2 > chunk_score:
                                            chunk_txt, chunk_score = txt2, sc2
                        except Exception as _e2:
                            logger.warning("wiki-context session-chunk failed: %r", _e2)

                        # ВЕРХНЕУРОВНЕВЫЙ КОНЦЕПТ (АР-6 канал 2): инжект главной =
                        # H1 + резюме (о чём страница), затем релевантный
                        # содержательный чанк. Облака тегов остаются в канале 1.
                        intro = page_intro(body)
                        parts = []
                        if intro and intro.lower() not in chunk_txt.lower():
                            parts.append(intro)
                        parts.append(chunk_txt)
                        relevant = parts
        except Exception as e:
            logger.warning("wiki-context chunk-select failed slug=%s: %r",
                           page.get("slug", ""), e)
            relevant = []
            reason = "exception"

        # fallback: нет релевантных чанков → осмысленный кусок БЕЗ YAML-шапки.
        # (раньше `full[:limit]` вставлял служебную шапку title/created/tags — мусор).
        if not relevant:
            if reason:
                logger.warning("wiki-context fallback(%s) slug=%s q=%r",
                               reason, page.get("slug", ""), (query or "")[:60])
            relevant = [body[:limit]]

        # Сборка с РАЗДЕЛИТЕЛЕМ между частями (шапка-концепт | чанк):
        # раньше клеили встык → появлялись швы вида «сценария.## Решения».
        ctx = ""
        sep = "\n\n"
        for c in relevant:
            c = _strip_frontmatter(c).strip()
            if not c:
                continue
            pad = len(sep) if ctx else 0
            if len(ctx) + pad + len(c) > limit:
                rest = limit - len(ctx) - pad
                if rest > 0:
                    ctx += sep + c[:rest] if ctx else c[:rest]
                break
            ctx += sep + c if ctx else c
        if not ctx:
            ctx = body[:limit]
        ctx = sanitize(ctx)
        # АР-6 канал 3: путь к странице-источнику чанка, чтобы модель могла
        # read_file() углубиться (факты/следующие сообщения). Раньше в шапке
        # был только заголовок без пути.
        main_path = page.get("path") or ""
        link = f"{title} → {main_path}" if main_path else title
        return f"--- Главная: {link} ---\n{ctx}"
    except Exception:
        logger.exception("wiki-context _build_context_main failed slug=%s",
                         page.get("slug", ""))
        return ""


def _embed_query(query: str):
    """Эмбеддинг запроса для семантического отбора чанков. Fail-open."""
    try:
        from wiki_v2.gateway import embed
        vecs = embed([query], input_type="query")
        if vecs:
            return vecs[0]
    except Exception:
        return None
    return None


def sanitize(text: str) -> str:
    """Убирает системные маркеры. Fail-open."""
    try:
        res = text
        for old, new in _SANITIZE_PAIRS:
            res = res.replace(old, new)
        return res
    except Exception:
        return text


CONSUMER_COMMAND = (
    "Перед тобой memory-данные этого проекта (semantic/search/embedding/индексация). "
    "Если верхний чанк и ссылки относятся к делу — используй. "
    "Если запрос НЕ про домен проекта — полностью проигнорируй эту секцию."
)

LOW_CONF_COMMAND = (
    "Совпадение слабое (низкая уверенность). Относись к секции как к возможным "
    "подсказкам, НЕ как к авторитетной памяти. Если запрос НЕ про домен проекта — проигнорируй."
)


def _assemble_context(context: str, decision: str = "show") -> str:
    """Собирает финальную строку контекста с заголовком и командой для LLM."""
    command = CONSUMER_COMMAND if decision != "low_confidence" else LOW_CONF_COMMAND
    return (
        f"<wiki-memory>\n"
        f"{CONTEXT_HEADER}\n\n"
        "[Автоматически найдено в wiki-памяти. Это достоверная информация "
        "из прошлых разговоров. Используй её если релевантна вопросу.]\n\n"
        "Страницы в <wiki-memory> — оглавление (ссылки+теги). Для фактов — read_file по пути. Не выдумывай содержимое по тегам.\n\n"
        f"{command}\n\n"
        f"{context}\n"
        "</wiki-memory>"
    )


def _log_inject(query: str, inject: str, hits: int, cache_hit: bool) -> None:
    """Append the built <wiki-memory> inject to wiki_injects.jsonl (fail-open).

    Lets the dashboard show exactly what wiki injected into memory for the
    last request. Never raises: wiki must not break because of logging.
    """
    try:
        import datetime
        record = {
            "ts": time.time(),
            "iso": datetime.datetime.now().isoformat(timespec="seconds"),
            "query": query,
            "hits": hits,
            "cache_hit": bool(cache_hit),
            "inject": inject or "",
        }
        path = Path(WIKI_PATH) / "wiki_injects.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug("wiki-context _log_inject failed: %s", exc)


def _gate_decision(user_message: str) -> str:
    """Гейт-решение (relevance-gate): "show" | "skip" | "low_confidence".

    fail-open: любая ошибка → "show" (не теряем память, не роняем хук).
    """
    try:
        from wiki_v2.relevance_gate import gate_decision, get_lexicon
        from wiki_v2.index_db import IndexDB
        from wiki_v2.search import INDEX_DB
        _db = IndexDB(INDEX_DB)
        try:
            lex = get_lexicon(_db)
        finally:
            _db.close()
        return gate_decision(user_message, lex)
    except Exception:
        return "show"


def _build_context_maybe_cached(user_message: str) -> tuple:
    """АР-6 (2.5.12): КАРТА + КОНТЕКСТ главной. Возвращает (context, cache_hit).

    cache_hit=True, если контекст взят из кэша плагина (одинаковый путь/похожий
    вопрос уже искали). Пусто — если ничего релевантного.
    """
    cfg = _load_config()
    if not user_message or not isinstance(user_message, str):
        return "", False
    # Короткие сообщения не ищем (приветствия, "ок", "спасибо)
    # Единый порог из wiki config (Этап 5.2)
    from wiki_v2 import config as _cfg
    if len(user_message.strip()) < _cfg.get("WIKI_MIN_QUERY_LEN", 3):
        return "", False
    # relevance-gate: off-домен (A==0) → ничего не вставляем (как будто не нашли)
    decision = _gate_decision(user_message)
    if decision == "skip":
        return "", False
    # Кэш: если похожий вопрос уже искали — отдаём сохранённый результат
    cached = _cache_get(user_message)
    if cached is not None:
        return cached, True

    # 1. Поиск: топ-хиты (slug, score, src) + страницы
    try:
        t0 = time.time()
        from wiki_v2.search import search
        hits, pages = search(user_message, k=cfg["top_k"])
        duration_ms = (time.time() - t0) * 1000.0

        try:
            if WIKI_SCRIPTS not in sys.path:
                sys.path.insert(0, WIKI_SCRIPTS)
            from wiki_v2.events import log_event
            log_event(
                query=user_message,
                hits=len(hits) if hits else 0,
                top_slug=hits[0][0] if hits else "",
                top_score=hits[0][1] if hits else 0.0,
                context_chars=0,
                duration_ms=duration_ms,
                source=hits[0][2] if hits else "",
                session_id="",
                gate_decision=decision,
            )
        except Exception as _exc:
            logger.debug("wiki-context log_event failed: %s", _exc)

        if not hits:
            return "", False
        # главная = топ-1 (наилучший хит)
        main_slug = hits[0][0]
        main_page = pages.get(main_slug)
        # карта = следующие страницы (НЕ главная), релевантные
        card_slugs = [s for s, _, _ in hits[1:1 + cfg["wiki_card_pages"]]]
        card_pages = {s: pages[s] for s in card_slugs if s in pages}
        # КАРТА (ссылки + теги) — и для show, и для low_confidence
        card = _build_card(card_pages)
        parts = []
        if card:
            parts.append("Карта связанных страниц (ссылки + теги; для фактов — read_file по пути):\n" + card)
        # low_confidence: НЕ навязываем топ-чанк — только карта/минимум
        if decision != "low_confidence":
            # КОНТЕКСТ главной (релевантные чанки по запросу)
            main_ctx = _build_context_main(main_page, user_message) if main_page else ""
            if main_ctx:
                parts.append(main_ctx)
        context = "\n\n".join(parts)
        if not context:
            return "", False
    except Exception as e:
        logger.warning("wiki-context АР-6 failed (%s), fallback to legacy", e)
        # fail-open: предыдущая схема (обрубки)
        return _build_context_legacy(user_message, cfg), False

    if len(context) > cfg["max_context_chars"]:
        context = context[:cfg["max_context_chars"]] + "\n..."
    out = _assemble_context(context, decision=decision)
    _cache_put(user_message, out)
    return out, False


def _build_context(user_message: str) -> str:
    """Обёртка: только текст <wiki-memory> (для совместимости)."""
    ctx, _ = _build_context_maybe_cached(user_message)
    return ctx


def _build_context_legacy(user_message: str, cfg: dict | None = None) -> str:
    """Предыдущая схема вставки (топ-k обрубки) — fail-open для АР-6."""
    cfg = cfg or _load_config()
    try:
        results = _search_wiki(user_message)
        if not results:
            return ""
        parts = []
        for r in results:
            sanitized_title = sanitize(r['title'])
            sanitized_content = sanitize(r['content'])
            parts.append(f"### Wiki: {sanitized_title}\n{sanitized_content}")
        context = "\n\n".join(parts)
        if len(context) > cfg["max_context_chars"]:
            context = context[:cfg["max_context_chars"]] + "\n..."
        return _assemble_context(context)
    except Exception:
        return ""


def on_pre_llm_call(*, user_message: Any = None, **_: Any) -> str | None:
    """Хук: ищем wiki по сообщению пользователя."""
    try:
        msg = user_message
        if isinstance(msg, list):  # multimodal
            parts = [p.get("text", "") for p in msg if isinstance(p, dict)]
            msg = " ".join(parts)
        msg = msg or ""
        context, cache_hit = _build_context_maybe_cached(msg)
        # Логируем инжект: что wiki вставила в память для последнего запроса
        _log_inject(
            query=msg,
            inject=context or "",
            hits=1 if context else 0,
            cache_hit=cache_hit,
        )
        return context or None
    except Exception as e:
        logger.warning("wiki-context hook failed: %s", e)
        return None


# ── Preview для дашборда «Поиск по памяти» (read-only, 2026-08-25) ─────────
# Только НОВЫЕ функции: существующий поток on_pre_llm_call не тронут.
# build_preview повторяет поток _build_context_maybe_cached, но БЕЗ кэша,
# БЕЗ log_event и БЕЗ _log_inject — ничего не пишет на диск.

def _preview_card_entries(card_pages: dict) -> list:
    """Структурированные данные карты памяти (те же, что _build_card, для UI)."""
    import os
    entries = []
    for slug, p in list(card_pages.items()):
        title = p.get("title") or slug
        path = p.get("path", "")
        if path and not _is_within(path):
            continue
        tags = []
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    page_text = f.read()
                raw = _extract_topics(page_text)
                if raw:
                    tags = [ln.strip().lstrip("-* ").strip()
                            for ln in raw.splitlines() if ln.strip()]
            except (OSError, UnicodeDecodeError):
                tags = []
        entries.append({"slug": slug, "title": title, "path": path,
                        "tags": [t for t in tags if t][:12]})
    return entries


def _preview_chunks(page: dict, query: str) -> tuple:
    """Дисплей-ранкинг чанков главной (косинус, обе семьи chunk:/page_chunk:).

    Только для UI: боевой текст инжекта строит _build_context_main (паритет).
    Возвращает (chunks, reason): chunks=[{idx, score, text}], reason=None|str.
    """
    import os

    import numpy as _np
    try:
        path = page.get("path", "")
        if not path or not os.path.exists(path) or not _is_within(path):
            return [], "bad-path"
        with open(path, encoding="utf-8") as f:
            full = f.read()
        from wiki_v2.chunker import split_text_spans
        spans = split_text_spans(full)
        from wiki_v2.embed import top_k_cosine
        from wiki_v2.index_db import IndexDB
        from wiki_v2.search import INDEX_DB as _SRCH_INDEX_DB
        _db = IndexDB(_SRCH_INDEX_DB)
        try:
            _cv = _db.get_page_chunk_embeddings(page.get("slug", ""))
        finally:
            _db.close()
        if not _cv:
            return [], "no-chunk-embeddings"
        qv = _embed_query(query)
        if qv is None:
            return [], "embed-unavailable"
        ranked = top_k_cosine(_np.array(qv, dtype=_np.float32),
                              {k: v for k, v in _cv.items()}, k=len(_cv))
        seen, out = set(), []
        for kind, sc in ranked:
            try:
                i = int(kind.split(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if i in seen or not (0 <= i < len(spans)):
                continue
            seen.add(i)
            text = _strip_frontmatter(full[spans[i][0]:spans[i][1]]).strip()
            if text:
                out.append({"idx": i, "score": round(float(sc), 4), "text": text})
        if not out:
            return [], "no-relevant-chunks"
        return out, None
    except Exception as e:
        return [], f"exception: {e}"


def build_preview(user_message: str) -> dict:
    """Структурированный предпросмотр инжекта для дашборда (read-only).

    Тот же поток, что _build_context_maybe_cached (гейт → поиск → карта →
    контекст главной → сборка <wiki-memory>), но: БЕЗ кэша (cache.json не
    читается и не пишется), БЕЗ log_event, БЕЗ _log_inject.
    Fail-open: любое исключение → {"error": "..."} без бросания.

    Возвращает dict:
      query, gate {decision, tokens, corpus_hits, reason?},
      hits [{slug, title, score, source, path}],
      card [{slug, title, path, tags}],
      main {slug, title, path, chunks, chunk_reason} | None,
      inject (точный текст <wiki-memory> или ""),
      meta {duration_ms, top_k, api_state, degraded, warnings}.
    """
    t0 = time.time()
    msg = user_message if isinstance(user_message, str) else ""
    out = {
        "query": msg,
        "gate": {"decision": "show"},
        "hits": [],
        "card": [],
        "main": None,
        "inject": "",
        "meta": {"duration_ms": 0.0, "top_k": None, "api_state": "",
                 "degraded": False, "warnings": []},
    }
    try:
        cfg = _load_config()
        out["meta"]["top_k"] = cfg["top_k"]
        from wiki_v2 import config as _wcfg
        if not msg or len(msg.strip()) < _wcfg.get("WIKI_MIN_QUERY_LEN", 3):
            out["gate"] = {"decision": "skip", "reason": "too-short"}
            out["meta"]["duration_ms"] = round((time.time() - t0) * 1000.0, 1)
            return out

        try:
            from wiki_v2.gateway import api_state
            st = api_state()
            out["meta"]["api_state"] = st or ""
            out["meta"]["degraded"] = (st == "degraded")
        except Exception:
            pass

        # 1. Гейт + детали (fail-open → show, как в бою)
        decision = "show"
        try:
            from wiki_v2.index_db import IndexDB
            from wiki_v2.relevance_gate import (A_count, gate_decision,
                                                get_lexicon,
                                                significant_words)
            from wiki_v2.search import INDEX_DB
            _db = IndexDB(INDEX_DB)
            try:
                lex = get_lexicon(_db)
            finally:
                _db.close()
            decision = gate_decision(msg, lex)
            gate_info = {"decision": decision,
                         "tokens": len(significant_words(msg))}
            if lex is not None:
                gate_info["corpus_hits"] = A_count(msg, lex)
            out["gate"] = gate_info
        except Exception as e:
            decision = "show"
            out["gate"] = {"decision": "show", "reason": f"gate-fail-open: {e}"}
        if decision == "skip":
            out["meta"]["duration_ms"] = round((time.time() - t0) * 1000.0, 1)
            return out

        # 2. Поиск — тот же search(), что в бою
        from wiki_v2.search import search
        hits, pages = search(msg, k=cfg["top_k"])
        if not hits:
            out["meta"]["warnings"].append("no-hits")
            out["meta"]["duration_ms"] = round((time.time() - t0) * 1000.0, 1)
            return out
        out["hits"] = [
            {"slug": s, "score": round(float(sc), 6), "source": src,
             "title": (pages.get(s) or {}).get("title", s),
             "path": (pages.get(s) or {}).get("path", "")}
            for s, sc, src in hits
        ]
        main_slug = hits[0][0]
        main_page = pages.get(main_slug)
        card_slugs = [s for s, _, _ in hits[1:1 + cfg["wiki_card_pages"]]]
        card_pages = {s: pages[s] for s in card_slugs if s in pages}
        out["card"] = _preview_card_entries(card_pages)

        # 3. Сборка контекста — БОЕВОЙ путь (_build_card/_build_context_main)
        parts = []
        if card_pages:
            card_text = _build_card(card_pages)
            if card_text:
                parts.append(
                    "Карта связанных страниц (ссылки + теги; для фактов — "
                    "read_file по пути):\n" + card_text)
        if decision != "low_confidence" and main_page:
            main_ctx = _build_context_main(main_page, msg)
            if main_ctx:
                parts.append(main_ctx)
        context = "\n\n".join(parts)
        if context:
            if len(context) > cfg["max_context_chars"]:
                context = context[:cfg["max_context_chars"]] + "\n..."
            out["inject"] = _assemble_context(context, decision=decision)
        else:
            out["meta"]["warnings"].append("empty-context")

        # 4. Дисплей-скоры чанков главной (отдельный ранкинг, только для UI)
        if main_page:
            chunks, reason = _preview_chunks(main_page, msg)
            out["main"] = {
                "slug": main_page.get("slug", main_slug),
                "title": main_page.get("title") or main_slug,
                "path": main_page.get("path", ""),
                "chunks": chunks,
                "chunk_reason": reason,
            }
        out["meta"]["duration_ms"] = round((time.time() - t0) * 1000.0, 1)
        return out
    except Exception as e:
        return {"query": msg, "error": f"{type(e).__name__}: {e}"}


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info("wiki-context plugin registered")
