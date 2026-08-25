# Changelog

## v3 (2026-08) — this branch

- Rebuilt retrieval: multi-vector channels (title/summary/tag/chunk) + BM25
  keyword channel, fused with RRF; cheap fail-open relevance gate.
- Local-first embeddings: llama.cpp CPU server (quantized Qwen3-Embedding-0.6B)
  or LM Studio; backend switch via env injection only.
- Chunk-level embeddings; strict terminology: session = transcript,
  page = compressed .md, chunk = embedded text slice.
- Observability dashboard (:9120): extraction status/errors, indexing progress,
  cache hits/misses; dead metrics from v2 removed after audit.
- Graceful stop between sessions (stop-flag, never kill mid-page).
- Atomic lease guard for the embed watchdog (Windows-safe).
- Deployment profiles for Windows desktop and Linux VPS (Docker).

## v2 (archived on the `v2-archive` branch)

Single-vector NVIDIA-backed memory with facts bridge. Kept for history;
algorithmic improvements were ported forward rather than copied wholesale
(older installs contained hardcoded paths).
