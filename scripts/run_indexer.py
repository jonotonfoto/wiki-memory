"""run_indexer.py — run one indexing pass (up to 5 finished/changed sessions).

Cross-platform entry point. Call ``config.configure()`` once to load .env,
resolve paths per OS, and wire sys.path, then invoke the indexer.

Usage:
    python run_indexer.py                 # background sweep-style single pass
    python run_indexer.py --session <id>  # index one specific session
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from wiki_v2 import config

config.configure()


def main():
    import argparse

    from wiki_v2.indexer import main as indexer_main

    parser = argparse.ArgumentParser(description="Wiki indexer — single pass")
    parser.add_argument("--session", dest="session_id", default=None,
                        help="Index only this session (single-session mode)")
    args = parser.parse_args()

    count = indexer_main(session_id=args.session_id)
    return count


if __name__ == "__main__":
    sys.exit(main())
