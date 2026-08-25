#!/usr/bin/env python3
"""Wiki Memory v3 installer / updater.

Usage:
    python tools/install.py --profile desktop [--with-embed-server]
    python tools/install.py --profile vps [--target /opt/hermes-data]

Embedding backend (choose one):
    --with-embed-server        download llama.cpp + Qwen3-Embedding model
                               (~640 MB) into <target>/wiki-embed and generate
                               a start script; best for a fresh machine
    --embed-url URL            use an existing OpenAI-compatible embeddings
                               endpoint instead of installing one
    (neither)                  keep the values from the chosen profile file

Extraction needs a CHAT endpoint (LLM that distills sessions into pages):
    --chat-url URL --chat-model M   OpenAI-compatible chat completions endpoint
    --chat-key KEY                  API key (stored in profiles/<profile>.env,
                                    which is gitignored)
    If none given and NVIDIA_API_KEY is not set in the environment, the
    installer prints a prominent warning but still installs (search works,
    indexing will fail until a chat backend is provided).

Idempotent: re-running performs an update; existing dirs are backed up as
siblings (*.bak.<timestamp>, two newest kept). Stdlib only.
"""
import argparse
import json
import os
import re
import shutil
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TARGETS = {
    "desktop": Path(os.environ.get("LOCALAPPDATA", "")) / "hermes",
    "vps": Path("/opt/hermes-data"),
}

BACKUP_KEEP = 2
EMBED_PORT = "11435"
EMBED_MODEL_FILE = "Qwen3-Embedding-0.6B-Q8_0.gguf"
EMBED_MODEL_URL = (
    "https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/main/"
    + EMBED_MODEL_FILE
)
LLAMA_RELEASES_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=30"
ASSET_RE = {
    "desktop": re.compile(r"llama-.+-bin-win-cpu-x64\.zip$", re.I),
    "vps": re.compile(r"llama-.+-bin-ubuntu-x64\.tar\.gz$", re.I),
}


def http_get(url: str, timeout: int = 30):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "wiki-memory-install"}),
        timeout=timeout,
    )


def download(url: str, dst: Path):
    print(f"Download {url.split('/')[-1]} ...")
    tmp = dst.with_suffix(dst.suffix + ".part")
    with http_get(url, timeout=120) as resp, open(tmp, "wb") as f:
        done = 0
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            print(f"  {done // (1 << 20)} MB", end="\r", flush=True)
    print()
    tmp.replace(dst)


def find_llama_asset(profile: str) -> tuple[str, str]:
    """Return (download_url, asset_name) from the newest release carrying it."""
    releases = json.load(http_get(LLAMA_RELEASES_API))
    pat = ASSET_RE[profile]
    for rel in releases:
        for asset in rel.get("assets", []):
            if pat.search(asset["name"]):
                return asset["browser_download_url"], asset["name"]
    raise RuntimeError("no matching llama.cpp release asset found")


def ensure_embed_server(home: Path, profile: str) -> dict:
    root = home / "wiki-embed"
    bindir, modeldir = root / "bin", root / "model"
    modeldir.mkdir(parents=True, exist_ok=True)

    exe_dir = bindir
    if not any(bindir.glob("**/llama-server*")):
        url, name = find_llama_asset(profile)
        archive = bindir / name
        bindir.mkdir(parents=True, exist_ok=True)
        download(url, archive)
        print("Extracting llama.cpp ...")
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(bindir)
        else:
            import tarfile

            with tarfile.open(archive) as t:
                t.extractall(bindir)
        archive.unlink()
        matches = [p for p in bindir.rglob("llama-server*") if p.suffix in (".exe", "") or p.is_file()]
        exe_dir = matches[0].parent if matches else bindir

    model_file = modeldir / EMBED_MODEL_FILE
    if not model_file.exists():
        download(EMBED_MODEL_URL, model_file)

    # start script
    if profile == "desktop":
        runner, server_exe = root / "start_wiki_embed.bat", exe_dir / "llama-server.exe"
        script = (
            f"@echo off\r\n"
            f'"{server_exe}" -m "{model_file}" --port {EMBED_PORT} '
            f"--embedding --pooling last --host 127.0.0.1\r\n"
        )
    else:
        runner, server_exe = root / "start_wiki_embed.sh", exe_dir / "llama-server"
        script = (
            f"#!/bin/sh\n"
            f'"{server_exe}" -m "{model_file}" --port {EMBED_PORT} '
            f"--embedding --pooling last --host 127.0.0.1\n"
        )
    runner.write_text(script, encoding="utf-8")
    if profile == "vps":
        os.chmod(runner, 0o755)

    return {
        "WIKI_EMBED_BACKEND": "llamaserver",
        "LLAMASERVER_URL": f"http://127.0.0.1:{EMBED_PORT}/v1/embeddings",
        "LLAMASERVER_MODEL": EMBED_MODEL_FILE.removesuffix(".gguf"),
        "_runner": str(runner),
    }


def load_profile_env(profile: str) -> dict:
    base = REPO_ROOT / "profiles"
    path = base / f"{profile}.env"
    if not path.exists():
        path = base / f"{profile}.env.example"
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip()
    return env


