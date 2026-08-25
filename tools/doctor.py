#!/usr/bin/env python3
"""Post-install smoke test for Wiki Memory v3.

Usage:
    python tools/doctor.py [--search "query"]

Runs cheap checks, reports PASS/WARN/FAIL, never raises. Exit code is 1 only
on FAIL-level problems (broken imports, unwritable data dir).
"""
import argparse
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

RESULTS = []


def record(level, name, detail=""):
    RESULTS.append((level, name, detail))
    print(f"[{level:4}] {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--search", help="run one search query as a final check")
    args = ap.parse_args()

    # 1. core import
    try:
        from wiki_v2 import config

        record("PASS", "import wiki_v2.config")
    except Exception as exc:
        record("FAIL", "import wiki_v2.config", repr(exc))
        return 1

    # 2. data dir writable
    wiki_path = Path(str(getattr(config, "WIKI_PATH", "")))
    try:
        wiki_path.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=wiki_path, delete=True):
            pass
        record("PASS", "WIKI_PATH writable", str(wiki_path))
    except Exception as exc:
        record("FAIL", "WIKI_PATH writable", f"{wiki_path}: {exc!r}")
        return 1

    # 3. index database
    db = wiki_path / ".index_v2.db"
    if db.exists():
        record("PASS", "index db present", f"{db.stat().st_size} bytes")
    else:
        record("WARN", "index db missing", "first sweep will create it")

    # 3b. legacy wiki v2 leftovers
    # entities/ alone is NOT legacy: it is the live v3 page store once the
    # index DB exists (fresh install -> no DB yet -> suspicious).
    leftovers = [p.name for p in (
        wiki_path / ".facts_pending.jsonl",
        wiki_path / ".facts_done.jsonl",
    ) if p.exists()]
    if (wiki_path / "entities").is_dir() and not db.exists():
        leftovers.append("entities/")
    if leftovers:
        record("WARN", "legacy v2 artifacts still in WIKI_PATH",
               ", ".join(leftovers) + " — re-run tools/install.py to migrate them")
    else:
        record("PASS", "no legacy v2 artifacts")

    # 3c. PyYAML: without it endpoints.yaml is silently ignored and built-in
    # DEFAULTS (wrong backend/url) are used with no error anywhere.
    try:
        import yaml  # noqa: F401

        record("PASS", "pyyaml present")
    except ImportError:
        record("FAIL", "pyyaml missing",
               "endpoints.yaml will be SILENTLY ignored — pip install pyyaml")

    # 4. embedding endpoint reachability
    url = model = None
    epcfg = {}
    try:
        from wiki_v2 import endpoints

        epcfg = endpoints.load()
        backend = os.environ.get(
            "WIKI_EMBED_BACKEND",
            epcfg.get("embed", {}).get("backend", "nvidia"),
        )
        ep = epcfg.get("embed", {}).get(backend, {})
        url, model = ep.get("url"), ep.get("model")
        record("PASS", "endpoints.yaml loads", f"backend={backend}")
    except Exception as exc:
        record("WARN", "endpoints.yaml", repr(exc))
        url = url or getattr(config, "LMSTUDIO_URL", None)
        model = model or getattr(config, "LMSTUDIO_MODEL", None)
    if url:
        payload = b'{"input":["ping"],"model":"' + model.encode() + b'"}'
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= resp.status < 300
            record("PASS" if ok else "FAIL", "embed endpoint", f"{url} -> HTTP {resp.status}")
        except urllib.error.HTTPError as exc:
            record("FAIL", "embed endpoint", f"{url} -> HTTP {exc.code} (model loaded?)")
        except Exception as exc:
            record("FAIL", "embed endpoint", f"{url}: {exc!r}")
    else:
        record("WARN", "embed endpoint unknown", "set WIKI_EMBED_URL / backend config")

    # 4b. chat endpoint for extraction (key presence, no echo)
    key = os.environ.get("NVIDIA_API_KEY", "")
    chat_url = os.environ.get("NVIDIA_API_URL") or getattr(config, "NVIDIA_CHAT_URL", None) or \
        epcfg.get("chat", {}).get("url")
    if key and chat_url:
        record("PASS", "extraction: API key present", chat_url)
    elif chat_url:
        record("WARN", "extraction: no NVIDIA_API_KEY",
               "indexing will fail; search works keyword-only")
    else:
        record("WARN", "extraction: no chat endpoint configured",
               "re-run install with --chat-url/--chat-key or set NVIDIA_API_KEY")

    # 5. optional end-to-end search
    if args.search:
        try:
            from wiki_v2.search import search

            hits = search(args.search)
            record("PASS", "search()", f'{len(hits)} hit(s) for "{args.search}"')
        except Exception as exc:
            record("FAIL", "search()", repr(exc))

    fails = sum(1 for lvl, _, _ in RESULTS if lvl == "FAIL")
    warns = sum(1 for lvl, _, _ in RESULTS if lvl == "WARN")
    print(f"\n{fails} FAIL, {warns} WARN, {len(RESULTS) - fails - warns} PASS")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
