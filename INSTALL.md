# Installation

One codebase, two supported deployments. Differences live in environment
variables (`profiles/`), never in the code.

## Fast install

```bash
git clone https://github.com/jonotonfoto/wiki-memory.git
cd wiki-memory

# Fresh desktop machine: code + local CPU embedding server (~640 MB download)
python tools/install.py --profile desktop --with-embed-server

# Machine that already has an embeddings endpoint (LM Studio, llama.cpp, ...)
python tools/install.py --profile desktop --embed-url http://127.0.0.1:11435/v1/embeddings

# VPS (Linux/Docker): code into /opt/hermes-data (host view of /opt/data)
python tools/install.py --profile vps
```

### Extraction needs a CHAT endpoint — don't skip this

Embeddings only power *search*. **Indexing** distills sessions into pages using
a chat LLM. Provide it during install:

```bash
# free NVIDIA cloud key (build.nvidia.com):
export NVIDIA_API_KEY=nvapi-...          # or pass --chat-key

# or any OpenAI-compatible server (LM Studio, llama-server, vLLM):
python tools/install.py --profile desktop \
    --chat-url http://127.0.0.1:1234/v1/chat/completions --chat-model <model> --chat-key KEY
```

If neither is given the installer prints a prominent warning; search still
works (keyword-only) until a chat backend exists. `tools/doctor.py` reports
the same as WARN.

The installer is idempotent — re-running updates and backs up the previous
copy (`*.bak.<timestamp>`, two newest kept). Resolved values are written to
`profiles/<profile>.env` (gitignored). It never touches the agent's own
`.env` or `config.yaml`.

## Common prerequisites

- Python 3.10+ with `numpy` and `requests`
- An OpenAI-compatible embedding endpoint:
  - **llama.cpp server** (CPU is enough): `Qwen3-Embedding-0.6B-Q8_0`, 1024-dim
  - or LM Studio, or the NVIDIA API
- Hermes Agent (sessions DB + plugin system) for auto-indexing

## Desktop (Windows)

1. Clone the repo anywhere.

2. Prepare the environment (see `profiles/desktop.env.example`):

   ```bat
   set WIKI_PATH=%LOCALAPPDATA%\hermes\wiki
   set WIKI_EMBED_BACKEND=llamaserver
   ```

3. Start the embedding server (llama.cpp on CPU):

   ```bat
   scripts\wiki_embed_serve.py
   ```

4. Index your history: `scripts\wiki_v3_sweep_loader.py`

5. Install plugins into Hermes:

   - `plugins/wiki-context/` — injects relevant pages into every turn
   - `plugins/wiki-session-finalize/` — indexes a session right after it closes

6. Optional: dashboard at `http://127.0.0.1:9120`:

   ```bat
   scripts\wiki_dashboard_serve.py
   ```

   The desktop button lives in `desktop-plugins/wiki3-dashboard/`.

7. Schedule the sweep loader in cron (every ~3 h) as a safety net.

## VPS (Linux, Docker)

The reference setup runs wiki code inside the existing Hermes container
(`/opt/data` volume), sharing its CPU-only embedding backend.

1. Copy `src/wiki_v2`, `scripts`, and `plugins` into the container's data
   volume (e.g. `/opt/hermes-data/scripts/wiki_v2`).

2. Fix ownership so the app user can write:

   ```bash
   chown -R 10000:10000 /opt/hermes-data/wiki /opt/hermes-data/scripts
   ```

3. Environment for every invocation (cron AND plugin):

   ```
   WIKI_PATH=/opt/data/wiki
   WIKI_EMBED_BACKEND=<your vps backend>
   ```

   Inject env into launching code — do not edit `.env` of the agent.

4. Cron on the host must always use the app user:

   ```bash
   docker exec -u hermes hermes python /opt/data/scripts/wiki_v2.indexer ...
   ```

   A cron line without `-u` silently recreates root-owned files and breaks
   writes.

5. Memory-constrained boxes: keep `cpus` limits small, prefer a quantized
   embedding model on CPU; skip the dashboard (it needs RAM).

## Sanity check after install

```bash
PYTHONPATH=src python -c "from wiki_v2 import config; print(config.WIKI_PATH)"
PYTHONPATH=src python -m wiki_v2.search "test query"
```

Both should run without tracebacks; the search may return few results until
the first index pass finishes.
