# nvidia_client.py
"""NVIDIA API client: chat completions + embeddings. Shared by indexer/search.

Reads the API key from ``NVIDIA_API_KEY`` (env) or from ``NVIDIA_ENV_FILE``
(a .env file), never from a hardcoded path. Endpoints and default models are
module constants and can be overridden via env (``NVIDIA_API_URL``,
``NVIDIA_EMBED_URL``, ``NVIDIA_CHAT_MODEL``, ``NVIDIA_EMBED_MODEL``).
"""
import os
import time

import requests

API_URL = os.environ.get("NVIDIA_API_URL", "https://integrate.api.nvidia.com/v1/chat/completions")
EMBED_URL = os.environ.get("NVIDIA_EMBED_URL", "https://integrate.api.nvidia.com/v1/embeddings")
DEFAULT_CHAT_MODEL = os.environ.get("NVIDIA_CHAT_MODEL", "nvidia/nemotron-3-super-120b-a12b")
DEFAULT_EMBED_MODEL = os.environ.get("NVIDIA_EMBED_MODEL", "nvidia/nv-embedqa-e5-v5")


def _default_env_file() -> str:
    return os.environ.get("NVIDIA_ENV_FILE", os.environ.get("HERMES_HOME", "")) + os.sep + ".env"


def load_api_key(env_file: str | None = None) -> str:
    """Return the NVIDIA API key. Priority: env var, then .env file."""
    key = os.environ.get("NVIDIA_API_KEY", "")
    if key:
        return key
    env_file = env_file or _default_env_file()
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("NVIDIA_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _headers():
    return {
        "Authorization": f"Bearer {load_api_key()}",
        "Content-Type": "application/json",
    }


def chat_completion(system: str, user: str, model: str = DEFAULT_CHAT_MODEL,
                    max_tokens: int = 2000, temperature: float = 0.3,
                    max_retries: int = 2, timeout: int = 120):
    """Return assistant content string, or None after exhausting retries."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(API_URL, headers=_headers(), json=payload, timeout=timeout)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"[WARN] chat attempt {attempt+1}/{max_retries+1}: {e}")
            if attempt < max_retries:
                time.sleep(5)
    return None


def embed(texts, model: str = DEFAULT_EMBED_MODEL, input_type: str = "query",
          max_retries: int = 2, timeout: int = 60):
    """Return list of embedding vectors (list[float]) for texts, or None on failure.

    input_type: 'query' for search queries, 'passage' for documents.
    """
    if isinstance(texts, str):
        texts = [texts]
    payload = {"model": model, "input": texts, "input_type": input_type,
               "encoding_format": "float", "truncate": "NONE"}
    for attempt in range(max_retries + 1):
        try:
            resp = requests.post(EMBED_URL, headers=_headers(), json=payload, timeout=timeout)
            resp.raise_for_status()
            data = resp.json()["data"]
            data.sort(key=lambda d: d["index"])
            return [d["embedding"] for d in data]
        except Exception as e:
            print(f"[WARN] embed attempt {attempt+1}/{max_retries+1}: {e}")
            if attempt < max_retries:
                time.sleep(3)
    return None
