"""run_facts_bridge.py — move queued wiki facts into holographic memory.

Reads ``.facts_pending.jsonl`` (written by the indexer) and imports each fact
into the holographic MemoryStore via ``facts_bridge_import.import_pending()``.

The bridge is optional: if MemoryStore is unavailable (no holographic plugin),
the script exits with a warning and does not crash the pipeline.

Usage:
    python run_facts_bridge.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from wiki_v2 import config

config.configure()


def main() -> int:
    try:
        from facts_bridge_import import import_pending
    except ImportError as e:
        print(f"[facts-bridge] facts_bridge_import.py not found: {e}")
        return -1
    try:
        return import_pending()
    except Exception as e:
        print(f"[facts-bridge] error: {e}")
        return -1


if __name__ == "__main__":
    sys.exit(main())
