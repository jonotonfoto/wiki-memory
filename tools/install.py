#!/usr/bin/env python3
"""Wiki Memory v3 installer / updater.

Usage:
    python tools/install.py --profile desktop
    python tools/install.py --profile vps --target /opt/hermes-data

Idempotent: re-running performs an update. Existing directories are backed up
as siblings with a ``.bak.<timestamp>`` suffix (max 2 kept per directory).

Stdlib only — safe to run before dependencies are installed.
"""
import argparse
import os
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_TARGETS = {
    "desktop": Path(os.environ.get("LOCALAPPDATA", "")) / "hermes",
    "vps": Path("/opt/hermes-data"),
}

BACKUP_KEEP = 2


def load_profile(profile: str) -> dict:
    """Read profiles/<profile>.env (user) or the .example template."""
    base = REPO_ROOT / "profiles"
    path = base / f"{profile}.env"
    used_example = False
    if not path.exists():
        path = base / f"{profile}.env.example"
        used_example = True
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        env[key.strip()] = val.strip()
    return env, used_example


def backup_dir(target: Path) -> str | None:
    """Snapshot an existing directory next to itself, prune old backups."""
    if not target.exists():
        return None
    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = target.with_name(target.name + f".bak.{ts}")
    shutil.copytree(target, bak)
    stem = target.name + ".bak."
    siblings = sorted(
        (p for p in target.parent.glob(target.name + ".bak.*") if p.is_dir()),
        key=lambda p: p.name,
    )
    for old in siblings[:-BACKUP_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
    return str(bak)


def copy_tree(src: Path, dst: Path):
    if not src.exists():
        print(f"  [WARN] missing in repo, skipped: {src.relative_to(REPO_ROOT)}")
        return
    shutil.copytree(src, dst, dirs_exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", required=True, choices=["desktop", "vps"])
    ap.add_argument("--target", help="override Hermes home (default depends on profile)")
    args = ap.parse_args()

    home = Path(args.target) if args.target else DEFAULT_TARGETS[args.profile]
    if not home or str(home) in ("", "."):
        print("[FAIL] could not resolve Hermes home; pass --target", file=sys.stderr)
        return 1

    scripts = home / "scripts"
    plugins = home / "plugins"

    print(f"Profile : {args.profile}")
    print(f"Target  : {home}")

    env, used_example = load_profile(args.profile)
    if used_example:
        print("[NOTE] no user profile found, using the .example template")
        print("       create profiles/%s.env with your real values" % args.profile)

    # --- code ---
    bak = backup_dir(scripts / "wiki_v2")
    if bak:
        print(f"Backup  : {bak}")
    copy_tree(REPO_ROOT / "src" / "wiki_v2", scripts / "wiki_v2")
    for wrapper in sorted((REPO_ROOT / "scripts").glob("*.py")):
        shutil.copy2(wrapper, scripts / wrapper.name)
    print(f"Copied  : core -> {scripts}/wiki_v2, wrappers -> {scripts}/")

    # --- plugins ---
    for plugin in sorted((REPO_ROOT / "plugins").iterdir()):
        if plugin.is_dir():
            bak = backup_dir(plugins / plugin.name)
            if bak:
                print(f"Backup  : {bak}")
            copy_tree(plugin, plugins / plugin.name)
    print(f"Copied  : plugins -> {plugins}/")

    # --- desktop UI plugin (desktop profile only) ---
    if args.profile == "desktop":
        dp = REPO_ROOT / "desktop-plugins"
        if dp.exists():
            copy_tree(dp, home / "desktop-plugins")
            print(f"Copied  : desktop-plugins -> {home / 'desktop-plugins'}/")

    # --- ownership fix for container installs ---
    if args.profile == "vps":
        uid = env.get("WIKI_VPS_UID", "10000")
        print(f"[TODO ] run on the host: chown -R {uid}:{uid} {home}")

    # --- environment ---
    print("\nEnvironment to inject into every launcher (cron AND plugins):")
    for key in ("WIKI_PATH", "WIKI_EMBED_BACKEND", "WIKI_EMBED_URL", "WIKI_EMBED_MODEL"):
        if key in env and "<" not in env[key]:
            print(f"  {key}={env[key]}")

    print(
        "\nPost-install checklist:\n"
        "  1. hermes plugins enable wiki-context\n"
        "  2. hermes plugins enable wiki-session-finalize\n"
        "  3. cron sweep: scripts/wiki_v3_sweep_loader.py every ~3h\n"
        f"  4. {'start dashboard: scripts/wiki_dashboard_serve.py (:9120)' if env.get('WIKI_DASHBOARD') != '0' else 'dashboard disabled by profile'}\n"
        "  5. sanity check: python tools/doctor.py"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
