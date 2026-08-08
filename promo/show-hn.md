# Hacker News — "Show HN"

Title suggestion: **Show HN: Wiki Memory — a "notebook that never forgets" for AI agents**

Body:

---

I built a semantic memory layer for Hermes Agent (an open-source autonomous
agent). The core idea: **knowledge you build once becomes knowledge you keep.**

**The problem:** AI assistants start each conversation fresh. Anything useful
you worked out yesterday is gone when you close the chat.

**The solution:** every conversation is quietly distilled into a readable
markdown "page" with a semantic fingerprint (an embedding). Later, the agent
retrieves by *meaning*, not just keywords — so a question phrased with entirely
different words still finds the right page.

**Why wiki + embeddings:**
- A wiki alone is human-readable but hard to search by meaning.
- Embeddings alone are fuzzy "related stuff" with no structure you can edit.
- Together: you keep an editable, trustworthy knowledge base, and the agent can
  find the right memory at the right moment.

**Example:** you fix "printer prints blank pages" today. Weeks later you ask
"why does the office copier spit out empty sheets?" — a keyword search finds
nothing, but embeddings see printer≈copier, blank≈empty and pull up the page.

**Stack:** pure Python + SQLite + numpy (no FAISS, no external vector DB).
Cross-platform (Windows, Linux, Docker). Includes auto-indexing of finished
sessions, a `/new` plugin for instant recall, and an optional bridge into
holographic memory. 55 tests.

Repo: https://github.com/jonotonfoto/wiki-memory

Happy to answer questions about the design, the embedding choice, or the
indexing lifecycle. Feedback very welcome!
