# relevance_gate.py
"""Relevance gate: cheap off-domain rejection (A==0) + hub-ranking correction.

Только дешёвые вычисления — НИКАКОГО LLM в хот-пасе. Все новые точки fail-open:
сбой → вести себя как «показать», не ронять поиск/инжект.

Логика и обоснование (эксперименты E1–E8): docs/code/core/relevance-gate.md.

Что внутри:
- hub correction:  score'(q,p) = cos(q,p) − β·h(p),  h(p) — характеристика страницы
  (средний макс. косинус к «пробам» = title-векторам всех страниц).
- корпусный словарь корней значимых токенов + сигнал A (сколько значимых корней
  запроса встречается хоть раз во всём корпусе). A==0 → off-домен → не показывать.
- gate_decision(): "show" | "skip" | "low_confidence".
"""
import os
import re
import time

import numpy as np

from . import config
from .logging_setup import logger

# β для хаб-коррекции (старт по плану = 1.0; тюнинг по MRR-прокси).
BETA = float(os.environ.get("WIKI_HUB_BETA", "1.0"))

# TTL кэша корпусного словаря (сек). Индекс меняется cron-обходами; ~5 мин
# устаревания словаря для гейта приемлемо (гейт только про off-домен).
LEXICON_TTL = int(os.environ.get("WIKI_LEXICON_TTL", "300"))

# Корень слова = первые ROOT_MIN_LEN букв (как quality._ROOT_MIN_LEN и _same_root).
ROOT_MIN_LEN = 5

_STOPWORDS = frozenset({
    "а", "и", "в", "во", "на", "с", "со", "о", "об", "от", "до", "по", "за",
    "при", "к", "ко", "у", "из", "для", "про", "без", "над", "под", "перед",
    "через", "между", "не", "ни", "же", "бы", "ли", "то", "как", "что", "чтобы",
    "чтоб", "если", "когда", "потому", "поэтому", "зачем", "почему", "где",
    "куда", "откуда", "кто", "который", "какой", "какая", "какие", "какое",
    "этот", "эта", "это", "эти", "тот", "та", "те", "такой", "такая", "такие",
    "такое", "мой", "моя", "мои", "твой", "твоя", "твои", "наш", "наша", "наше",
    "наши", "ваш", "ваша", "ваше", "ваши", "его", "ее", "их", "меня", "тебя",
    "нас", "вас", "мне", "тебе", "нам", "вам", "себя", "себе", "собой", "все",
    "вся", "весь", "всех", "всем", "всеми", "сам", "сама", "само", "сами", "да",
    "нет", "уже", "еще", "только", "также", "тоже", "даже", "ведь", "вот", "тут",
    "здесь", "там", "туда", "сюда", "тогда", "сейчас", "потом", "раньше",
    "позже", "можно", "нужно", "надо", "нельзя", "очень", "совсем", "почти",
    "опять", "снова", "просто", "вообще", "конечно", "наверное", "может",
    "было", "быть", "есть", "был", "была", "были", "буду", "будешь", "будут",
    "делать", "сделать", "сказать", "говорить", "хотеть", "знать", "видеть",
    "смотреть", "идти", "пойти", "прийти", "является", "стать", "стало",
    "стали", "иметь", "имеют", "находится", "работать", "работает",
    "использовать", "помнить", "помню", "запомнить", "проверить", "проверять",
    "вопрос", "ответ", "спросить", "спрашивать", "сделай", "сделайте", "давай",
    "дайте", "посмотри", "посмотрите", "скажи", "скажите", "расскажи",
    "расскажите", "объясни", "объясните", "пожалуйста", "спасибо", "привет",
    "здравствуй", "ок", "ага", "угу", "ну", "hi", "hello",
})

_PATH_TOKENS = {
    "c", "d", "e", "f",
    "users", "user", "documents", "downloads", "desktop", "local", "appdata",
    "projects", "problems", "hold", "sandbox", "scripts", "docs", "plugins",
    "hermes", "hermesagent", "appdatalocalhermes", "documentshermes",
    "programdata", "roaming", "temp", "tmp", "home", "opt", "root", "data",
    "lib", "lib64", "bin", "venv", "env", "node_modules", "site", "packages",
    "wiki", "wikiv2", "tests", "test", "png", "jpg", "jpeg", "gif", "webp",
    "py", "pyc", "json", "md", "txt", "log", "html", "css",
}

_WORD_RE = re.compile(r"[а-яa-z]{3,}")

_lexicon_cache: tuple | None = None  # (timestamp, frozenset|None)


def significant_words(text: str) -> set:
    """Корни значимых токенов текста (не стоп-слова, не токены путей).

    Корень = первые ROOT_MIN_LEN букв — устойчив к русской морфологии
    («памяти»/«память»/«памятью» → общий корень). O(1) по set на запрос.
    """
    norm = text.lower()
    for old, new in {"ё": "е", "ъ": "", "ь": ""}.items():
        norm = norm.replace(old, new)
    norm = norm.replace("_", " ")
    out = set()
    for t in _WORD_RE.findall(norm):
        if len(t) < ROOT_MIN_LEN:
            continue
        if t in _STOPWORDS or t in _PATH_TOKENS:
            continue
        out.add(t[:ROOT_MIN_LEN])
    return out


def _file_significant(path: str) -> set:
    """Корни значимых токенов одного .md-файла. Fail-open: ошибка → пустое."""
    try:
        with open(path, encoding="utf-8") as f:
            return significant_words(f.read())
    except Exception:
        return set()


