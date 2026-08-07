"""facts_bridge_import.py — move facts from .facts_pending.jsonl into holographic memory.

Run after the wiki indexer (via run_sweep.py, run_facts_bridge.py, or standalone).
Reads the JSONL queue written by ``wiki_v2.facts_bridge.queue_facts``, adds each
fact to the holographic MemoryStore, then archives processed lines to
``.facts_done.jsonl``.

Cross-platform: imports ``plugins.memory.holographic.store.MemoryStore`` from
the Hermes install dir. If holographic memory is not present, the bridge is a
no-op (prints a warning) — the rest of the wiki pipeline keeps working.

Requires: ``HERMES_AGENT_DIR`` (or Hermes home) on sys.path so the holographic
plugin is importable. ``config.configure()`` handles this.
"""
from __future__ import annotations

import json
import os
import sys

from wiki_v2 import config

config.configure()

WIKI_PATH = str(config.WIKI_PATH)
PENDING = os.path.join(WIKI_PATH, ".facts_pending.jsonl")
DONE = os.path.join(WIKI_PATH, ".facts_done.jsonl")


def _resolve_hermes_site():
    """Add the Hermes install dir to sys.path so holographic plugin imports."""
    for candidate in (str(config.HERMES_AGENT_DIR), str(config.HERMES_HOME)):
        if candidate not in sys.path and os.path.isdir(candidate):
            sys.path.insert(0, candidate)


def import_pending() -> int:
    if not os.path.exists(PENDING):
        print("[facts-bridge] no queue — nothing to import.")
        return 0

    _resolve_hermes_site()

    try:
        from plugins.memory.holographic.store import MemoryStore
    except ImportError as e:
        print(f"[facts-bridge] WARN: holographic MemoryStore not importable: {e}")
        print("[facts-bridge] facts bridge skipped (wiki pipeline continues).")
        return -1

    store = MemoryStore()

    imported = 0
    skipped_dupes = 0
    errors = 0
    done_lines = []

    with open(PENDING, "r", encoding="utf-8") as f:
        lines = f.readlines()

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            errors += 1
            continue

        content = item.get("content", "").strip()
        if not content:
            continue

        category = "general"
        tags = item.get("tags", "")
        if "decision" in tags:
            category = "project"
        elif "fact" in tags:
            category = "general"

        try:
            store.add_fact(content, category=category, tags=tags)
            imported += 1
        except Exception as e:
            if "UNIQUE" in str(e) or "IntegrityError" in str(e):
                skipped_dupes += 1
            else:
                errors += 1
                print(f"[facts-bridge] error: {e}")
        done_lines.append(line)

    if done_lines:
        with open(DONE, "a", encoding="utf-8") as f:
            f.write("\n".join(done_lines) + "\n")
        os.remove(PENDING)

    print(f"[facts-bridge] imported: {imported}, dupes: {skipped_dupes}, errors: {errors}")
    return imported


if __name__ == "__main__":
    sys.exit(import_pending())
