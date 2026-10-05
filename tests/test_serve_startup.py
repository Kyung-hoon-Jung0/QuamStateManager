"""`qsm serve` / `qsm browser` never send a person to a port that is not open yet.

On-site report: after installing from main, `qsm serve` showed "connection
refused" for ~20 s on a customer PC. Two causes: the app's startup imported
scipy (Diagnostics -> waveform_synth: 4 s warm, ~15 s on a first start), and the
CLI said "open <url>" (serve) or opened the browser after a fixed 1 s (browser)
BEFORE the app was built and the port bound (docs/287).
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_app_startup_path_does_not_import_scipy(tmp_path):
    """Building the app (what `qsm serve` waits for before binding) loads no
    scipy; waveform synthesis imports it when a waveform is drawn."""
    code = ("import sys\n"
            "from quam_state_manager.web.app import create_app\n"
            "create_app(testing=True, instance_path=sys.argv[1])\n"
            "print('scipy' in sys.modules)\n")
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8="1")
    out = subprocess.run([sys.executable, "-c", code, str(tmp_path / "inst")], env=env,
                         capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().splitlines()[-1] == "False"


def test_waveform_synthesis_still_uses_the_scipy_functions():
    """Lazy, not replaced: the flat-top and the band-limited shapes still come
    from scipy's own windows/filter (the bit-exact golden depends on it)."""
    import numpy as np
    from scipy.ndimage import gaussian_filter1d as ref_filter
    from scipy.signal.windows import blackman, gaussian

    from quam_state_manager.core import waveform_synth as ws
    assert np.array_equal(ws._gaussian_window(10, 2.0), gaussian(10, 2.0))
    assert np.array_equal(ws._blackman_window(8), blackman(8))
    x = np.arange(20, dtype=float)
    assert np.array_equal(ws.gaussian_filter1d(x, sigma=1.5), ref_filter(x, sigma=1.5))


def test_ready_is_announced_only_once_the_port_answers():
    from quam_state_manager import cli
    port = _free_port()
    fired = []
    cli._when_listening("127.0.0.1", port, lambda: fired.append(time.monotonic()), timeout_s=10)
    t_listen = time.monotonic() + 0.8
    time.sleep(0.8)
    assert fired == [], "announced before anything listened"
    srv = socket.socket()
    srv.bind(("127.0.0.1", port))
    srv.listen()
    try:
        deadline = time.monotonic() + 5
        while not fired and time.monotonic() < deadline:
            time.sleep(0.05)
        assert fired and fired[0] >= t_listen
    finally:
        srv.close()


def test_a_busy_port_is_refused_before_the_app_is_built(monkeypatch):
    """The busy-port answer must not wait for the app build."""
    import typer

    from quam_state_manager import cli
    built = []
    import quam_state_manager.web.app as appmod
    monkeypatch.setattr(appmod, "create_app", lambda *a, **k: built.append(1))
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()
    port = srv.getsockname()[1]
    try:
        with pytest.raises(typer.Exit) as exc:
            cli.serve(port=port, host="127.0.0.1", debug=False)
        assert exc.value.exit_code == 1 and built == []
    finally:
        srv.close()


@pytest.mark.parametrize("command", ["serve", "browser"])
def test_the_url_is_given_and_the_browser_opened_only_after_the_bind(monkeypatch, capsys, command):
    """A slow app build (here 1.2 s) must not produce "ready"/a browser before
    the server listens -- the old `browser` opened it after a fixed 1 s."""
    import webbrowser

    import quam_state_manager.web.app as appmod
    from quam_state_manager import cli
    port = _free_port()
    events = []
    monkeypatch.setattr(appmod, "create_app", lambda *a, **k: (time.sleep(1.2), "app")[1])
    monkeypatch.setattr(webbrowser, "open", lambda url: events.append(("browser", time.monotonic(), url)))

    def fake_run_app(app, *, host, port, debug):
        srv = socket.socket()
        srv.bind((host, port))
        srv.listen()
        events.append(("bound", time.monotonic(), None))
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not any(e[0] == "browser" for e in events) \
                    and "ready" not in capsys.readouterr().out:
                time.sleep(0.05)
            time.sleep(0.3)
        finally:
            srv.close()
    monkeypatch.setattr(cli, "_run_app", fake_run_app)
    if command == "serve":
        cli.serve(port=port, host="127.0.0.1", debug=False)
    else:
        cli.browser(port=port, host="127.0.0.1", debug=False, no_open=False)
    bound = next(e[1] for e in events if e[0] == "bound")
    if command == "browser":
        opened = [e for e in events if e[0] == "browser"]
        assert opened and opened[0][1] >= bound and opened[0][2] == f"http://127.0.0.1:{port}"