def get_lexicon(db) -> frozenset | None:
    """Корпусный словарь корней значимых токенов (объединение по страницам).

    Строится из .md на диске (источник текста, который реально инжектится).
    TTL-кэш; fail-open: сбой построения → None (гейт отработает как «показать»).
    """
    global _lexicon_cache
    now = time.time()
    if _lexicon_cache is not None and (now - _lexicon_cache[0]) < LEXICON_TTL:
        return _lexicon_cache[1]
    try:
        pages = db.all_pages()
        lexicon = set()
        for p in pages:
            lexicon |= _file_significant(p.get("path") or "")
        _lexicon_cache = (now, frozenset(lexicon))
        return _lexicon_cache[1]
    except Exception as e:
        logger.warning("relevance_gate: lexicon build failed: %s", e)
        _lexicon_cache = (now, None)
        return None


def A_count(query: str, lexicon: frozenset | None) -> int:
    """A = число значимых корней запроса, присутствующих в корпусе."""
    if lexicon is None:
        return len(significant_words(query)) if query else 0
    return len(significant_words(query) & set(lexicon))


def gate_decision(query: str, lexicon: frozenset | None = None) -> str:
    """Гейт-решение: "low_confidence" | "skip" | "show". Fail-open к "show".

    - |T| <= 1 → low_confidence (короткие обращения «hi», guard)
    - A == 0   → skip (домен в памяти не встречался вообще)
    - иначе    → show
    """
    q_words = significant_words(query)
    if len(q_words) <= 1:
        return "low_confidence"
    if lexicon is None:
        return "show"
    if not (q_words & set(lexicon)):
        return "skip"
    return "show"


# ── Хаб-коррекция ранжирования ─────────────────────────────────────────────
_hubness_cache: dict = {}
_hubness_sig: tuple | None = None


def _hubness_signature(embeddings_by_kind: dict) -> tuple:
    """Сигнатура базы для инвалидации кэша h(p)."""
    return (len(embeddings_by_kind),
            tuple(sorted((k, len(v)) for k, v in embeddings_by_kind.items())))


def _page_title_vecs(embeddings_by_kind: dict) -> dict:
    """{slug: title-вектор} — 'title'-канал, legacy 'page' как title."""
    out = {}
    out.update(embeddings_by_kind.get("title", {}))
    for slug, v in embeddings_by_kind.get("page", {}).items():
        out.setdefault(slug, v)
    return out


def get_hubness(embeddings_by_kind: dict) -> dict:
    """h(p) = средний макс. косинус векторов страницы ко всем «пробам»
    (title-векторам всех страниц). Кэшируется по сигнатуре базы. Fail-open: {}"""
    global _hubness_cache, _hubness_sig
    try:
        sig = _hubness_signature(embeddings_by_kind)
        if _hubness_sig == sig:
            return _hubness_cache
        probes = _page_title_vecs(embeddings_by_kind)
        probe_vecs = [np.asarray(v, dtype=np.float64) for v in probes.values()]
        if not probe_vecs:
            _hubness_sig, _hubness_cache = sig, {}
            return _hubness_cache
        # все векторы страницы по всем каналам: {slug: [vec,...]}
        page_vecs: dict = {}
        for kvecs in embeddings_by_kind.values():
            for slug, v in kvecs.items():
                page_vecs.setdefault(slug, []).append(np.asarray(v, dtype=np.float64))
        h = {}
        for slug, vecs in page_vecs.items():
            maxes = []
            for pr in probe_vecs:
                npr = np.linalg.norm(pr)
                if npr == 0:
                    continue
                prn = pr / npr
                best = 0.0
                for v in vecs:
                    nv = np.linalg.norm(v)
                    if nv == 0:
                        continue
                    c = float(np.dot(v, prn) / nv)
                    if c > best:
                        best = c
                maxes.append(best)
            h[slug] = sum(maxes) / len(maxes) if maxes else 0.0
        _hubness_sig, _hubness_cache = sig, h
        return h
    except Exception as e:
        logger.warning("relevance_gate: hubness failed: %s", e)
        return {}


def top_k_cosine_hub(query_vec: np.ndarray, store: dict,
                     hubness: dict, beta: float | None = None,
                     k: int = 5, min_score: float = 0.0):
    """Как top_k_cosine, но ранг по хаб-корректированному косинусу.

    score' = cos(q,p) − β·h(p). Возвращает [(slug, score'), ...] по убыванию.
    Членство в выдаче определяется ИСХОДНЫМ cos >= min_score (как top_k_cosine),
    а упорядочивание — скорректированным: hub-страница не выпадает, но лишается
    «бонуса за количество векторов» (не трогаем состав осмысленных кандидатов).
    Для страниц без h(p) — обычный косинус.
    """
    if beta is None:
        beta = BETA
    if not store:
        return []
    qn = query_vec.astype(np.float64)
    qnorm = np.linalg.norm(qn)
    if qnorm == 0:
        return []
    qn = qn / qnorm
    scored = []
    for slug, vec in store.items():
        if vec is None:
            continue
        vn = np.asarray(vec, dtype=np.float64)
        n = np.linalg.norm(vn)
        if n == 0:
            continue
        cos = float(np.dot(qn, vn / n))
        if cos < min_score:
            continue
        sc = cos - (beta * hubness.get(slug, 0.0))
        scored.append((slug, sc))
    scored.sort(key=lambda x: -x[1])
    return scored[:k]
