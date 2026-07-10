"""Tests for the desktop entry point (quam_state_manager.main)."""

from __future__ import annotations

import socket
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import MagicMock, patch

import pytest

from quam_state_manager.main import (
    WINDOW_TITLE,
    DEFAULT_WIDTH,
    DEFAULT_HEIGHT,
    MIN_WIDTH,
    MIN_HEIGHT,
    SERVER_STARTUP_TIMEOUT,
    find_free_port,
    _wait_for_server,
    _start_server,
)


class TestFindFreePort:
    """Tests for find_free_port()."""

    def test_returns_int(self):
        port = find_free_port()
        assert isinstance(port, int)

    def test_returns_valid_range(self):
        port = find_free_port()
        assert 1024 <= port <= 65535

    def test_port_is_actually_free(self):
        port = find_free_port()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))

    def test_consecutive_calls_differ(self):
        ports = {find_free_port() for _ in range(5)}
        assert len(ports) >= 2, "Expected at least 2 distinct ports from 5 calls"


class TestWaitForServer:
    """Tests for _wait_for_server()."""

    def test_returns_true_when_server_running(self):
        port = find_free_port()

        class _OK(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", port), _OK)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            assert _wait_for_server(port, timeout=5.0) is True
        finally:
            server.shutdown()

    def test_returns_false_on_timeout(self):
        port = find_free_port()
        assert _wait_for_server(port, timeout=0.3) is False


class TestStartServer:
    """Tests for _start_server()."""

    def test_starts_daemon_thread(self):
        barrier = threading.Event()

        mock_app = MagicMock()
        mock_app.run.side_effect = lambda **_kw: barrier.wait(timeout=5)

        port = find_free_port()
        thread = _start_server(mock_app, port)
        try:
            assert isinstance(thread, threading.Thread)
            assert thread.daemon is True
            assert thread.is_alive()
        finally:
            barrier.set()
            thread.join(timeout=2)

        mock_app.run.assert_called_once_with(
            host="127.0.0.1",
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )


class TestMainFunction:
    """Tests for main() -- uses mocks to avoid opening real windows."""

    @patch("quam_state_manager.main._shutdown")
    @patch("quam_state_manager.main.webview")
    @patch("quam_state_manager.main._wait_for_server", return_value=True)
    @patch("quam_state_manager.main._start_server")
    @patch("quam_state_manager.main.create_app")
    @patch("quam_state_manager.main.find_free_port", return_value=9999)
    def test_happy_path(
        self,
        mock_port,
        mock_create_app,
        mock_start_server,
        mock_wait,
        mock_webview,
        mock_shutdown,
    ):
        from quam_state_manager.main import main

        mock_window = MagicMock()
        mock_webview.create_window.return_value = mock_window

        main()

        mock_create_app.assert_called_once()
        mock_port.assert_called_once()
        mock_start_server.assert_called_once()
        mock_wait.assert_called_once_with(9999)
        mock_webview.create_window.assert_called_once_with(
            WINDOW_TITLE,
            url="http://127.0.0.1:9999",
            width=DEFAULT_WIDTH,
            height=DEFAULT_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
        )
        mock_webview.start.assert_called_once()

    @patch("quam_state_manager.main.webview")
    @patch("quam_state_manager.main._wait_for_server", return_value=False)
    @patch("quam_state_manager.main._start_server")
    @patch("quam_state_manager.main.create_app")
    @patch("quam_state_manager.main.find_free_port", return_value=8888)
    def test_exits_on_server_timeout(
        self,
        mock_port,
        mock_create_app,
        mock_start_server,
        mock_wait,
        mock_webview,
    ):
        from quam_state_manager.main import main

        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
        mock_webview.create_window.assert_not_called()


class TestConstants:
    """Sanity checks for module-level constants."""

    def test_window_title(self):
        assert WINDOW_TITLE == "QUAM State Manager"

    def test_dimensions(self):
        assert DEFAULT_WIDTH >= MIN_WIDTH
        assert DEFAULT_HEIGHT >= MIN_HEIGHT

    def test_timeout_positive(self):
        assert SERVER_STARTUP_TIMEOUT > 0


class TestModuleImport:
    """Verify the module and __main__ can be imported."""

    def test_import_main_module(self):
        import quam_state_manager.main
        assert hasattr(quam_state_manager.main, "main")
        assert hasattr(quam_state_manager.main, "find_free_port")

    def test_import_dunder_main(self):
        import importlib
        spec = importlib.util.find_spec("quam_state_manager.__main__")
        assert spec is not None
