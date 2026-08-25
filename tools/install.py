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
EMBED_PORT = "11436"          # wake-on-request proxy port (backend on 11435)
EMBED_BACKEND_PORT = "11435"
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

# Memory-hardened llama-server flags for small VPS boxes. Do not remove:
# with the defaults, one batch of 8+ chunk embeddings OOM-kills llama-server
# inside MemoryMax=800M (see deploy/vps/README.md). -ub 128 shrinks compute
# buffers ~4x, -np 1 drops unused slots; embed clients are sequential.
VPS_LLAMA_FLAGS = "--threads 1 -c 512 -b 512 -ub 128 -np 1"

REQUIRED_IMPORTS = ("numpy", "requests", "yaml")  # yaml = PyYAML


def check_deps() -> bool:
    """Warn loudly about missing runtime deps.

    PyYAML is the sneaky one: without it endpoints.yaml is silently ignored
    and the code falls back to built-in DEFAULTS (wrong backend/url) with no
    error anywhere — indexing then talks to a dead endpoint.
    """
    missing = []
    for mod in REQUIRED_IMPORTS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(
            "\n[WARN] missing Python packages: " + ", ".join(missing) + "\n"
            "       pip install " + " ".join(missing) + "\n"
            "       Without pyyaml, endpoints.yaml is SILENTLY ignored and\n"
            "       the wrong default backend/url is used."
        )
        return False
    return True


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
            f'"{server_exe}" -m "{model_file}" --port {EMBED_BACKEND_PORT} '
            f"--embedding --pooling last --host 127.0.0.1\r\n"
        )
    else:
        runner, server_exe = root / "start_wiki_embed.sh", exe_dir / "llama-server"
        script = (
            f"#!/bin/sh\n"
            f'"{server_exe}" -m "{model_file}" --port {EMBED_BACKEND_PORT} '
            f"--embedding {VPS_LLAMA_FLAGS} --pooling last --host 127.0.0.1\n"
        )
    runner.write_text(script, encoding="utf-8")
    if profile == "vps":
        os.chmod(runner, 0o755)
        write_vps_embed_stack(root, exe_dir, model_file)
        # vps goes through the wake-on-request proxy (11436); desktop talks
        # to llama-server directly.
        url = f"http://127.0.0.1:{EMBED_PORT}/v1/embeddings"
    else:
        url = f"http://127.0.0.1:{EMBED_BACKEND_PORT}/v1/embeddings"

    return {
        "WIKI_EMBED_BACKEND": "llamaserver",
        "LLAMASERVER_URL": url,
        "LLAMASERVER_MODEL": EMBED_MODEL_FILE.removesuffix(".gguf"),
        "_runner": str(runner),
    }


def write_vps_embed_stack(root: Path, exe_dir: Path, model_file: Path):
    """Emit the hardened systemd stack for a small VPS (see deploy/vps/README.md).

    Generated into <root>/systemd/: rendered llama-embed unit (memory-hardened
    flags + MemoryMax=800M), wake-on-request proxy, idle-unload watchdog.
    The proxy listens on 11436 so containers reach it via the docker bridge
    gateway IP instead of host loopback.
    """
    src = REPO_ROOT / "deploy" / "vps"
    out = root / "systemd"
    out.mkdir(parents=True, exist_ok=True)

    unit = (src / "llama-embed.service.template").read_text(encoding="utf-8")
    unit = (unit.replace("@LLAMA_SERVER@", str(exe_dir / "llama-server"))
                .replace("@MODEL_FILE@", str(model_file))
                .replace("@LLAMA_DIR@", str(exe_dir)))
    (out / "llama-embed.service").write_text(unit, encoding="utf-8")

    shutil.copy2(src / "llama_embed_proxy.py", out / "llama_embed_proxy.py")
    shutil.copy2(src / "llama_embed_watchdog.py", out / "llama_embed_watchdog.py")
    shutil.copy2(src / "README.md", out / "README.md")

    print(
        "\nEmbed stack for systemd generated -> "
        f"{out} (see README.md there)\n"
        "  cp  " + str(out / "llama-embed.service") + " /etc/systemd/system/\n"
        "  install -m 755 " + str(out / "llama_embed_proxy.py") + " /usr/local/bin/\n"
        "  install -m 755 " + str(out / "llama_embed_watchdog.py") + " /usr/local/bin/\n"
        "  then enable llama-embed + proxy + watchdog services and:\n"
        "  ufw allow from 172.20.0.0/16 to any port " + EMBED_PORT + " proto tcp"
    )


