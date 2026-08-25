"""Wiki Memory v3 — dashboard CLI entry point."""
from __future__ import annotations

import sys
from pathlib import Path

from . import config
from .dashboard_http import serve
from .dashboard_page import render_dashboard
from .logging_setup import logger


def main() -> None:
    """Write wiki_dashboard.html and open it in a browser."""
    try:
        wiki_dir = config.WIKI_PATH
    except Exception:
        wiki_dir = Path(__file__).resolve().parent.parent / "wiki"

    output_path = wiki_dir / "wiki_dashboard.html"

    try:
        html = render_dashboard()
    except Exception as exc:
        logger.error("render_dashboard failed: %s", exc)
        html = render_dashboard()

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(html, encoding="utf-8")
        logger.info("Dashboard written to %s", output_path)
    except Exception as exc:
        logger.error("Failed to write dashboard: %s", exc)
        print(f"Error writing dashboard: {exc}", file=sys.stderr)
        return

    try:
        import webbrowser
        url = output_path.as_uri()
        webbrowser.open(url)
    except Exception as exc:
        logger.debug("Could not open browser: %s", exc)
        print(f"Dashboard saved to: {output_path}")


if __name__ == "__main__":
    if "--serve" in sys.argv:
        serve()
    else:
        main()
