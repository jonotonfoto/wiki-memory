"""run_sweep.py — catch-up indexing of finished/changed sessions.

Cron entry point. Loops ``indexer.main()`` until nothing is left to index,
then runs the facts bridge (wiki facts -> holographic memory). Quiet output:
prints nothing useful when there is nothing to index (watchdog cron pattern).

Usage (every 3h on your scheduler):
    python run_sweep.py
"""
from __future__ import annotations

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from wiki_v2 import config

config.configure()


def main() -> int:
    from wiki_v2.indexer import main as indexer_main

    total_processed = 0
    loops = 0
    while True:
        loops += 1
        processed = indexer_main()
        if processed <= 0:
            break
        total_processed += processed
        time.sleep(1)  # rate-limit NVIDIA API

    # Facts bridge — only if something was indexed
    if total_processed > 0:
        try:
            from facts_bridge_import import import_pending
            import_pending()
        except Exception as e:
            print(f"[WARN] facts bridge: {e}")

    if total_processed > 0:
        print(f"[wiki-sweep] processed sessions: {total_processed} (passes: {loops})")
    else:
        print("[wiki-sweep] no finished/changed sessions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