def save_profile_env(profile: str, env: dict):
    path = REPO_ROOT / "profiles" / f"{profile}.env"
    lines = [f"# generated by tools/install.py on {time.strftime('%Y-%m-%d %H:%M')}\n"]
    lines += [f"{k}={v}\n" for k, v in env.items() if not k.startswith("_")]
    path.write_text("".join(lines), encoding="utf-8")
    print(f"Profile saved: {path.relative_to(REPO_ROOT)} (gitignored)")


def backup_dir(target: Path):
    if not target.exists():
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = target.with_name(target.name + f".bak.{ts}")
    shutil.copytree(target, bak)
    siblings = sorted((p for p in target.parent.glob(target.name + ".bak.*") if p.is_dir()), key=lambda p: p.name)
    for old in siblings[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return str(bak)


def copy_tree(src: Path, dst: Path):
    if not src.exists():
        print(f"  [WARN] missing in repo, skipped: {src.relative_to(REPO_ROOT)}")
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", required=True, choices=["desktop", "vps"])
    ap.add_argument("--target", help="override Hermes home")
    embed = ap.add_mutually_exclusive_group()
    embed.add_argument("--with-embed-server", action="store_true",
                       help="download & configure a local llama.cpp embedding server")
    embed.add_argument("--embed-url", help="use existing embeddings endpoint URL")
    ap.add_argument("--embed-model", help="model name at --embed-url")
    ap.add_argument("--chat-url", help="OpenAI-compatible chat/completions URL for extraction")
    ap.add_argument("--chat-model", help="chat model name for extraction")
    ap.add_argument("--chat-key", help="API key for extraction (stored gitignored)")
    args = ap.parse_args()

    home = Path(args.target) if args.target else DEFAULT_TARGETS[args.profile]
    scripts, plugins = home / "scripts", home / "plugins"
    print(f"Profile : {args.profile}\nTarget  : {home}")

    env = {k: v for k, v in load_profile_env(args.profile).items() if "<" not in v}

    # --- code ---
    bak = backup_dir(scripts / "wiki_v2")
    if bak:
        print(f"Backup  : {bak}")
    copy_tree(REPO_ROOT / "src" / "wiki_v2", scripts / "wiki_v2")
    for wrapper in sorted((REPO_ROOT / "scripts").glob("*.py")):
        shutil.copy2(wrapper, scripts / wrapper.name)
    print(f"Copied  : core -> {scripts}/wiki_v2, wrappers -> {scripts}/")

    for plugin in sorted((REPO_ROOT / "plugins").iterdir()):
        if plugin.is_dir():
            copy_tree(plugin, plugins / plugin.name)
    print(f"Copied  : plugins -> {plugins}/")
    if args.profile == "desktop" and (REPO_ROOT / "desktop-plugins").exists():
        copy_tree(REPO_ROOT / "desktop-plugins", home / "desktop-plugins")

    # --- embedding backend ---
    if args.with_embed_server:
        print("Embed server: ensuring llama.cpp + model ...")
        env.update(ensure_embed_server(home, args.profile))
    elif args.embed_url:
        env["WIKI_EMBED_BACKEND"] = "llamaserver"  # generic OpenAI-compatible slot
        env["LLAMASERVER_URL"] = args.embed_url
        if args.embed_model:
            env["LLAMASERVER_MODEL"] = args.embed_model

    # --- extraction (chat) ---
    has_key = bool(args.chat_key or os.environ.get("NVIDIA_API_KEY"))
    if args.chat_url:
        env["NVIDIA_API_URL"] = args.chat_url
    if args.chat_model:
        env["NVIDIA_CHAT_MODEL"] = args.chat_model
    if args.chat_key:
        env["NVIDIA_API_KEY"] = args.chat_key

    save_profile_env(args.profile, env)

    if not has_key and "NVIDIA_API_URL" not in env:
        print(
            "\n[WARN] EXTRACTION NEEDS A CHAT LLM ENDPOINT.\n"
            "  Indexing will NOT work until you provide one:\n"
            "    a) free NVIDIA key: https://build.nvidia.com -> export NVIDIA_API_KEY=nvapi-...\n"
            "    b) local/OpenAI-compatible: re-run with\n"
            "       --chat-url http://127.0.0.1:1234/v1/chat/completions --chat-model <model>\n"
            "  Search still works without it (keyword-only degradation)."
        )

    if args.profile == "vps":
        uid = env.get("WIKI_VPS_UID", "10000")
        print(f"\n[TODO ] host: chown -R {uid}:{uid} {home}")

    print("\nEnvironment injected via profiles/*.env (source before cron/plugins):")
    for k in ("WIKI_PATH", "WIKI_EMBED_BACKEND", "LLAMASERVER_URL", "NVIDIA_API_URL"):
        if k in env:
            print(f"  {k}={env[k]}")
    if "_runner" in env:
        print(f"\nStart embed server: {env['_runner']}")
    print("\nNext: hermes plugins enable wiki-context wiki-session-finalize;"
          " cron sweep ~3h; then python tools/doctor.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
