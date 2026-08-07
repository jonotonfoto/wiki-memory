# extract.py
"""Structured extraction from conversation text with garbage validation."""
import json
from .nvidia_client import chat_completion
from .quality import is_garbage_text

_SYSTEM = ("Ты — аналитик, извлекающий структурированную информацию из разговоров. "
           "Отвечай ТОЛЬКО валидным JSON без markdown-обёрток.")

_PROMPT = """Проанализируй этот разговор и извлеки ключевую информацию для базы знаний.

Заголовок сессии: {title}

Разговор:
{text}

Верни ТОЛЬКО валидный JSON без markdown-обёрток:
{{
  "summary": "Краткое описание разговора (1-2 предложения на русском)",
  "key_topics": ["тема1", "тема2"],
  "decisions": ["решение1"],
  "facts": ["факт1"],
  "links": ["ссылка1"],
  "entities": ["сущность1"],
  "concepts": ["концепция1"]
}}

Если какого-то поля нет — верни пустой массив или пустую строку. Всё на русском языке."""

_FIELDS = ["key_topics", "decisions", "facts", "links", "entities", "concepts"]


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


def _validate(data) -> bool:
    """Extraction is valid if summary is non-garbage and fields are lists."""
    if not isinstance(data, dict):
        return False
    if is_garbage_text(str(data.get("summary", ""))):
        return False
    return all(isinstance(data.get(f, []), list) for f in _FIELDS)


def _normalize(data: dict) -> dict:
    return {
        "summary": str(data.get("summary", "")).strip(),
        **{f: [str(x) for x in data.get(f, [])][:20] for f in _FIELDS},
    }


def extract_content(title: str, text: str) -> dict:
    """Extract structured content. Always returns a dict with 'quality':
    'ok' (validated AI) or 'fallback' (no-AI basic page)."""
    prompt = _PROMPT.format(title=title, text=text[:8000])

    # Attempt 1: normal temperature; Attempt 2: lower temperature (deterministic)
    for temp in (0.3, 0.1):
        raw = chat_completion(_SYSTEM, prompt, temperature=temp)
        data = clean_json(raw or "")
        if data and _validate(data):
            out = _normalize(data)
            out["quality"] = "ok"
            return out
        print(f"[WARN] extraction invalid at temp={temp}, retrying")

    # Fallback: no-AI page from raw conversation
    first_user = ""
    for line in text.split("\n"):
        if line.startswith("👤"):
            first_user = line.lstrip("👤: ").strip()[:300]
            break
    return {
        "summary": f"Сессия: {title}. Автоматический анализ не удался, сохранён сырой фрагмент.",
        "key_topics": [title] if title else [],
        "decisions": [],
        "facts": [first_user] if first_user else [],
        "links": [], "entities": [], "concepts": [],
        "quality": "fallback",
    }
