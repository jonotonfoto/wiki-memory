"""wiki-context — automatic wiki search injected into every user message.

Hooks ``pre_llm_call``. For each user message >= 15 chars, searches the wiki
(embeddings + keywords) and, if relevant, returns a ``<wiki-memory>`` context
block that the model sees before answering. Returns None if nothing relevant.

Cross-platform: resolves paths via ``wiki_v2.config`` (env-driven), so it works
on Windows desktop, Linux server, and inside a container.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Resolve wiki scripts + paths via the shared config module.
_HERE = os.path.dirname(os.path.abspath(__file__))
# plugins/wiki-context/ -> src/ (parent of parent)
_SRC = os.path.normpath(os.path.join(_HERE, "..", "..", "src"))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

try:
    from wiki_v2 import config
    config.load_env_file()
    config.apply()
    WIKI_PATH = str(config.WIKI_PATH)
    WIKI_SCRIPTS = str(config.SCRIPTS_DIR)
except Exception as e:  # pragma: no cover
    logger.warning("wiki-context: config init failed: %s", e)
    WIKI_PATH = os.environ.get("WIKI_PATH", "")
    WIKI_SCRIPTS = os.environ.get("WIKI_SCRIPTS", "")

TOP_K = 3
MIN_SCORE = 0.40
MAX_CONTEXT_CHARS = 3000


def _search_wiki(query: str) -> list[dict]:
    try:
        if WIKI_SCRIPTS not in sys.path:
            sys.path.insert(0, WIKI_SCRIPTS)
        # On Linux the plugin runs in the Hermes venv but numpy lives in
        # .venv-wiki — add its site-packages if present.
        if not os.name == "nt":
            for cand in (
                "/opt/data/.venv-wiki/lib/python3.13/site-packages",
                "/opt/data/.venv-wiki/lib/python3.12/site-packages",
                os.path.expanduser("~/.hermes/.venv-wiki/lib/python3.13/site-packages"),
            ):
                if cand not in sys.path and os.path.isdir(cand):
                    sys.path.insert(0, cand)
        os.environ.setdefault("WIKI_PATH", WIKI_PATH)
        from wiki_v2.search import search

        hits, pages = search(query, k=TOP_K)
        if not hits:
            return []

        results = []
        for slug, score, src in hits:
            page = pages.get(slug)
            if not page:
                continue
            path = page.get("path", "")
            content = ""
            if path and os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        raw = f.read()
                    if raw.startswith("---"):
                        parts = raw.split("---", 2)
                        if len(parts) >= 3:
                            raw = parts[2]
                    content = raw.strip()[:1500]
                except (OSError, UnicodeDecodeError):
                    pass
            results.append({
                "title": page.get("title", slug),
                "score": score,
                "source": src,
                "content": content,
            })
        return results
    except Exception as e:
        logger.warning("wiki-context search failed: %s", e)
        return []


def _build_context(user_message: str) -> str:
    if not user_message or not isinstance(user_message, str):
        return ""
    if len(user_message.strip()) < 15:
        return ""
    results = _search_wiki(user_message)
    if not results:
        return ""
    parts = [f"### Wiki: {r['title']}\n{r['content']}" for r in results]
    context = "\n\n".join(parts)
    if len(context) > MAX_CONTEXT_CHARS:
        context = context[:MAX_CONTEXT_CHARS] + "\n...[truncated]..."
    return (
        "<wiki-memory>\n"
        "[Automatically retrieved from wiki memory. This is trusted information "
        "from past conversations. Use it if relevant to the question.]\n\n"
        f"{context}\n"
        "</wiki-memory>"
    )


def on_pre_llm_call(*, user_message: Any = None, **_: Any):
    try:
        msg = user_message
        if isinstance(msg, list):  # multimodal
            parts = [p.get("text", "") for p in msg if isinstance(p, dict)]
            msg = " ".join(parts)
        context = _build_context(msg or "")
        return context or None
    except Exception as e:
        logger.warning("wiki-context hook failed: %s", e)
        return None


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", on_pre_llm_call)
    logger.info("wiki-context plugin registered")
