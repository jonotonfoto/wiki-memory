# pages.py
"""Wiki page rendering, parsing, and topic-based merging."""
import json
import os
import re

FIELDS = ["key_topics", "decisions", "facts", "links", "entities", "concepts"]


def write_meta(path: str, meta: dict) -> None:
    """Writes a meta.json file next to the given path atomically."""
    try:
        meta_path = os.path.splitext(path)[0] + ".json"
        tmp_path = meta_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, meta_path)
    except Exception as e:
        print(f"[WARN] Failed to write meta for {path}: {e}")


def render_page(title: str, content: dict, date_str: str,
                sources: list, updated: str = None) -> str:
    updated = updated or date_str
    tags = ["discussion"]
    if content.get("decisions"):
        tags.append("decision")
    if content.get("facts"):
        tags.append("fact")
    if content.get("quality") == "fallback":
        tags.append("needs-review")

    body = [content.get("summary", "")]
    section_titles = {
        "key_topics": "Темы", "decisions": "Решения", "facts": "Факты",
        "links": "Ссылки", "entities": "Сущности", "concepts": "Концепции",
    }
    for field in FIELDS:
        items = content.get(field) or []
        if items:
            body.append(f"\n## {section_titles[field]}\n" +
                        "\n".join(f"- {i}" for i in items))
    src = "\n".join(f"- {s}" for s in sources)
    body.append(f"\n## Источники\n{src}")
    # S2.5.6: противоречия
    if content.get("contested"):
        contra = content.get("contradictions") or []
        if contra:
            body.append("\n## Противоречия\n" + "\n".join(f"- {c}" for c in contra))
    body.append(f"\n---\n📅 Дата разговора: {date_str}")

    contested_line = "contested: true\n" if content.get("contested") else ""
    return f"""---
title: "{title}"
created: {date_str}
updated: {updated}
type: entity
tags: [{', '.join(tags)}]
confidence: {'low' if content.get('quality') == 'fallback' else 'medium'}
{contested_line}quality: {content.get('quality', 'ok')}
sources: [{', '.join(sources)}]
---

# {title}

{chr(10).join(body)}"""


def parse_page(md: str) -> dict:
    """Parse rendered page back into dict (title, fields, sources)."""
    out = {"title": "", "sources": [], **{f: [] for f in FIELDS}}
    m = re.search(r'^title:\s*"(.+)"', md, re.MULTILINE)
    if m:
        out["title"] = m.group(1)
    m = re.search(r"^sources:\s*\[(.*)\]", md, re.MULTILINE)
    if m and m.group(1).strip():
        out["sources"] = [s.strip() for s in m.group(1).split(",")]
    section_map = {
        "Темы": "key_topics", "Решения": "decisions", "Факты": "facts",
        "Ссылки": "links", "Сущности": "entities", "Концепции": "concepts",
    }
    current = None
    for line in md.split("\n"):
        h = re.match(r"^## (.+)$", line)
        if h:
            current = section_map.get(h.group(1).strip())
            continue
        if current and line.startswith("- "):
            out[current].append(line[2:].strip())
    return out


def merge_content(old: dict, new: dict) -> dict:
    """Merge new extraction into existing page content, dedup preserving order.

    S2.5.6: детекция противоречий в поле "facts". Факты с одной темой
    (совпадает первый ключ/существительное), но разным содержанием считаются
    противоречием — старый факт НЕ затирается, оба попадают в contradictions,
    флаг contested=True. Без противоречий — contested=False, contradictions=[].
    """
    merged = {}
    for field in FIELDS:
        seen = list(old.get(field, []))
        for item in new.get(field, []):
            if item not in seen:
                seen.append(item)
        merged[field] = seen

    # S2.5.6: противоречия в фактах
    old_facts = [str(f) for f in (old.get("facts") or [])]
    new_facts = [str(f) for f in (new.get("facts") or [])]
    contradictions = []
    for nf in new_facts:
        nkey = _fact_key(nf)
        if not nkey:
            continue
        for of in old_facts:
            if _fact_key(of) == nkey and of.strip() != nf.strip():
                contradictions.append(f"старый: {of} | новый: {nf}")
    merged["contested"] = bool(contradictions)
    merged["contradictions"] = contradictions
    return merged


