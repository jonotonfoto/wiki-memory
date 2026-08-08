# Nous Research Discord — #plugins-skills-and-skins

Post this in the `#plugins-skills-and-skins` channel (the official place Hermes
recommends for sharing plugins and skills).

---

**I built a semantic conversation memory for Hermes — "a notebook that never forgets" 📓**

By default, Hermes starts each chat fresh. This plugin quietly writes down
everything useful you discuss, organizes it into searchable pages, and lets the
agent recall it weeks or months later — even when you use different words.

**How it works (plain terms):**
- Every conversation is turned into a short markdown "page" with a semantic
  fingerprint (embedding).
- When you ask something later, it compares *meaning*, not just words — so
  "why does the office copier print empty sheets?" finds the page about
  "fixing the printer with blank pages."
- A wiki (readable/editable) + embeddings (meaning matching) = the strength.

**What's included:**
- Cross-platform (Windows desktop, Linux server, Docker) — no hardcoded paths
- Auto-indexing of finished sessions (cron sweep + `/new` plugin)
- Optional facts bridge into holographic memory
- 55 tests

**Install:** `hermes skills tap add jonotonfoto/wiki-memory`
or clone https://github.com/jonotonfoto/wiki-memory

Feedback welcome! 🙏
