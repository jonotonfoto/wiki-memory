# Security

## Secrets

- API keys (`NVIDIA_API_KEY`, etc.) live only in the host environment or the
  agent's own `.env`. Never in git, never in profiles committed here.
- The wiki package receives credentials through its parent process
  environment; it does not read or write agent config files itself.

## Boundaries

- Plugins are fail-open: an exception in the wiki hook logs and skips, it can
  never break the agent's reply loop.
- The dashboard binds to `127.0.0.1` by default. Exposing it remotely requires
  an explicit reverse proxy with TLS + auth (see your platform's docs).
- Search synthesis calls the configured chat LLM only on actual hits; there is
  no LLM call for query gating.

## Data

- All user knowledge stays in local files (`WIKI_PATH`) and local SQLite;
  nothing is uploaded anywhere except embedding/chat API calls you configure.
- Deleting pages via cleanup tools supports `--dry-run` by default.
