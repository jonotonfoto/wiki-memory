# extract.py
"""Structured extraction from conversation text with garbage validation."""
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

from .logging_setup import logger
from .gateway import chat_completion
from .quality import is_garbage_text

_SYSTEM = ("Ты — аналитик, извлекающий структурированную информацию из разговоров. "
           "Отвечай ТОЛЬКО валидным JSON без markdown-обёрток. "
           "Работаешь на русском языке. Точно следуй схеме полей.")

_PROMPT = """Проанализируй этот разговор и извлеки ключевую информацию для базы знаний.

Заголовок сессии: {title}

Разговор:
{text}

ВАЖНО про key_topics: включай не только темы, которые прямо названы в разговоре,
но и СВЯЗАНЫЕ понятия и синонимы, которые помогают найти эту страницу по смыслу.
Например, если разговор про Выготского и культурно-историческую теорию — добавь
также «сознание», «психология», «мышление» (даже если эти слова не звучали явно).
Это нужно, чтобы поиск находил страницу по абстрактным/синонимичным запросам.

Структурируй key_topics по 4 группам (все в одном массиве key_topics):
1. ПРЯМЫЕ темы — о чём разговор напрямую (названы явно).
2. СИНОНИМЫ/близкие понятия — другие слова для тех же тем.
3. АБСТРАКЦИИ/обобщения — обобщённая категория (напр. «психология», «инструменты»).
4. КОНКРЕТИКА/примеры — конкретные сущности, имена, названия, упомянутые в разговоре.
Итог: 5-15 тегов, каждый по 1-3 слова.

ПРАВИЛА для тегов (key_topics, entities, concepts):
1. Все теги — в нижнем регистре, через пробел, БЕЗ подчёркиваний и дефисов.
   Пример: «субтитры-редактор» → «редактор субтитров», «N.Zakomoldina» → «закомолдина».
2. НЕ дублируй по смыслу: если два тега означают одно и то же (например
   «редактор субтитров» и «субтитры редактор») — оставь ОДИН канонический вариант.
3. Теги должны быть короткими (1-3 слова), конкретными, без общих слов («важное», «разговор»).

ПРИМЕР правильного ответа:
{{
  "summary": "Разговор о настройке редактора субтитров.",
  "key_topics": ["редактор субтитров", "инструменты"],
  "decisions": ["использовать закомолдину для монтажа"],
  "facts": ["редактор называется закомолдина"],
  "links": [],
  "entities": ["закомолдина"],
  "concepts": ["инструменты монтажа"],
  "triplets": [{{"subject": "субтитры", "predicate": "модифицируется", "object": "редактором"}}, {{"subject": "монтаж", "predicate": "выполняется в", "object": "закомолдина"}}]
}}

Верни ТОЛЬКО валидный JSON без markdown-обёрток по схеме:
{{
  "summary": "Краткое описание разговора (1-2 предложения на русском)",
  "key_topics": ["тема1", "тема2"],
  "decisions": ["решение1"],
  "facts": ["факт1"],
  "links": ["ссылка1"],
  "entities": ["сущность1"],
  "concepts": ["концепция1"],
  "triplets": [{{"subject": "объект1", "predicate": "отношение", "object": "объект2"}}]
}}

Если какого-то поля нет — верни пустой массив или пустую строку. Всё на русском языке."""

_FIELDS = ["key_topics", "decisions", "facts", "links", "entities", "concepts"]

# Бюджет LLM-вызовов на один прогон сессии: при исчерпании extract идёт в fallback,
# а не продолжает retry (иначе reasoning-empty → вечный перебор temp).
EXTRACT_MAX_LLM_CALLS = 6

_llm_calls = 0  # модульный счётчик вызовов chat_completion на текущий прогон
_LLM_LOCK = threading.Lock()  # MAP считает вызовы из параллельных потоков


def _reset_llm_budget() -> None:
    """Обнулить бюджет LLM-вызовов (вызывается в начале обработки сессии)."""
    global _llm_calls
    with _LLM_LOCK:
        _llm_calls = 0


def _count_llm_call() -> None:
    """Инкремент счётчика под локом: MAP зовёт extract_content из ThreadPool,
    `x += 1` без лока не атомарен (гонка → недосчёт → перерасход бюджета)."""
    global _llm_calls
    with _LLM_LOCK:
        _llm_calls += 1


def _llm_budget_exhausted() -> bool:
    """True, если бюджет LLM-вызовов исчерпан."""
    return _llm_calls >= EXTRACT_MAX_LLM_CALLS