def _fact_key(fact: str) -> str:
    """Нормализованный ключ факта: первые 5 значимых символов до пробела."""
    s = fact.strip().lower()
    if not s:
        return ""
    # первое слово/сущность (до пробела), обрезаем пунктуацию
    first = s.split()[0].strip(".,;:!?()\"'«»")
    return first[:5]


def find_merge_target(
    new_topics: list, candidates: list, threshold: float = 0.20, new_title=None
):
    """Return slug of candidate whose topics overlap >= threshold, else None.

    Используем корневое сравнение (первые 5 букв): «сознание» и «сознания»
    считаются одним. Порог ниже (0.20), потому что экстрактор теперь
    добавляет синонимы — точное пересечение меньше, но тема та же.

    Если передан new_title — его нормализованное значение добавляется в набор
    «тем» новой страницы как ДОПОЛНИТЕЛЬНЫЙ сигнал (бонус к пересечению). Это
    помогает сливать страницы с одинаковым title, НО НЕ безусловно: если теги
    у новой и кандидата совсем не пересекаются (разные темы), score остаётся
    ниже порога → НЕ сливаем (иначе 4 разные задачи с одним префиксом «Прочитай
    бриф» схлопнулись бы в одну).
    """
    # 0) Нормализованный title новой страницы — сравнивается с title кандидата,
    #    чтобы применить ПОНИЖЕННЫЙ порог (title = сильный сигнал близости темы).
    if new_title is not None:
        from wiki_v2.quality import normalize_tag

        title_norm = normalize_tag(new_title)

    new_set = {t.lower()[:5] for t in new_topics if t}
    if not new_set:
        return None
    best, best_score = None, 0.0
    for cand in candidates:
        cand_set = {t.lower()[:5] for t in (cand.get("key_topics") or [])}
        for word in (cand.get("title") or "").lower().split():
            cand_set.add(word[:5])
        if not cand_set:
            continue
        inter = len(new_set & cand_set)
        if inter == 0:
            # темы не пересеклись вовсе — не сливаем (разные темы), даже если title совпал
            continue
        union = len(new_set | cand_set)
        score = inter / union if union else 0.0
        # title-совпадение = сильный сигнал: понижаем порог для таких кандидатов
        eff_thr = threshold
        if new_title is not None and candidates and \
           normalize_tag(cand.get("title") or "") == title_norm:
            eff_thr = threshold * 0.6
        if score >= eff_thr and score > best_score:
            best, best_score = cand["slug"], score
    return best


def find_semantic_merge_target(page_vec, existing_vectors, threshold=None):
    """Найти slug существующей страницы, косинус близость с которой >= threshold.

    page_vec: вектор новой страницы (np.ndarray или None).
    existing_vectors: {slug: vector} — векторы существующих страниц.
    threshold: порог косинуса (по умолчанию config.SEMANTIC_DEDUP_COSINE).

    fail-open: page_vec None → None (страница без эмбеддинга — не сливаем, пропускаем).
    Возвращает slug с максимальной косинусной близости >= threshold, иначе None.
    """
    if page_vec is None:
        return None
    import numpy as np

    from wiki_v2 import config
    thr = threshold if threshold is not None else config.SEMANTIC_DEDUP_COSINE
    best, best_cos = None, 0.0
    for slug, vec in (existing_vectors or {}).items():
        if vec is None:
            continue
        a = np.asarray(page_vec, dtype=np.float32)
        b = np.asarray(vec, dtype=np.float32)
        denom = (np.linalg.norm(a) * np.linalg.norm(b))
        if denom == 0:
            continue
        cos = float(np.dot(a, b) / denom)
        if cos >= thr and cos > best_cos:
            best, best_cos = slug, cos
    return best
