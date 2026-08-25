"""Wiki Memory v3 — dashboard HTTP server and routing."""
from __future__ import annotations

import os
import sys

from .dashboard_analysis import _ts_charts
from .dashboard_data import _build_api_status as _build_api_status_full
from .dashboard_page import render_dashboard
from .dashboard_sections import _last_inject, _safe_json
from .logging_setup import logger


def serve(host: str = "127.0.0.1", port: int | None = None) -> None:
    """Run the dashboard as a threaded HTTP server."""
    port = int(os.environ.get("DASHBOARD_PORT", port or 9120))
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/api/status"):
                body = _safe_json(_build_api_status_full()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/api/health"):
                from .dashboard_health import health_snapshot
                body = _safe_json(health_snapshot()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/api/control"):
                from .dashboard_control import extraction_status, progress
                body = _safe_json({"status": extraction_status(), "progress": progress()}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/api/config"):
                from .dashboard_control import api_config_get
                body = _safe_json(api_config_get()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/api/charts"):
                rng = "1w"
                try:
                    from urllib.parse import parse_qs, urlsplit
                    qs = parse_qs(urlsplit(self.path).query)
                    if qs.get("range") and qs["range"][0] in ("1w", "3d", "1d", "1h"):
                        rng = qs["range"][0]
                except Exception:
                    pass
                charts = _ts_charts(rng)
                body = _safe_json(charts).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/api/injects"):
                inj = _last_inject()
                body = _safe_json(inj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            elif self.path.startswith("/api/memory-search"):
                from urllib.parse import parse_qs, urlsplit

                from .dashboard_memory import memory_preview
                try:
                    qs = parse_qs(urlsplit(self.path).query)
                    q = (qs.get("q") or [""])[0]
                except Exception:
                    q = ""
                body = _safe_json(memory_preview(q)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            else:
                rng = "1w"
                try:
                    from urllib.parse import parse_qs, urlsplit
                    qs = parse_qs(urlsplit(self.path).query)
                    if qs.get("range") and qs["range"][0] in ("1w", "3d", "1d", "1h"):
                        rng = qs["range"][0]
                except Exception:
                    pass
                body = render_dashboard(range_=rng).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            import json as _json

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                data = _json.loads(body.decode("utf-8") or "{}")
            except Exception:
                data = {}
            if self.path.startswith("/api/control"):
                action = data.get("action")
                from .dashboard_control import start_extraction, stop_extraction
                if action == "start":
                    result = start_extraction(
                        mode=data.get("mode", "normal"),
                        limit=data.get("limit"),
                    )
                elif action == "stop":
                    result = stop_extraction()
                else:
                    result = {"ok": False, "error": "unknown action"}
                resp = _json.dumps(result, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            elif self.path.startswith("/api/config"):
                from .dashboard_control import api_config_set
                result = api_config_set(data)
                resp = _json.dumps(result, ensure_ascii=False).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)
            else:
                resp = _json.dumps({"error": "not found"}, ensure_ascii=False).encode()
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        def log_message(self, *a):  # type: ignore[override]
            pass

    try:
        import socket as _sock
        _probe = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        _probe.settimeout(1.0)
        try:
            _probe.connect((host, port))
            logger.warning("serve: port %s:%d already in use — exiting (another server running)", host, port)
            _probe.close()
            sys.exit(0)
        except OSError:
            pass
        finally:
            _probe.close()
    except Exception:
        pass

    try:
        server = ThreadingHTTPServer((host, port), _Handler)
        logger.info("Dashboard HTTP server listening on %s:%d", host, port)
        server.serve_forever()
    except OSError as exc:
        logger.error("serve port %s busy: %s", port, exc)
