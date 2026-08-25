#!/usr/bin/env python3
"""Auto-unload the llama.cpp embed service after IDLE_MINUTES of downtime.

Production lessons baked in (2026-08-25, see deploy/vps/README.md):
- while a wiki indexer process is alive, the model is NEVER unloaded:
  LLM extraction phases pause embed traffic longer than the idle threshold,
  and unloading mid-run turned every following batch into a cold wake
  (502/timeout retry storm on the client);
- activity is measured via mtime of the proxy's activity log — llama-server
  itself logs to journald, so a bare log file is the cheap shared signal.

Configuration via environment (defaults match the reference VPS setup):
    LLAMA_IDLE_MINUTES     idle threshold in minutes (default 10)
    LLAMA_ACTIVITY_LOG     activity log path          (default /opt/llama/llama.log)
    LLAMA_EMBED_UNIT       systemd unit name          (default llama-embed)
"""
import os
import subprocess
import time

IDLE_MINUTES = int(os.environ.get("LLAMA_IDLE_MINUTES", "10"))
ACTIVITY_LOG = os.environ.get("LLAMA_ACTIVITY_LOG", "/opt/llama/llama.log")
UNIT = os.environ.get("LLAMA_EMBED_UNIT", "llama-embed")
CHECK_INTERVAL = 60  # seconds


def indexer_running():
    """True while a wiki v3 indexer process is alive (embed bursts incoming)."""
    try:
        r = subprocess.run(["pgrep", "-f", "wiki_v2.indexer"],
                           capture_output=True, text=True)
        return r.returncode == 0
    except Exception:
        return False


while True:
    try:
        r = subprocess.run(["systemctl", "is-active", UNIT],
                           capture_output=True, text=True)
        if r.stdout.strip() == "active":
            if indexer_running():
                # indexer mid-run — keep the model loaded
                time.sleep(CHECK_INTERVAL)
                continue
            try:
                age = time.time() - os.path.getmtime(ACTIVITY_LOG)
                if age > IDLE_MINUTES * 60:
                    print(f"[watchdog] no requests for {IDLE_MINUTES} min, stopping {UNIT}")
                    subprocess.run(["systemctl", "stop", UNIT], capture_output=True)
            except FileNotFoundError:
                pass
    except Exception as e:
        print(f"[watchdog] error: {e}")
    time.sleep(CHECK_INTERVAL)
