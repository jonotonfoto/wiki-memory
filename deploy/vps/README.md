# VPS embed stack (llama.cpp on a small box) — production lessons

These files package the hardening that a 1-core / 2 GB VPS needs to run the
local embedding server next to the Hermes bot without OOM storms. They were
extracted from a real incident on 2026-08-25; every flag and retry loop below
exists because something broke without it.

## Files

| File | Purpose |
|---|---|
| `llama-embed.service.template` | systemd unit with memory-hardened llama-server flags |
| `llama_embed_proxy.py` | wake-on-request proxy 11436 → 11435 with restart-window retries |
| `llama_embed_watchdog.py` | unloads the model after idle, but never mid-indexing |

## The incident, in one paragraph

`install.py --with-embed-server` used to generate a bare start script
(default llama.cpp flags). Under a real sweep, `embed_chunks` sent all chunk
embeddings of a session in ONE HTTP request; compute buffers spiked anon RSS
past `MemoryMax=800M`, the kernel OOM-killed llama-server (10 kills in 20
minutes), systemd restarted it, and the client burned its whole retry budget
on 502/read-timeouts — minutes lost per batch. Two independent killers were
found: cgroup-OOM under batch load (this directory) and an idle watchdog
unloading the model during LLM extraction pauses (fixed inside the watchdog).

## Why you cannot fix this with swap

Ubuntu kernels ship with `swapaccount=0`-equivalent behaviour: the cgroup has
no `memory.swap.*` files, so `MemorySwapMax` is silently ignored. Spikes must
be prevented (`-ub 128 -np 1`, client-side sub-batching via
`WIKI_EMBED_SUBBATCH`), not absorbed.

## Manual install

```bash
# 1. unit (see header of the template for sed placeholders)
sed -e 's|@LLAMA_SERVER@|/opt/hermes-data/wiki-embed/bin/llama-server|' \
    -e 's|@MODEL_FILE@|/opt/hermes-data/wiki-embed/model/Qwen3-Embedding-0.6B-Q8_0.gguf|' \
    -e 's|@LLAMA_DIR@|/opt/hermes-data/wiki-embed/bin|' \
    llama-embed.service.template > /etc/systemd/system/llama-embed.service

# 2. proxy + watchdog
install -m 755 llama_embed_proxy.py /usr/local/bin/
install -m 755 llama_embed_watchdog.py /usr/local/bin/

# 3. proxy + watchdog as plain always-restarted services
cat > /etc/systemd/system/llama-embed-proxy.service <<'EOF'
[Unit]
Description=wiki-memory embed wake-on-request proxy (11436 -> 11435)
After=llama-embed.service
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/llama_embed_proxy.py
Restart=always
[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/llama-embed-watchdog.service <<'EOF'
[Unit]
Description=unload wiki embed server after idle
[Service]
ExecStart=/usr/bin/python3 /usr/local/bin/llama_embed_watchdog.py
Restart=always
RestartSec=30
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now llama-embed llama-embed-proxy llama-embed-watchdog

# 4. container -> host networking: docker containers reach host ports ONLY
#    through the bridge gateway IP, not loopback:
ufw allow from 172.20.0.0/16 to any port 11436 proto tcp
```

Point the wiki at the proxy through the gateway:
`LLAMASERVER_URL=http://172.20.0.1:11436/v1/embeddings`.

## Related knobs (client side)

| Env | Default | Meaning |
|---|---|---|
| `WIKI_EMBED_SUBBATCH` | 8 | chunk texts per HTTP request in `indexer.embed_chunks` |
| `WIKI_EXTRACT_MAX_LLM_CALLS` | 6 | LLM-call budget per session run (raise on paid/unlimited chat APIs) |
