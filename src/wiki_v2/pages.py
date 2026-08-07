# pages.py
"""Wiki page rendering, parsing, and topic-based merging."""
import re

FIELDS = ["key_topics", "decisions", "facts", "links", "entities", "concepts"]


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
    body.append(f"\n---\n📅 Дата разговора: {date_str}")

    return f"""---
title: "{title}"
created: {date_str}
updated: {updated}
type: entity
tags: [{', '.join(tags)}]
confidence: {'low' if content.get('quality') == 'fallback' else 'medium'}
quality: {content.get('quality', 'ok')}
sources: [{', '.join(sources)}]
---

# {title}

{chr(10).join(body)}
"""


def parse_page(md: str) -> dict:
    """Parse rendered page back into dict (title, fields, sources)."""
    out = {"title": "", "sources": [], **{f: [] for f in FIELDS}}
    m = re.search(r'^title:\s*"(.+)"', md, re.M)
    if m:
        out["title"] = m.group(1)
    m = re.search(r"^sources:\s*\[(.*)\]", md, re.M)
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
    """Merge new extraction into existing page content, dedup preserving order."""
    merged = {}
    for field in FIELDS:
        seen = list(old.get(field, []))
        for item in new.get(field, []):
            if item not in seen:
                seen.append(item)
        merged[field] = seen
    return merged


def find_merge_target(new_topics: list, candidates: list, threshold: float = 0.34):
    """Return slug of candidate whose topics overlap >= Jaccard threshold, else None."""
    new_set = {t.lower() for t in new_topics if t}
    if not new_set:
        return None
    best, best_score = None, 0.0
    for cand in candidates:
        cand_set = {t.lower() for t in (cand.get("key_topics") or [])}
        for word in (cand.get("title") or "").lower().split():
            cand_set.add(word)
        if not cand_set:
            continue
        inter = len(new_set & cand_set)
        union = len(new_set | cand_set)
        score = inter / union if union else 0.0
        if score >= threshold and score > best_score:
            best, best_score = cand["slug"], score
    return best