def clamp_confidence(conf) -> float:
    """S4.1 — Clamp a single fact confidence to [0.0, 1.0].

    None / non-number → config.WIKI_FACT_CONFIDENCE_DEFAULT (0.5).
    <0 → 0.0, >1 → 1.0. Otherwise return float(conf).
    """
    from . import config as _config
    if conf is None:
        return _config.WIKI_FACT_CONFIDENCE_DEFAULT
    try:
        val = float(conf)
    except (TypeError, ValueError):
        return _config.WIKI_FACT_CONFIDENCE_DEFAULT
    if val < 0.0:
        return 0.0
    if val > 1.0:
        return 1.0
    return val


def clean_json(raw: str):
    """Parse JSON possibly wrapped in markdown fences. None on failure."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        return json.loads(cleaned.strip())
    except (json.JSONDecodeError, IndexError):
        return None


def validate_extract(data: dict) -> bool:
    """S3.3 — строгая JSON-схема extract."""
    if not isinstance(data, dict):  # fail-open: не dict -> False
        return False

    # summary: str (не garbage)
    summary = data.get("summary")
    if not isinstance(summary, str) or is_garbage_text(summary):
        return False

    # key_topics/decisions/facts/links/entities/concepts: list[str], каждая длина <= 20
    for field in _FIELDS:
        val = data.get(field)
        if not isinstance(val, list):
            return False
        if len(val) > 20:
            return False
        if any(not isinstance(x, str) for x in val):
            return False

    # quality: str in {'ok','fallback'} (если поле есть)
    if "quality" in data:
        if data["quality"] not in {"ok", "fallback"}:
            return False

    return True


def _normalize(data: dict, existing_facts: list | None = None) -> dict:
    facts_raw = data.get("facts", [])
    # S4.1: if LLM returned dicts with confidence, extract text + confidences
    fact_confidences: list[float] = []
    facts_strs: list[str] = []
    for item in facts_raw:
        if isinstance(item, dict):
            facts_strs.append(str(item.get("text", "")))
            fact_confidences.append(clamp_confidence(item.get("confidence")))
        else:
            facts_strs.append(str(item))

    # S4.7: normalize triplets (optional, not in _FIELDS)
    triplets_out = []
    for t in (data.get("triplets") or []):
        if isinstance(t, dict):
            s, p, o = str(t.get("subject", "")), str(t.get("predicate", "")), str(t.get("object", ""))
            if s and p and o:
                triplets_out.append({"subject": s, "predicate": p, "object": o})
        elif isinstance(t, (list, tuple)) and len(t) >= 3:
            triplets_out.append({"subject": str(t[0]), "predicate": str(t[1]), "object": str(t[2])})
    if not triplets_out:
        triplets_out = None

    # S4.3: CoVe — optional fact verification (parallel list, does not change facts format)
    fact_verification: list[str] = []
    if existing_facts:
        for f in facts_strs:
            fact_verification.append(verify_fact(f, existing_facts))

    return {
        "summary": str(data.get("summary", "")).strip(),
        **{f: [str(x) for x in data.get(f, [])][:20] for f in _FIELDS if f != "facts"},
        "facts": facts_strs[:20],
    } | ({"fact_confidences": fact_confidences} if fact_confidences else {}) | ({"triplets": triplets_out[:10]} if triplets_out else {}) | ({"fact_verification": fact_verification} if fact_verification else {})


def _make_fallback(title: str, text: str) -> dict:
    """No-AI page from raw conversation."""
    first_user = ""
    for line in text.split("\n"):
        if line.startswith("👤"):
            first_user = line.lstrip("👤: ").strip()[:300]
            break
    return {
        "summary": f"Сессия: {title}. Автоматический анализ не удался, сохранён сырой фрагмент.",
        "key_topics": [title] if title else [],
        "decisions": [], "facts": [first_user] if first_user else [],
        "links": [], "entities": [], "concepts": [],
        "triplets": [],
        "quality": "fallback",
    }


def extract_content(title: str, text: str) -> dict:
    """Extract structured content. Always returns a dict with 'quality':
    'ok' (validated AI) or 'fallback' (no-AI basic page)."""
    global _llm_calls
    logger.info("[EXTRACT] начало экстракции title=%r", title)

    if _llm_budget_exhausted():
        logger.info("[EXTRACT] бюджет LLM-вызовов исчерпан (%d), fallback для title=%r", _llm_calls, title)
        return _make_fallback(title, text)

    prompt = _PROMPT.format(title=title, text=text[:8000])

    # Attempt 1: normal temperature; Attempt 2: lower temperature (deterministic)
    for temp in (0.3, 0.1):
        raw = chat_completion(_SYSTEM, prompt, temperature=temp, max_tokens=5000,
                              empty_reasoning_is_error=True)
        _count_llm_call()  # считаем каждый предпринятый вызов (даже вернувший None)
        data = clean_json(raw or "")
        if data and validate_extract(data):
            out = _normalize(data)
            out["quality"] = "ok"
            logger.info("[EXTRACT] готово (ok) title=%r: %d фактов, %d тегов", title, len(out.get("facts", [])), len(out.get("key_topics", [])))
            return out
        logger.warning("[EXTRACT] невалидно при temp=%s, retry", temp)

    # Fallback: no-AI page from raw conversation
    return _make_fallback(title, text)


def extract_chunk_tags(title: str, chunk_text: str, raise_on_error: bool = False) -> list:
    """S2.5.8c: извлечь теги (key_topics) для ОДНОГО чанка.

    raise_on_error=False (по умолчанию): любое исключение → [] (fail-open для
    одиночных вызовов). raise_on_error=True: исключение пробрасывается — так
    MAP (_map_chunk_one) отличает сбой от честного «тегов нет» и делает retry.
    """
    try:
        data = extract_content(title, chunk_text)
        return data.get("key_topics", [])
    except Exception:
        if raise_on_error:
            raise
        return []


def _map_chunk_one(title: str, chunk) -> list:
    """Извлечь теги для одного чанка с retry (для параллельного MAP).

    Retry делается только при РЕАЛЬНОМ исключении (extract_chunk_tags с
    raise_on_error=True пробрасывает его наверх); при исчерпании бюджета
    LLM-вызовов или уже сработавшем reasoning-empty повтор бессмысленен —
    возвращаем [] сразу (fallback-ответ extract_content тоже не ретраим).
    """
    for _ in range(2):
        if _llm_budget_exhausted():
            return []
        try:
            tags = extract_chunk_tags(title, chunk, raise_on_error=True)
        except Exception:
            continue
        # extract_content вернул fallback (reasoning-empty или исчерпан бюджет) —
        # повтор бесполезен.
        return tags if tags else []
    return []


def _stop_requested() -> bool:
    """Return True if a graceful-stop flag (``.stop_request``) exists.

    The dashboard's ``stop_extraction`` writes this flag to ``config.WIKI_PATH``.
    ``map_chunk_tags`` checks it BETWEEN batches (of 4 parallel chunks) so the
    current batch finishes cleanly before extraction stops — never mid-chunk.
    """
    try:
        from . import config as _config
        stop_flag = os.path.join(str(_config.WIKI_PATH), ".stop_request")
        return os.path.exists(stop_flag)
    except Exception:
        return False


def map_chunk_tags(title: str, chunks: list) -> dict:
    """S2.5.9a (MAP): извлечь теги для КАЖДОГО чанка страницы.

    Чанки обрабатываются ПАРАЛЛЕЛЬНО батчем по 4 (ThreadPoolExecutor),
    чтобы ускорить экстракцию длинных сессий (пользователь: «экстракция
    должна для скорости идти 4 батчами»).

    ГРАЦИОЗНАЯ ОСТАНОВКА: между батчами (по 4) проверяется ``.stop_request``.
    Если флаг есть — текущий батч из 4 параллельных чанков ДОДЕЛЫВАЕТСЯ,
    а следующие батчи пропускаются. Так экстракция гигантской сессии
    останавливается после завершения текущих 4 чанков, не рвя их посредине.

    При ошибке модели чанк ПЕРЕДЕЛЫВАЕТСЯ (retry). Только после retry
    не получилось — пустой (fail-open, чтобы не терять весь пайплайн).
    """
    _reset_llm_budget()
    result = {}
    if not chunks:
        return result
    from . import config as _config
    # Параллелизм берём из конфига (endpoints.yaml → WIKI_CHAT_PARALLEL).
    # Облако NVIDIA: 1 (пачки по 4 → 429/блокировка); LM Studio локально: можно 4.
    BATCH = max(1, int(getattr(_config, "CHAT_PARALLEL", 1)))
    for start in range(0, len(chunks), BATCH):
        batch = list(enumerate(chunks))[start:start + BATCH]
        with ThreadPoolExecutor(max_workers=BATCH) as ex:
            futures = {i: ex.submit(_map_chunk_one, title, c) for i, c in batch}
            for i, fut in futures.items():
                try:
                    result[i] = fut.result()
                except Exception:
                    result[i] = []
        # После завершения текущего батча — проверить запрос остановки.
        if _stop_requested() and start + BATCH < len(chunks):
            logger.info("[EXTRACT] запрошена остановка — останавливаюсь после батча %d (%d чанков)", start // BATCH + 1, len(batch))
            break
        if _llm_budget_exhausted():
            logger.info("[EXTRACT] MAP: бюджет LLM-вызовов исчерпан — прекращаю обработку оставшихся чанков")
            for i in range(start + BATCH, len(chunks)):
                result[i] = []
            break
    return result


_REDUCE_SYSTEM = (
    "Ты — аналитик, сливающий теги чанков одной страницы в единое облако. "
    "Отвечай ТОЛЬКО валидным JSON-массивом строк, без markdown-обёрток, на русском."
)

_REDUCE_PROMPT = """Страница «{title}» разбита на чанки. Каждый чанк дал свои теги (key_topics).

