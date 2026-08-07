"""Append session facts/decisions to .facts_pending.jsonl for fact_store import."""
import json
import os
import time

from wiki_v2 import config

PENDING = os.path.join(str(config.WIKI_PATH), ".facts_pending.jsonl")


def queue_facts(session_id: str, title: str, content: dict):
    items = []
    for fact in content.get("facts", []):
        items.append({"content": fact, "tags": "wiki,fact",
                      "source": f"session:{session_id}", "title": title})
    for dec in content.get("decisions", []):
        items.append({"content": dec, "tags": "wiki,decision",
                      "source": f"session:{session_id}", "title": title})
    if not items:
        return 0
    with open(PENDING, "a") as f:
        for it in items:
            it["queued_at"] = time.time()
            f.write(json.dumps(it, ensure_ascii=False) + "\n")
    return len(items)
