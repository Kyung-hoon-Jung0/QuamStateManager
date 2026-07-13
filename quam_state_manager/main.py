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

import atexit
import logging
import os
import socket
import sys
import tempfile
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


def _kill_scheduler(instance_path: str) -> None:
    """Group-kill any running Scheduler experiment subprocess before we exit.

    Each experiment is spawned in its OWN process session/group (for group-kill),
    so it is NOT reaped when this process dies — it would keep driving the OPX
    headless and writing state.json. ``scheduler.cancel`` sets the cancel event
    and group-kills the child. Registered via atexit as a backstop, AND called
    explicitly on the window-close path because ``os._exit(0)`` bypasses atexit.
    """
    try:
        from quam_state_manager.core import scheduler
        scheduler.cancel(instance_path)
    except Exception:  # noqa: BLE001 — best-effort cleanup on the way out
        logger.warning("scheduler cleanup on exit failed", exc_info=True)


def _user_instance_path() -> str | None:
    """A per-user, WRITABLE instance dir for the frozen exe (else None → dev default).

    When frozen (PyInstaller), Flask's default ``instance_relative_config`` derives
    instance_path from the package ``__file__`` — inside the install dir (e.g.
    ``C:\\Program Files\\quam-manager``), which a standard user can't write, so every
    working-copy / history / settings write fails. Point it at a per-user data dir.
    Running from source keeps the repo ``instance/`` (returns None)."""
    if not getattr(sys, "frozen", False):
        return None
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
    elif sys.platform == "darwin":
        base = os.path.join(home, "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    path = os.path.join(base, "QUAM State Manager")
    os.makedirs(path, exist_ok=True)
    return path


def _fatal_startup_error(exc: BaseException) -> None:
    """Surface a startup failure that console=False would otherwise swallow: write a
    findable log AND show a native dialog, so a failed launch isn't an invisible
    'nothing happened'."""
    import traceback
    msg = f"{type(exc).__name__}: {exc}"
    try:
        log_dir = _user_instance_path() or os.path.join(tempfile.gettempdir(), "quam-manager")
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "startup-error.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(traceback.format_exc())
        where = log_path
    except Exception:  # noqa: BLE001
        where = "(a log file could not be written)"
    logger.error("Startup failed: %s", msg, exc_info=True)
    body = (f"QUAM State Manager failed to start.\n\n{msg}\n\n"
            f"A detailed log was written to:\n{where}")
    try:
        if sys.platform == "win32":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, body, WINDOW_TITLE, 0x10)  # MB_ICONERROR
        else:
            print(body, file=sys.stderr)
    except Exception:  # noqa: BLE001
        print(body, file=sys.stderr)


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

    # Guard the whole startup: with console=False the exe swallows stderr, so an
    # unhandled exception here (e.g. a non-writable instance dir in Program Files,
    # a migration failure) means the user double-clicks and nothing happens —
    # no window, no error. Surface it via _fatal_startup_error instead.
    try:
        app = create_app(instance_path=_user_instance_path())
        # Backstop for exit paths that DO run atexit (sys.exit, Ctrl-C); the window
        # close below calls _kill_scheduler explicitly because os._exit bypasses atexit.
        atexit.register(_kill_scheduler, app.instance_path)
        port = find_free_port()
        logger.info("Starting Flask server on 127.0.0.1:%d", port)
        _start_server(app, port)
        if not _wait_for_server(port):
            raise RuntimeError(
                f"Flask server did not start within {SERVER_STARTUP_TIMEOUT:.0f}s")
    except Exception as exc:  # noqa: BLE001
        _fatal_startup_error(exc)
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

    def _on_closing():
        """Confirm before closing if any context has unsaved edits.

        The change_log (the tray's "N unsaved") lives ONLY in server memory —
        Save writes it to the working copy — so closing the window discards it
        with no trace. beforeunload isn't reliably surfaced by every pywebview
        backend, so guard on the Python side too. Returning False cancels the
        close; on any error we allow it (never trap the user in an unclosable
        window).
        """
        try:
            from quam_state_manager.web.routes import any_unsaved_changes
            if not any_unsaved_changes(app):
                return True
            return bool(window.create_confirmation_dialog(
                WINDOW_TITLE,
                "You have unsaved edits that haven't been written to disk yet. "
                "Close anyway and discard them?"))
        except Exception:  # noqa: BLE001
            logger.warning("window-close unsaved-edits guard failed", exc_info=True)
            return True

    window.events.closing += _on_closing

    webview.start()
    logger.info("Window closed. Exiting.")
    # Kill any in-flight Scheduler experiment BEFORE os._exit — otherwise it keeps
    # driving the OPX headless (os._exit bypasses the atexit backstop above).
    _kill_scheduler(app.instance_path)
    # Force-kill the process to ensure the Flask daemon thread is cleaned up.
    # On Windows, daemon threads may linger after webview.start() returns.
    _shutdown()


if __name__ == "__main__":
    main()
