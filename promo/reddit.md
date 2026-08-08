# Reddit — r/LocalLLaMA (or r/selfhosted)

Title suggestion: **I built "Wiki Memory": a notebook-that-never-forgets for AI agents (semantic recall)**

Body:

---

I've been using Hermes Agent (open-source autonomous agent) and kept hitting the
same wall: every chat starts fresh. Yesterday's hard-won fixes and decisions are
gone the moment you close the window.

So I built **Wiki Memory** — it quietly turns conversations into a searchable
knowledge base that the agent recalls later, even with different wording.

**The neat part:** it's a wiki (readable, editable markdown) *plus* embeddings
(meaning-matching). A wiki alone can't be searched by meaning; embeddings alone
have no structure you can edit. Together you get:
- A knowledge base you can actually read and correct
- Retrieval by *meaning* — "why does the copier print empty sheets?" finds the
  page about "fixing the printer with blank pages"

**Stack:** pure Python + SQLite + numpy. No FAISS, no vector DB, no heavy deps.
Cross-platform (Windows / Linux / Docker). Auto-indexes finished sessions
(cron + `/new` plugin). Optional facts bridge into holographic memory. 55 tests.

Repo: https://github.com/jonotonfoto/wiki-memory

Happy for any feedback on the approach — especially the embedding/retrieval
choices. Would this be useful to others running self-hosted agents?
