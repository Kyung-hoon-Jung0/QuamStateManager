"""scipy is never left half-imported in the SM process.

Concurrent scipy imports from two threads can raise ``_DeadlockError`` inside
CPython's import machinery; when that hits a package import, the package is
dropped from ``sys.modules`` while its finished children stay, and the next
import re-creates the package without them bound. From then on
``from scipy import signal`` fails with "Module 'scipy' has no attribute
'_lib'" and ``/zline`` answers 500 for the life of the server.

Every check runs in a fresh interpreter: the pytest process has scipy loaded.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The state a failed package import leaves behind: children loaded, the
# package object gone (the next import builds a new one without them bound).
_ORPHAN = ("import sys\n"
           "import scipy._lib._ccallback\n"
           "del sys.modules['scipy']\n")


def _run(code: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([str(ROOT), str(ROOT / "tests")]),
               PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-c", code, *args], env=env,
                          capture_output=True, text=True, timeout=timeout)


def _last(out: subprocess.CompletedProcess) -> str:
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def test_the_orphaned_state_itself_breaks_a_plain_import():
    """The precondition is real: without the guard, the orphaned state makes
    ``from scipy import signal`` fail with the reported AttributeError."""
    out = _run(_ORPHAN + "import scipy\n"
               "try:\n"
               "    from scipy import signal\n"
               "    print('IMPORTED')\n"
               "except AttributeError as e:\n"
               "    print('BROKEN', e)\n")
    assert out.returncode == 0, out.stderr[-2000:]
    assert _last(out).startswith("BROKEN") and "_lib" in _last(out)


def test_an_orphaned_scipy_heals_on_the_next_guarded_import():
    out = _run(_ORPHAN +
               "from quam_state_manager.core.scipy_import import import_scipy\n"
               "sig = import_scipy('scipy.signal')\n"
               "import quam_state_manager.core.zline_filters as zf\n"
               "import scipy.stats\n"
               "assert zf.signal is sig\n"
               "print('OK')\n")
    assert out.returncode == 0, out.stderr[-2000:]
    assert _last(out) == "OK"


_BOOT = ("import sys, json\n"
         "from pathlib import Path\n"
         "from test_zline_page import _chip\n"
         "from quam_state_manager.web.app import create_app\n"
         "from quam_state_manager.core import waveform_synth\n"
         "tmp = Path(sys.argv[1]); live = tmp / 'chips' / 'live'\n"
         "live.mkdir(parents=True)\n"
         "s, w = _chip()\n"
         "(live / 'state.json').write_text(json.dumps(s), encoding='utf-8')\n"
         "(live / 'wiring.json').write_text(json.dumps(w), encoding='utf-8')\n"
         "app = create_app(testing=True, instance_path=str(tmp / '_inst'))\n"
         "c = app.test_client()\n"
         "# the server start runs this on its warm-up thread before the templates\n"
         "waveform_synth.preload_scipy_quietly(max_wait_s=0)\n"
         "assert 'scipy.signal.windows' in sys.modules\n"
         "assert c.post('/load', data={'folder': str(live)}).status_code in (200, 302)\n")


def test_after_the_server_preload_the_zline_page_renders(tmp_path):
    """App booted, the start-up preload finished, then /zline is 200 and
    zline_filters imports."""
    out = _run(_BOOT +
               "import quam_state_manager.core.zline_filters\n"
               "print(c.get('/zline').status_code)\n", str(tmp_path))
    assert out.returncode == 0, out.stderr[-3000:]
    assert _last(out) == "200"


def test_a_preload_that_ran_into_the_orphaned_state_still_serves_zline(tmp_path):
    """The reported failure: scipy left orphaned before the preload; the
    preload and the page that follows must both repair it, not 500."""
    out = _run(_ORPHAN + _BOOT +
               "print(c.get('/zline').status_code)\n", str(tmp_path))
    assert out.returncode == 0, out.stderr[-3000:]
    assert _last(out) == "200"


_RACE = ("import sys, threading, time\n"
         "from quam_state_manager.core import waveform_synth\n"
         "errors = []\n"
         "def fg(delay):\n"
         "    time.sleep(delay)\n"
         "    try:\n"
         "        import quam_state_manager.core.zline_filters  # noqa: F401\n"
         "        from scipy import signal  # noqa: F401\n"
         "    except BaseException as e:\n"
         "        errors.append(repr(e))\n"
         "pre = threading.Thread(target=waveform_synth.preload_scipy_quietly,\n"
         "                       kwargs={'max_wait_s': 0})\n"
         "pre.start()\n"
         "fgs = [threading.Thread(target=fg, args=(float(sys.argv[1]) * k,)) for k in range(3)]\n"
         "[t.start() for t in fgs]\n"
         "pre.join(120); [t.join(120) for t in fgs]\n"
         "import scipy.stats, scipy.ndimage, scipy.signal.windows\n"
         "from scipy import signal\n"
         "print('ERRORS' if errors else 'OK', errors)\n")


def test_a_foreground_import_racing_the_preload_leaves_both_working():
    """A foreground scipy.signal import (the /zline route) racing the preload
    thread, at several offsets: no import raises, scipy works afterwards."""
    for delay in ("0", "0.02", "0.1", "0.3"):
        out = _run(_RACE, delay)
        assert out.returncode == 0, (delay, out.stderr[-2000:])
        assert _last(out).startswith("OK"), (delay, out.stdout[-2000:])


_STORM = ("import sys, threading\n"
          "import numpy  # noqa: F401\n"
          "from quam_state_manager.core.scipy_import import import_scipy\n"
          "names = ['scipy.linalg', 'scipy.signal', 'scipy.signal.windows',\n"
          "         'scipy.interpolate', 'scipy.stats']\n"
          "gate = threading.Barrier(len(names)); errors = []\n"
          "def run(n):\n"
          "    gate.wait()\n"
          "    try:\n"
          "        import_scipy(n)\n"
          "    except BaseException as e:\n"
          "        errors.append(repr(e))\n"
          "ts = [threading.Thread(target=run, args=(n,)) for n in names]\n"
          "[t.start() for t in ts]; [t.join(120) for t in ts]\n"
          "print('ERRORS' if errors else 'OK', errors)\n")


def test_guarded_imports_from_many_threads_never_deadlock():
    """Five threads importing scipy subpackages at the same instant: raw
    imports raised _DeadlockError in about one start in four when measured;
    through the guard none does."""
    for _ in range(4):
        out = _run(_STORM)
        assert out.returncode == 0, out.stderr[-2000:]
        assert _last(out).startswith("OK"), out.stdout[-2000:]
