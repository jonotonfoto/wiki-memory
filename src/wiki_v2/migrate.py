"""One-shot migration from v1 wiki (md files + .indexer_state.json) to v2."""
import hashlib
import os
import sqlite3
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wiki_v2 import config
from wiki_v2.index_db import IndexDB
from wiki_v2.gateway import embed
from wiki_v2.pages import parse_page
from wiki_v2.quality import is_garbage_text

WIKI_PATH = str(config.WIKI_PATH)
STATE_DB = str(config.STATE_DB)
INDEX_DB = os.path.join(WIKI_PATH, ".index_v2.db")
OLD_STATE = os.path.join(WIKI_PATH, ".indexer_state.json")


def main():
    db = IndexDB(INDEX_DB)
    migrated, garbage, embedded = 0, 0, 0

    for section in ("entities", "concepts", "comparisons", "queries"):
        d = os.path.join(WIKI_PATH, section)
        if not os.path.isdir(d):
            continue
        for fname in sorted(os.listdir(d)):
            if not fname.endswith(".md") or fname.startswith("."):
                continue
            path = os.path.join(d, fname)
            slug = fname[:-3]
            with open(path, encoding="utf-8", errors="replace") as f:
                md = f.read()
            parsed = parse_page(md)
            title = parsed["title"] or slug
            quality = "needs-review" if is_garbage_text(md) else "ok"
            if quality == "needs-review":
                garbage += 1
            h = hashlib.sha256(md.encode()).hexdigest()[:16]
            db.upsert_page(slug=slug, title=title, section=section, path=path,
                           content_hash=h, summary="", quality=quality)
            text = f"{title}\n{' '.join(parsed.get('key_topics', []))}"[:1000]
            vecs = embed([text], input_type="passage")
            if vecs:
                db.set_embedding(slug, np.array(vecs[0], dtype=np.float32))
                embedded += 1
            migrated += 1
            print(f"[MIGRATE] {slug} quality={quality}")

    # Register all existing sessions as indexed (don't reprocess old data)
    if os.path.exists(STATE_DB):
        conn = sqlite3.connect(STATE_DB)
        for (sid,) in conn.execute("SELECT DISTINCT id FROM sessions"):
            db.mark_session_indexed(sid)
        conn.close()

    db.close()
    print(f"\n[DONE] migrated={migrated} garbage={garbage} embedded={embedded}")
    print("Garbage pages need manual review or re-extraction.")


if __name__ == "__main__":
    main()