def write_vps_runtime(home: Path, env: dict):
    """Generate <home>/wiki.env + cron wrapper sourcing it.

    Cron inside Docker cannot see profiles/*.env from the repo checkout, and
    a cron line without the chat API key silently indexes nothing. The wrapper
    also sources the agent's own /opt/data/.env when present (keys live there,
    never in git).
    """
    env_path = home / "wiki.env"
    lines = [f"# generated by tools/install.py on {time.strftime('%Y-%m-%d %H:%M')}\n"]
    lines += [f"{k}={v}\n" for k, v in sorted(env.items())]
    env_path.write_text("".join(lines), encoding="utf-8")

    bin_dir = home / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    wrapper = bin_dir / "wiki_sweep_cron.sh"
    home_sh = str(home)
    wrapper.write_text(
        "#!/bin/sh\n"
        "# wiki v3 sweep cron entry: env first, then the loader.\n"
        "set -a\n"
        f'. "{home_sh}/wiki.env" 2>/dev/null\n'
        "# agent env (chat API keys) — best effort, never stored in git:\n"
        '[ -f /opt/data/.env ] && . /opt/data/.env\n'
        "set +a\n"
        f'PY="{home_sh}/.venv-wiki/bin/python"\n'
        '[ -x "$PY" ] || PY=python3\n'
        f'exec "$PY" "{home_sh}/scripts/wiki_v3_sweep_loader.py" '
        f'>> "{home_sh}/wiki/backups/wiki_sweep.log" 2>&1\n',
        encoding="utf-8",
    )
    os.chmod(wrapper, 0o755)
    os.makedirs(home / "wiki" / "backups", exist_ok=True)
    print(f"\nCron runtime generated: {env_path} + {wrapper}")
    print("Cron line (host crontab, note -u hermes — without it files become root-owned):")
    print("  0 */3 * * * docker exec -u hermes hermes /bin/sh /opt/data/bin/wiki_sweep_cron.sh")


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


def upgrade_from_v2(wiki_dir: Path) -> bool:
    """Auto-detect legacy wiki v2 data and migrate it out of the way.

    v2 vectors and page markup are incompatible with v3, so the old state is
    preserved under <wiki>/backups/ (DB copy, queue files, pages moved aside)
    and the live DB file is removed — v3 creates a fresh one on first run.

    Returns True when v2 artifacts were found (and handled).
    """
    has_v2 = (
        (wiki_dir / ".index_v2.db").exists()
        or (wiki_dir / "entities").is_dir()
        or (wiki_dir / ".facts_pending.jsonl").exists()
    )
    if not has_v2:
        return False

    print(
        "\n[AUTO ] legacy wiki v2 detected in %s\n"
        "        v2 vectors/markup are incompatible with v3 -> backing up "
        "and starting fresh" % wiki_dir
    )

    ts = time.strftime("%Y%m%d_%H%M%S")
    bdir = wiki_dir / "backups"
    bdir.mkdir(parents=True, exist_ok=True)

    db = wiki_dir / ".index_v2.db"
    if db.exists():
        dst = bdir / f".index_v2.db.bak.{ts}"
        try:
            shutil.copy2(db, dst)
            db.unlink()
            print(f"  DB backed up -> {dst.relative_to(wiki_dir)} "
                  f"({dst.stat().st_size // 1024} KB); fresh DB will be created")
        except OSError as exc:
            print(f"  [FAIL] could not back up {db.name}: {exc!r} — leaving it in place")
            return True

    for qf in (".facts_pending.jsonl", ".facts_done.jsonl"):
        src = wiki_dir / qf
        if src.exists():
            dst = bdir / f"{qf}.bak.{ts}"
            try:
                shutil.move(str(src), str(dst))
                print(f"  queue backed up -> {dst.relative_to(wiki_dir)}")
            except OSError as exc:
                print(f"  [WARN] could not move {qf}: {exc!r}")

    entities = wiki_dir / "entities"
    if entities.is_dir():
        dst = bdir / f"entities.bak.{ts}"
        try:
            shutil.move(str(entities), str(dst))
            n = sum(1 for _ in dst.rglob("*.md"))
            print(f"  {n} old page(s) moved to {dst.relative_to(wiki_dir)} (incompatible markup)")
        except OSError as exc:
            print(f"  [WARN] could not move entities/: {exc!r} — move it manually")

    print(
        "  manual checklist:\n"
        "  - hermes plugins disable llm-extractor   (v2 leftover, duplicate load)\n"
        "  - disable/replace the old v2 cron sweep (it points at dead paths)"
    )
    return True


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

    check_deps()
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

    # --- legacy wiki v2: auto-detect, back up, start fresh ---
    # An explicit --target scopes EVERYTHING (code AND data) to that directory;
    # otherwise the data dir comes from the profile env.
    if args.target:
        wiki_dir = home / "wiki"
    else:
        wiki_dir = Path(os.path.expandvars(env.get("WIKI_PATH", str(home / "wiki"))))
    upgrade_from_v2(wiki_dir)

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

    if args.profile == "vps":
        write_vps_runtime(home, env)

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