Чанки и их теги:
{chunks_json}

Слей их в ЕДИНОЕ согласованное облако тегов:
1. Убери ДУБЛИ по смыслу (root-match): «выготский» и «теория выготского» → один тег.
2. Если теги ПРОТИВОРЕЧАТ друг другу — тег из ПОЗДНЕГО чанка выигрывает.
3. Приведи термины к одному знаменателю (каноничный вид, 1-3 слова).
4. Итог: 5-15 тегов.

Верни ТОЛЬКО JSON-массив строк."""


def reduce_chunk_tags(title: str, chunk_tags: dict, model: str = None) -> list:
    """S2.5.9b (REDUCE): слить теги всех чанков в единое облако.

    При ошибке модели (пустой/невалидный ответ) — ПЕРЕДЕЛЫВАЕТ (retry),
    а не молча берёт fallback. Только после retry не получилось — flat.
    """
    flat = []
    for i in sorted(chunk_tags.keys()):
        for t in chunk_tags.get(i, []):
            if t not in flat:
                flat.append(t)
    import json as _json
    chunks_json = _json.dumps(chunk_tags, ensure_ascii=False)
    prompt = _REDUCE_PROMPT.format(title=title, chunks_json=chunks_json)
    for attempt in range(2):  # retry: страница переделывается при ошибке
        try:
            # max_tokens достаточно большой (как в key-knowledge): reasoning-модель
            # должна успеть завершить мышление И вывести сжатый JSON в content.
            # При 400 тегах ответ обрезался -> fail-open лил все сырые теги (баг v3).
            # model=None НЕ передаём явно — иначе chat_completion получит "model": null
            # и LM Studio упадёт "Failed to load model null" (валит последний шаг каждой статьи).
            kw = {"max_tokens": 2000}
            if model:
                kw["model"] = model
            raw = chat_completion(_REDUCE_SYSTEM, prompt, **kw)
            if not raw:
                continue
            data = clean_json(raw)
            if isinstance(data, list):
                result = [str(x).strip() for x in data if str(x).strip()]
                if result:
                    return result
        except Exception:
            pass
    # Честный fail-open: если модель не смогла сжать — НЕ лить все сырые теги,
    # а взять топ-15 каноничных (дедуп по смыслу). Сохраняет замысел "10-20 тегов".
    return _top_tags(flat, 15)


def _top_tags(tags: list, limit: int) -> list:
    """Вернуть до *limit* тегов, дедуплицируя по смыслу (root-match по первым символам).

    Первый каноничный тег выигрывает; варианты того же корня отбрасываются.
    """
    seen_roots = set()
    out = []
    for t in tags:
        t = str(t).strip()
        if not t:
            continue
        root = t[:5].lower()  # root-match (как _same_root в плагине)
        if root in seen_roots:
            continue
        seen_roots.add(root)
        out.append(t)
        if len(out) >= limit:
            break
    return out


# ── S4.3: CoVe — Chain-of-Verification ──────────────────────────────────────

def verify_fact(fact: str, existing_facts: list) -> str:
    """S4.3 — Chain-of-Verification: check if a fact is consistent with existing knowledge.

    Returns ``'True'``, ``'False'``, or ``'Unknown'``.
    Fail-open: any error → ``'Unknown'``.
    If ``WIKI_COVE_ENABLED`` is False, returns ``'Unknown'`` without calling LLM.
    """
    from . import config as _config

    if not _config.WIKI_COVE_ENABLED:
        return "Unknown"
    if not fact or not isinstance(fact, str):
        return "Unknown"

    existing = "\n".join(str(f) for f in (existing_facts or []))
    user_prompt = (
        f"{_config.WIKI_COVE_PROMPT}\n\n"
        f"Уже известные факты:\n{existing}\n\n"
        f"Факт для проверки: {fact}"
    )
    try:
        raw = chat_completion(_SYSTEM, user_prompt, temperature=0.1, max_tokens=10)
        if not raw:
            return "Unknown"
        answer = raw.strip().strip(".,;:!?\"'").upper()
        if answer in ("TRUE", "FALSE", "UNKNOWN"):
            return answer
        return "Unknown"
    except Exception:
        return "Unknown"