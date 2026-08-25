#!/usr/bin/env python3
"""Wake-on-request proxy for llama.cpp embed server (port 11436 -> 11435).

If the embed service is unloaded (auto-unload watchdog), requests to this
proxy wake the backend and wait for readiness, so clients never see raw
connection errors. Only POST /v1/embeddings-style payloads are forwarded.

Production lessons baked in (2026-08-25, see deploy/vps/README.md):
- touch the activity log on every completed request so an idle-unload
  watchdog does not kill the backend mid-burst;
- retry on HTTP 503 while the model loads after a cold wake;
- retry on ConnectionRefused/timeout during the backend restart window
  instead of answering an instant 502 to the client;
- swallow BrokenPipeError when the client gave up first (no journal noise).

Configuration via environment (defaults match the reference VPS setup):
    EMBED_PROXY_TARGET     upstream URL        (default http://127.0.0.1:11435)
    EMBED_PROXY_ACTIVITY   activity log path   (default /opt/llama/llama.log)
    EMBED_PROXY_BIND       extra bind address  (default 172.20.0.1, docker bridge GW)
"""
import http.server
import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

TARGET = os.environ.get("EMBED_PROXY_TARGET", "http://127.0.0.1:11435")
BACKEND_PORT = int(TARGET.rsplit(":", 1)[1].split("/")[0]) if ":" in TARGET else 11435
ACTIVITY_LOG = os.environ.get("EMBED_PROXY_ACTIVITY", "/opt/llama/llama.log")
EXTRA_BIND = os.environ.get("EMBED_PROXY_BIND", "172.20.0.1")

BIND = [("127.0.0.1", 11436)]
if EXTRA_BIND:
    BIND.append((EXTRA_BIND, 11436))

# Retry windows: 503 (model loading) every 5s; network errors during a
# backend restart (OOM kill / watchdog unload) every 3s.
MAX_503_RETRIES = 12
MAX_CONN_RETRIES = 15


def backend_ready(timeout=60):
    """True once the upstream port accepts TCP connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", BACKEND_PORT), timeout=2)
            s.close()
            return True
        except OSError:
            time.sleep(1)
    return False


def ensure_backend():
    """Start the systemd unit if inactive, then wait until it listens."""
    r = subprocess.run(["systemctl", "is-active", "llama-embed"], capture_output=True, text=True)
    if r.stdout.strip() != "active":
        print("[proxy] waking llama-embed...")
        subprocess.run(["systemctl", "start", "llama-embed"], capture_output=True)
        if not backend_ready(90):
            raise RuntimeError("llama-embed did not come up within 90s")
        time.sleep(1)


def touch_activity():
    try:
        with open(ACTIVITY_LOG, "a"):
            os.utime(ACTIVITY_LOG, None)
    except OSError:
        pass


class Handler(http.server.BaseHTTPRequestHandler):
    def _forward(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        req = urllib.request.Request(TARGET + self.path, data=body,
                                     headers={"Content-Type": "application/json"})
        last = None
        conn_retries = 0
        for _ in range(MAX_503_RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code != 503:
                    raise
                last = e
                time.sleep(5)
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                # Backend restarting (OOM kill / watchdog): hold the request
                # until it is back instead of failing the client instantly.
                if conn_retries >= MAX_CONN_RETRIES:
                    raise
                conn_retries += 1
                time.sleep(3)
        if last is not None:
            raise last

    def do_POST(self):
        try:
            ensure_backend()
            data = self._forward()
            touch_activity()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except BrokenPipeError:
            pass  # client timed out on its side; response is no longer needed
        except Exception as e:
            try:
                self.send_response(502)
                self.send_header("Content-Type", "application/json")
                body = json.dumps({"error": str(e)}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionError):
                pass

    def log_message(self, *args):
        pass  # keep journals quiet


def serve(addr):
    server = http.server.ThreadingHTTPServer(addr, Handler)
    print(f"[proxy] listening {addr[0]}:{addr[1]} -> {TARGET}")
    server.serve_forever()


if __name__ == "__main__":
    threads = [threading.Thread(target=serve, args=(a,), daemon=True) for a in BIND]
    for t in threads:
        t.start()
    threads[0].join()
