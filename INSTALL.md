# Installation Manual

One codebase, two supported deployments (Windows desktop, Linux VPS/Docker).
Differences live in environment variables — never in the code.

---

## Table of contents

1. [Requirements](#requirements)
2. [Three install scenarios](#three-install-scenarios)
3. [Extraction backend — required for indexing](#extraction-backend--required-for-indexing)
4. [What the installer does automatically](#what-the-installer-does-automatically)
5. [Upgrading from legacy wiki v2](#upgrading-from-legacy-wiki-v2)
6. [Post-install checklist](#post-install-checklist)
7. [Verification with doctor](#verification-with-doctor)
8. [Updating an existing installation](#updating-an-existing-installation)
9. [Troubleshooting](#troubleshooting)

---

## Requirements

- **Python 3.10+** with `numpy`, `requests` and `pyyaml`:
  ```bash
  pip install numpy requests pyyaml
  ```
  Without **pyyaml**, `endpoints.yaml` is *silently ignored* and the code
  falls back to built-in defaults (wrong backend/URL) with no error message —
  `tools/doctor.py` checks for it explicitly.
- **Hermes Agent** (sessions DB + plugin system) — needed for auto-indexing.
- An OpenAI-compatible **embeddings endpoint** (for search & indexing):
  - none yet? use `--with-embed-server` and one will be downloaded (~640 MB);
  - LM Studio / llama.cpp / NVIDIA API also work.
- A **chat LLM endpoint** (only for *indexing* — it distills sessions into
  pages). Search works without it; see
  [Extraction backend](#extraction-backend--required-for-indexing).

---

## Three install scenarios

### Scenario A — fresh desktop machine (fully automated)

Installs the code AND a local CPU embedding server:

```bash
git clone https://github.com/jonotonfoto/wiki-memory.git
cd wiki-memory
python tools/install.py --profile desktop --with-embed-server
```

What you get: llama.cpp (`Qwen3-Embedding-0.6B-Q8_0`, CPU, port 11435)
downloaded into `%LOCALAPPDATA%\hermes\wiki-embed\`, plus a ready
start script. Start the server once per boot:

```bat
%LOCALAPPDATA%\hermes\wiki-embed\start_wiki_embed.bat
```

### Scenario B — machine that already has an embeddings endpoint

Point the installer at your existing server instead of downloading one:

```bash
python tools/install.py --profile desktop \
    --embed-url http://127.0.0.1:11435/v1/embeddings \
    --embed-model Qwen3-Embedding-0.6B-Q8_0
```

Works with LM Studio (`http://127.0.0.1:1234/v1/embeddings`), llama.cpp,
vLLM, or any OpenAI-compatible API.

### Scenario C — VPS (Linux, inside Docker data volume)

```bash
python tools/install.py --profile vps                 # target /opt/hermes-data
# or custom location:
python tools/install.py --profile vps --target /srv/hermes-data
```

On a VPS the installer additionally generates:

- `<target>/wiki.env` + `<target>/bin/wiki_sweep_cron.sh` — the cron entry
  point that sources the wiki env AND the agent's own `.env` (chat API keys
  live there, never in git) before running the sweep loader;
- with `--with-embed-server`: the hardened systemd embed stack in
  `<target>/wiki-embed/systemd/` — memory-hardened llama-server unit,
  wake-on-request proxy, idle-unload watchdog. Enable instructions are
  printed; background: `deploy/vps/README.md`.

VPS-specific lessons (all baked into the generated files):

- **Docker networking**: containers reach host ports ONLY through the bridge
  gateway IP (`172.20.0.1` on the default network), never via `127.0.0.1`.
  Point `WIKI_EMBED_URL` at `http://172.20.0.1:<port>/v1/embeddings` and open
  the firewall for the docker subnet:
  `ufw allow from 172.20.0.0/16 to any port <port> proto tcp`.
- **Memory-hardened llama-server** (`-ub 128 -np 1`, `MemoryMax=800M`): with
  stock flags, one batch of 8+ chunk embeddings OOM-kills llama-server inside
  the cgroup limit and every batch turns into a 502/timeout retry storm.
  Swap does NOT help — Ubuntu kernels ignore cgroup swap limits.
  Client-side counterpart: `WIKI_EMBED_SUBBATCH` (chunk texts per request).
- **Cron must run as the app user**: `docker exec -u hermes hermes /bin/sh
  /opt/data/bin/wiki_sweep_cron.sh`. A cron line without `-u` silently
  recreates root-owned files and breaks writes.

Other notes:

- The installer prints the ownership fix you must run on the host:
  `chown -R 10000:10000 /opt/hermes-data`
- Small boxes: skip the dashboard, prefer a quantized embedding model on CPU.
- `--target` scopes EVERYTHING (code and data dir) under that path.

### Common options

| Flag | Meaning |
|---|---|
| `--profile desktop\|vps` | deployment profile (required) |
| `--target PATH` | override Hermes home (default depends on profile) |
| `--with-embed-server` | download llama.cpp + embedding model |
| `--embed-url URL` | use an existing embeddings endpoint |
| `--embed-model M` | model name at that endpoint |
| `--chat-url URL` | OpenAI-compatible chat/completions endpoint for extraction |
| `--chat-model M` | chat model name |
| `--chat-key KEY` | API key (stored gitignored, never committed) |

---

## Extraction backend — required for indexing

Embeddings power *search*. **Indexing** additionally needs a chat LLM that
reads raw sessions and writes knowledge pages. Provide it during install:

```bash
# free NVIDIA cloud key (build.nvidia.com):
export NVIDIA_API_KEY=nvapi-...        # or pass --chat-key

# or any local/OpenAI-compatible server:
python tools/install.py --profile desktop \
    --chat-url http://127.0.0.1:1234/v1/chat/completions \
    --chat-model gpt-oss-20b --chat-key KEY
```

If neither is given, the installer prints a prominent warning and continues:
search works immediately (keyword-only), indexing starts working the moment a
chat backend is configured. `tools/doctor.py` reports this state as WARN.

---

## What the installer does automatically

1. **Backs up** any previous installation (`*.bak.<timestamp>` next to the
   original, two newest kept).
2. **Copies code**: core package → `<target>/scripts/wiki_v2`, entry-point
   wrappers → `<target>/scripts/`, Hermes plugins → `<target>/plugins/`,
   desktop dashboard button → `<target>/desktop-plugins/` (desktop only).
3. **Detects a legacy wiki v2 installation** and migrates it out of the way
   (see next section).
4. **Resolves configuration**: profile defaults + your flags are written to
   `profiles/<profile>.env` (gitignored — keys never reach git).
5. **VPS only**: generates `<target>/wiki.env` + the cron wrapper
   `<target>/bin/wiki_sweep_cron.sh`; with `--with-embed-server` also the
   hardened systemd embed stack (see Scenario C).
6. **Prints** the final environment and a post-install checklist.

The installer never edits the agent's own `.env` or `config.yaml`.

---

## Upgrading from legacy wiki v2

Detected automatically — no flag needed. If `.index_v2.db`, `entities/` or
`.facts_*.jsonl` exist in the data dir, the installer:

1. copies the old DB to `<wiki>/backups/.index_v2.db.bak.<timestamp>`;
2. moves queue files and old pages to `<wiki>/backups/*.bak.<timestamp>`
   (old page markup is incompatible with v3 — they are kept purely as
   reference, not re-indexed);
3. removes the live DB file so v3 creates a fresh one on first run;
4. prints two manual cleanup steps: disable the v2 leftover `llm-extractor`
   plugin and the old v2 cron sweep.

Why not migrate in place: v2 vectors (NVIDIA nv-embedqa-e5-v5) and v3 vectors
(Qwen3-Embedding-0.6B) are not comparable even at equal dimension, so mixing
them silently corrupts semantic search. A fresh index is built automatically
from your sessions by the sweep.

If a file is locked (server running), the step is skipped with a warning —
nothing is lost; re-run the installer after stopping the server.

---

## Post-install checklist

```bash
# 1. plugins
hermes plugins enable wiki-context            # injects relevant pages each turn
hermes plugins enable wiki-session-finalize   # indexes a session right after it closes

# 2. background sweep (safety net, every ~3h) — schedule in cron:
#    desktop: python <target>/scripts/wiki_v3_sweep_loader.py
#    vps:     docker exec -u hermes hermes python /opt/data/scripts/wiki_v3_sweep_loader.py

# 3. dashboard (desktop only): http://127.0.0.1:9120
<target>/scripts/wiki_dashboard_serve.py

# 4. verify
python tools/doctor.py
```

Environment variables from `profiles/<profile>.env` must be visible to every
launcher (cron AND plugin child processes). On the desktop profile they can be
set once per user session; on VPS inject them in the cron command line.

---

## Verification with doctor

```bash
python tools/doctor.py                  # config + endpoints checks
python tools/doctor.py --search "test"  # + end-to-end search query
```

Checks performed: package import → data dir writable → index DB present →
no legacy v2 artifacts → endpoints.yaml loads → embeddings endpoint answers a
real request → extraction key/endpoint presence. Exit code is 1 only on
FAIL-level problems; WARN means degraded-but-working.

---

## Updating an existing installation

The installer is idempotent — just re-run it after `git pull`:

```bash
git pull
python tools/install.py --profile desktop        # same flags as before
```

Previous code is backed up automatically; data is never touched (except the
one-time v2 migration above).

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| doctor: `embed endpoint HTTP 410/404` | wrong backend selected — set the right one via `--embed-url` / profile env (`WIKI_EMBED_BACKEND`) |
| doctor: `pyyaml missing` (FAIL) | endpoints.yaml is being silently ignored — `pip install pyyaml`, then re-check |
| embed batches fail with 502 storms / read timeouts | llama-server OOM-killed inside its cgroup — use the hardened unit flags from `deploy/vps/` (`-ub 128 -np 1`, MemoryMax) and keep `WIKI_EMBED_SUBBATCH` at 8 or lower; swap limits do not work on Ubuntu kernels |
| long sessions stop extracting tags mid-way (`бюджет LLM-вызовов исчерпан`) | per-session LLM budget hit — raise `WIKI_EXTRACT_MAX_LLM_CALLS` (default 6; on free-tier NVIDIA NIM >12 risks 429-blocking) |
| doctor: `no NVIDIA_API_KEY` WARN | indexing disabled until you add a chat backend (see [Extraction](#extraction-backend--required-for-indexing)) |
| search returns keyword-only results | embeddings endpoint down or empty index — check embed server, run the sweep |
| first indexer run crashes: `No such file or directory: ...entities\<slug>.md` | stale pages table pointing at deleted files — ensure ALL six tables were cleared (the v2 auto-migration handles this) |
| bot/files owned by root (VPS) | cron used no `-u`: fix with `chown -R 10000:10000` and always `docker exec -u hermes ...` |
| `unlink fails: file locked` during v2 migration | stop the running wiki server/dashboard, re-run the installer |
