import shutil
from pathlib import Path
import datetime

live_dir = Path(r"%LOCALAPPDATA%\hermes\scripts\wiki_v2")
mirror_dir = Path(r"<REPO_ROOT>\scripts\wiki_v2")
tests_dir = mirror_dir / "tests"
live_tests_dir = live_dir / "tests"

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

files_to_update = [
    "dashboard_health.py",
    "dashboard_styles.py",
    "dashboard_sections.py",
    "dashboard_page.py",
    "tests/test_dashboard_health.py"
]

print("Backing up files...")
for rel in files_to_update:
    for base in [live_dir, mirror_dir]:
        src = base / rel
        if src.exists():
            bak = base / f"{rel}.bak.{ts}"
            shutil.copy2(src, bak)
            print(f"Backed up {src} -> {bak}")

print("Backup complete.")
