# Security

## Secrets handling

- **Never store secrets in the repo.** API keys, tokens, passwords, IPs must
  not appear in code or committed config.
- The NVIDIA key is read from `NVIDIA_API_KEY` (env) or from a `.env` file
  (`NVIDIA_ENV_FILE`, default `<HERMES_HOME>/.env`). `.env` is gitignored.
- `.env.example` is the only committed config template — it contains placeholders,
  no real values.

## Boundaries

| Asset | Rule |
|-------|------|
| `.env` | Never committed |
| `*.db`, `*.jsonl`, `wiki/` | Runtime data — never committed (gitignored) |
| API keys | Env only, never in code |
| Paths | Env-driven via `config.py`; no hardcoded user paths |

## Plugin trust

Plugins run inside the Hermes agent process. `wiki-context` (reads wiki) and
`wiki-session-finalize` (spawns a subprocess indexer) are read/background-only:
- `wiki-context` returns a context string or `None` — no tool override.
- `wiki-session-finalize` launches `python -m wiki_v2.indexer --session <id>`
  with `close_fds`; it does not modify agent tools or data.

## Supply chain

Dependencies are minimal and pinned with upper bounds (`numpy`, `requests`) —
see `pyproject.toml`. Install from PyPI only.

## Reporting

If you find a hardcoded secret or path in this repo, open an issue. The
`SECURITY.md` and the `AGENTS.md` invariant #1 exist to keep this from
happening; automated scans are recommended before release.
