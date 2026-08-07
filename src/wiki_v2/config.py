"""Wiki Memory — cross-platform path & environment resolution.

Replaces the old hardcoded ``windows_config.py`` (Windows) and the inline
``/opt/data`` defaults baked into the Linux/VPS variant. All paths are derived
from environment variables with sensible per-OS defaults, so the same package
runs on Windows desktop, Linux server, or inside a container without edits.

Resolution order (highest priority first):
  1. explicit env var (HERMES_HOME / WIKI_PATH / HERMES_STATE_DB / WIKI_SCRIPTS)
  2. this module's defaults, which are OS-aware via ``os.name``
  3. a ``.env`` file next to the scripts (loaded into os.environ, never
     overwriting already-set keys)

Usage:
    from wiki_v2 import config
    config.configure()                 # one-shot at process start
    wiki = str(config.WIKI_PATH)       # current resolved path

For tests that change env vars, call ``config.reload()`` after setting them
to re-resolve the module-level paths.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

IS_WINDOWS = os.name == "nt"
IS_POSIX = not IS_WINDOWS


def _default_hermes_home() -> Path:
    if IS_WINDOWS:
        return Path.home() / "AppData" / "Local" / "hermes"
    return Path("/opt/data") if Path("/opt/data").is_dir() else Path.home() / ".hermes"


def _default_python() -> str:
    if IS_WINDOWS:
        return sys.executable
    candidates = [
        str(_default_hermes_home() / ".venv-wiki" / "bin" / "python"),
        "/opt/hermes/.venv/bin/python",
        "python3",
        "python",
    ]
    for c in candidates:
        if c.startswith("/") and os.path.exists(c):
            return c
    return candidates[-1]


def _resolve() -> dict:
    """Compute the current path set from the environment (fresh each call)."""
    home = Path(os.environ.get("HERMES_HOME", str(_default_hermes_home()))).resolve()
    wiki = Path(os.environ.get("WIKI_PATH", str(home / "wiki"))).resolve()
    state = Path(os.environ.get("HERMES_STATE_DB", str(home / "state.db"))).resolve()
    scripts = Path(os.environ.get(
        "WIKI_SCRIPTS", str(Path(__file__).resolve().parent.parent))).resolve()
    agent = Path(os.environ.get("HERMES_AGENT_DIR", str(home / "hermes-agent"))).resolve()
    env = Path(os.environ.get("HERMES_ENV_FILE", str(home / ".env"))).resolve()
    py = os.environ.get("WIKI_PYTHON", _default_python())
    return {
        "HERMES_HOME": home,
        "WIKI_PATH": wiki,
        "STATE_DB": state,
        "SCRIPTS_DIR": scripts,
        "HERMES_AGENT_DIR": agent,
        "ENV_FILE": env,
        "PYTHON": py,
    }


# Module-level current values (refreshed by reload()).
_PATHS = _resolve()

HERMES_HOME: Path = _PATHS["HERMES_HOME"]
WIKI_PATH: Path = _PATHS["WIKI_PATH"]
STATE_DB: Path = _PATHS["STATE_DB"]
SCRIPTS_DIR: Path = _PATHS["SCRIPTS_DIR"]
HERMES_AGENT_DIR: Path = _PATHS["HERMES_AGENT_DIR"]
ENV_FILE: Path = _PATHS["ENV_FILE"]
PYTHON: str = _PATHS["PYTHON"]


def reload() -> None:
    """Re-resolve all module-level paths from the current env.

    Call after changing env vars (e.g. in tests) so ``config.WIKI_PATH`` etc.
    reflect the new values.
    """
    global HERMES_HOME, WIKI_PATH, STATE_DB, SCRIPTS_DIR, HERMES_AGENT_DIR, ENV_FILE, PYTHON
    p = _resolve()
    HERMES_HOME = p["HERMES_HOME"]
    WIKI_PATH = p["WIKI_PATH"]
    STATE_DB = p["STATE_DB"]
    SCRIPTS_DIR = p["SCRIPTS_DIR"]
    HERMES_AGENT_DIR = p["HERMES_AGENT_DIR"]
    ENV_FILE = p["ENV_FILE"]
    PYTHON = p["PYTHON"]


def load_env_file(path: Path | str | None = None) -> None:
    p = Path(path) if path else ENV_FILE
    if not p.exists():
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


def apply() -> None:
    os.environ.setdefault("HERMES_HOME", str(HERMES_HOME))
    os.environ.setdefault("WIKI_PATH", str(WIKI_PATH))
    os.environ.setdefault("HERMES_STATE_DB", str(STATE_DB))
    os.environ.setdefault("NVIDIA_ENV_FILE", str(ENV_FILE))

    scripts = str(SCRIPTS_DIR)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    agent = str(HERMES_AGENT_DIR)
    if agent not in sys.path:
        sys.path.insert(0, agent)


def ensure_dirs() -> None:
    for sub in ("entities", "concepts", "comparisons", "queries"):
        (WIKI_PATH / sub).mkdir(parents=True, exist_ok=True)


def configure() -> None:
    load_env_file()
    reload()          # reflect any .env values
    apply()
    ensure_dirs()
