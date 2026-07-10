"""Desktop entry point for the QUAM State Manager.

Starts a Flask server on a random localhost port, then opens a native OS
window via pywebview. The window behaves like a desktop app -- no browser
needed.

Usage::

    python -m quam_state_manager            # via __main__.py
    python quam_state_manager/main.py       # directly
    quam-manager.exe                  # PyInstaller bundle
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import threading
import time
from typing import TYPE_CHECKING
from urllib.request import urlopen

import webview

if TYPE_CHECKING:
    from flask import Flask

from quam_state_manager.web.app import create_app

logger = logging.getLogger(__name__)

def _shutdown() -> None:
    """Force-exit the process so Flask daemon threads are cleaned up."""
    os._exit(0)


WINDOW_TITLE = "QUAM State Manager"
DEFAULT_WIDTH = 1400
DEFAULT_HEIGHT = 900
MIN_WIDTH = 1000
MIN_HEIGHT = 600
SERVER_STARTUP_TIMEOUT = 10.0


def find_free_port() -> int:
    """Bind to port 0 and let the OS assign a free port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: float = SERVER_STARTUP_TIMEOUT) -> bool:
    """Block until the Flask server responds on the given port, or timeout."""
    url = f"http://127.0.0.1:{port}/"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=1):
                return True
        except Exception:
            time.sleep(0.1)
    return False


def _start_server(app: Flask, port: int) -> threading.Thread:
    """Start the Flask server in a daemon thread.

    Uses Werkzeug's built-in server. ``use_reloader=False`` is critical
    because the reloader spawns a child process that breaks PyInstaller
    and pywebview.
    """
    thread = threading.Thread(
        target=lambda: app.run(
            host="127.0.0.1",
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,   # a long Scheduler scan/run must not freeze the UI polls
        ),
        daemon=True,
    )
    thread.start()
    return thread


def main() -> None:
    """Entry point: create app, start server, open native window."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    app = create_app()
    port = find_free_port()

    logger.info("Starting Flask server on 127.0.0.1:%d", port)
    _start_server(app, port)

    if not _wait_for_server(port):
        logger.error("Flask server did not start within %.0fs", SERVER_STARTUP_TIMEOUT)
        sys.exit(1)

    logger.info("Server ready. Opening native window.")

    window = webview.create_window(
        WINDOW_TITLE,
        url=f"http://127.0.0.1:{port}",
        width=DEFAULT_WIDTH,
        height=DEFAULT_HEIGHT,
        min_size=(MIN_WIDTH, MIN_HEIGHT),
    )

    def _on_loaded():
        """Expose folder picker to the frontend via JS API."""
        pass

    window.events.loaded += _on_loaded

    webview.start()
    logger.info("Window closed. Exiting.")
    # Force-kill the process to ensure the Flask daemon thread is cleaned up.
    # On Windows, daemon threads may linger after webview.start() returns.
    _shutdown()


if __name__ == "__main__":
    main()
