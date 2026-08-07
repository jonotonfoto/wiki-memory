# Installation

Wiki Memory runs on **Windows desktop**, **Linux server**, and inside a
**Docker container** (Hermes Agent). This guide covers all three.

> **Concept:** paths are never hardcoded. The system auto-detects the Hermes
> data dir per OS and reads any overrides from the environment / `.env`. See
> `src/wiki_v2/config.py`.

---

## 0. Prerequisites

- Hermes Agent installed (its sessions DB is the input).
- Python 3.10+ with `numpy` and `requests` (Hermes venv already has them).
- An **NVIDIA API key** for extraction + embeddings (free tier at
  build.nvidia.com). Set `NVIDIA_API_KEY`.

---

## 1. Get the code

```bash
git clone <your-repo-url> wiki-memory
cd wiki-memory
```

---

## 2. Configure

Copy the template and fill in your NVIDIA key:

```bash
cp .env.example .env
# edit .env -> set NVIDIA_API_KEY=...
```

The `.env` is read automatically by `config.py` and the plugins. All other
settings are optional (see `.env.example` for knobs).

---

## 3. Install (package mode, optional)

```bash
pip install -e .
```

Or just run from source — the scripts add `src/` to `sys.path` themselves.

---

## 4. Initial full index

Indexes finished, changed sessions. Run once to build the base wiki:

```bash
python scripts/run_sweep.py
```

The first run processes up to 5 sessions per pass and loops until done. On a
large history this takes a while (each session = ~2 NVIDIA calls, ~2 min per 5
sessions).

> **Windows note:** use your Hermes venv python:
> `%LOCALAPPDATA%\hermes\hermes-agent\venv\Scripts\python scripts\run_sweep.py`

---

## 5. Install the Hermes plugins

Two plugins extend Hermes:

| Plugin | Hook | Purpose |
|--------|------|---------|
| `wiki-context` | `pre_llm_call` | Auto-search wiki on every message; inject relevant pages |
| `wiki-session-finalize` | `on_session_finalize` | Immediately index a closed session on `/new` |

Copy the plugins into your Hermes plugins dir, then enable them:

```bash
# Windows: plugins live in %LOCALAPPDATA%\hermes\plugins\
# Linux:   plugins live in ~/.hermes/plugins/  (or /opt/hermes-data/plugins on VPS)
cp -r plugins/wiki-context <HERMES_PLUGINS>/
cp -r plugins/wiki-session-finalize <HERMES_PLUGINS>/

hermes plugins enable wiki-context
hermes plugins enable wiki-session-finalize
```

> Plugins take effect on the **next session** (Hermes caches the prompt
> pipeline). After enabling, start a new session.

---

## 6. Schedule the cron sweep

The sweep is the safety net — it catches up anything the plugin missed. Run it
every 3 hours.

### Windows desktop

Use the Hermes cron (`cronjob`) tool, or Task Scheduler:

```bash
# via Hermes cron (no_agent, quiet watchdog)
# schedule: every 3h, script: path/to/wiki-memory/scripts/run_sweep.py
```

Or Task Scheduler: create a task that runs every 3 h:
`<hermes-venv-python> C:\path\to\wiki-memory\scripts\run_sweep.py`

### Linux server / VPS

Add a crontab entry on the **host** (the sweep runs inside the container):

```bash
# every 3 hours, at minute 0
0 */3 * * * docker exec hermes /opt/data/.venv-wiki/bin/python /opt/data/scripts/run_sweep.py >> /opt/hermes-data/backups/wiki_sweep.log 2>&1
```

(Adjust paths to your container layout. The container mounts your Hermes data
dir at `/opt/data`.)

---

## 7. Docker (Hermes Agent container)

Add the wiki-memory files into the image or mount them as a volume. The
container's Hermes data dir is `/opt/data`; a `docker-compose` example is in
`examples/docker-compose.yml`.

Mount points:
- `src/wiki_v2` and `scripts/` → `/opt/data/scripts/`
- `plugins/*` → `/opt/data/plugins/`

Then run `run_sweep.py` on a cron inside or outside the container (see above).

---

## 8. Verify it works

```bash
# Run the test suite
python -m pytest src/wiki_v2/tests/ -q        # expect 55 passed

# Manual search
python -m wiki_v2.search "some topic from your conversations"

# Check the DB grew
python -c "import sqlite3; c=sqlite3.connect('<WIKI_PATH>/.index_v2.db'); print(c.execute('select count(*) from pages').fetchone())"
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `[LOCK] another process indexing` | A stale lock remains after a killed process. Delete `<WIKI_PATH>/.index.lock` (auto-reclaimed after 15 min). |
| Indexing "hangs" | Not a hang — NVIDIA is slow (~2 min per 5 sessions). Let it run. |
| Plugins "don't work" | They activate on the **next session**, not the current one. |
| Facts not appearing in memory | The facts bridge is optional; run `scripts/run_facts_bridge.py` or ensure it's invoked after the sweep. |
