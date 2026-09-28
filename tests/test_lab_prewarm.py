"""w9/labwarm -- the lab-code worker is started BEFORE the first lab check.

The first lab edit/delete check after a server start paid the worker's spawn
and imports (5-12 s on the KRS 5Q chip, ~12 s big30x) while the user watched
a cell. Now:

1. PREWARM (core/lab_waveform.prewarm): a chip that carries a lab class the
   pulse/gate check would ask, opened with an env selected, starts the env's
   worker in the background -- it IMPORTS the chip's lab classes and nothing
   else (no dataclass built, no waveform drawn, no chip loaded). A check that
   arrives while the imports run waits for THAT worker (one process, never a
   second cold one); a check already holding the env starts it itself.
2. WHEN (routes._maybe_prewarm_lab_worker): only when BOTH conditions hold
   (lab class + env selected); never for an archive; after an env switch the
   old env's worker is retired at once and the new one started.
3. IDLE: a worker is retired after 60 min idle (was 10 min).
4. The UI's state: ``worker_state`` -> ready / starting / cold / no-env, what
   "Preparing your lab code... (first check after start)" is decided on.

The subprocess pins run the REAL worker script under this interpreter (the cqt
env carries quam); they skip where quam is absent.
"""

from __future__ import annotations

import json
import sys
import threading
import time

import pytest

from quam_state_manager.core import lab_waveform
from quam_state_manager.web.app import create_app
from tests.test_lab_doors import GATE, LAB, _state
from tests.test_pulses_routes import _make_wiring

try:
    import quam.components.pulses  # noqa: F401
    HAVE_QUAM = True
except Exception:  # noqa: BLE001
    HAVE_QUAM = False
needs_quam = pytest.mark.skipif(not HAVE_QUAM, reason="quam not importable")

PKG = "smlab_warm"
CLS = PKG + ".cz.WarmCZPulse"
SRC = '''import time as _t
import os as _os
from quam.components.pulses import Pulse
from quam.core import quam_dataclass

_t.sleep(float(_os.environ.get("SM_WARM_IMPORT_SLEEP", "0")))
IMPORTED_AT = _t.time()


@quam_dataclass
class WarmCZPulse(Pulse):
    amplitude: float
    length: int = 16

    def waveform_function(self):
        return [self.amplitude] * self.length
'''


@pytest.fixture
def labpkg(tmp_path, monkeypatch):
    root = tmp_path / "labpkg"
    pkg = root / PKG
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "cz.py").write_text(SRC, encoding="utf-8")
    (pkg / "other.py").write_text(SRC.replace("WarmCZPulse", "OtherPulse")
                                  .replace("_t.sleep(", "(lambda s: None)("), encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(root))
    lab_waveform.shutdown_workers()
    lab_waveform.MEMO.clear()
    with lab_waveform._SLOT_LOCK:
        lab_waveform._SLOT_FILES.clear()
    lab_waveform.PREWARM_LOG.clear()
    with lab_waveform._PREWARM_LOCK:
        lab_waveform._PREWARM.clear()
    yield pkg
    lab_waveform.shutdown_workers()
    lab_waveform.MEMO.clear()
    lab_waveform.PREWARM_LOG.clear()


def _w():
    return lab_waveform._WORKERS.get(sys.executable)


# ---------------------------------------------------------------- 1. prewarm
@needs_quam
class TestThePrewarm:
    def test_it_imports_and_the_first_check_is_answered_warm(self, labpkg):
        assert lab_waveform.worker_state(sys.executable) == "cold"
        assert lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        w = _w()
        assert w is not None and w.prewarmed and w.ready and w.requests == 1
        assert lab_waveform.worker_state(sys.executable) == "ready"
        assert lab_waveform.PREWARM_LOG[sys.executable]["state"] == "ready"
        assert lab_waveform.PREWARM_LOG[sys.executable]["imported"] == [True]
        # the import answer recorded the lab module it read: an edit to it
        # still retires this worker (the staleness contract is unchanged)
        assert any(f.endswith("cz.py") for f in w.sources)
        t0 = time.perf_counter()
        rec = lab_waveform.draw(sys.executable, [(CLS, {"amplitude": 0.25})])[0]
        dt = time.perf_counter() - t0
        assert rec["ok"] and rec["i"] == [0.25] * 16
        assert _w() is w and w.requests == 2       # the SAME process answered
        assert dt < 5, dt

    def test_it_builds_draws_and_caches_nothing(self, labpkg):
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        assert lab_waveform.MEMO.slots() == []
        with lab_waveform._SLOT_LOCK:
            assert not lab_waveform._SLOT_FILES

    def test_an_unimportable_class_is_reported_never_raised(self, labpkg):
        lab_waveform.prewarm(sys.executable, [CLS, "nolab.x.Missing"],
                             start_thread=False)
        log = lab_waveform.PREWARM_LOG[sys.executable]
        assert log["state"] == "ready" and log["imported"] == [False, True]

    def test_a_check_arriving_meanwhile_waits_for_THAT_worker(self, labpkg, monkeypatch):
        monkeypatch.setenv("SM_WARM_IMPORT_SLEEP", "2")
        spawned = []
        real = lab_waveform._Worker

        class Counting(real):
            def __init__(self, *a, **k):
                spawned.append(1)
                super().__init__(*a, **k)
        monkeypatch.setattr(lab_waveform, "_Worker", Counting)
        assert lab_waveform.prewarm(sys.executable, [CLS])       # a thread
        for _ in range(200):
            if lab_waveform.worker_state(sys.executable) == "starting":
                break
            time.sleep(0.02)
        assert lab_waveform.worker_state(sys.executable) == "starting"
        rec = lab_waveform.draw(sys.executable, [(CLS, {"amplitude": 0.5})])[0]
        assert rec["ok"] and rec["i"] == [0.5] * 16
        assert spawned == [1], "a second, cold worker was started"
        assert _w().prewarmed and _w().requests == 2

    def test_it_never_takes_the_env_from_a_running_check(self, labpkg):
        lk = lab_waveform._env_lock(sys.executable)
        with lk:
            assert lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        assert lab_waveform.PREWARM_LOG[sys.executable]["state"] == "check-running"
        assert _w() is None

    def test_a_ready_worker_is_not_restarted(self, labpkg):
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        w = _w()
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        assert _w() is w and lab_waveform.PREWARM_LOG[sys.executable]["state"] == "already-ready"

    def test_it_waits_for_the_quiet_server_and_stands_down_when_superseded(self, labpkg):
        seen = []

        def wait(stop):
            seen.append(stop())
            lab_waveform.retire_except("C:/another/env/python.exe")   # env switched meanwhile
            seen.append(stop())
        assert lab_waveform.prewarm(sys.executable, [CLS], wait=wait, start_thread=False)
        assert seen == [False, True]
        assert lab_waveform.PREWARM_LOG[sys.executable]["state"] == "superseded"
        assert _w() is None

    def test_a_timed_out_import_answers_the_waiting_check_at_once(self, labpkg, monkeypatch):
        monkeypatch.setenv("SM_WARM_IMPORT_SLEEP", "30")
        monkeypatch.setattr(lab_waveform, "TIMEOUT_S", 3)
        lab_waveform.prewarm(sys.executable, [CLS])
        for _ in range(200):
            if lab_waveform.worker_state(sys.executable) == "starting":
                break
            time.sleep(0.02)
        t0 = time.perf_counter()
        rec = lab_waveform.draw(sys.executable, [(CLS, {"amplitude": 0.5})])[0]
        dt = time.perf_counter() - t0
        assert rec["ok"] is False and "did not answer within" in rec["error"]
        assert dt < 3 * 1.8, dt        # one TIMEOUT_S, never two
        assert _w() is None

    def test_a_chip_switched_to_meanwhile_gets_its_classes_imported_too(self, labpkg, monkeypatch):
        monkeypatch.setenv("SM_WARM_IMPORT_SLEEP", "2")
        spawned = []
        real = lab_waveform._Worker

        class Counting(real):
            def __init__(self, *a, **k):
                spawned.append(1)
                super().__init__(*a, **k)
        monkeypatch.setattr(lab_waveform, "_Worker", Counting)
        other = PKG + ".other.OtherPulse"
        assert lab_waveform.prewarm(sys.executable, [CLS])
        for _ in range(200):
            if lab_waveform.worker_state(sys.executable) == "starting":
                break
            time.sleep(0.02)
        assert not lab_waveform.prewarm(sys.executable, [other])   # one in flight
        for _ in range(600):
            if lab_waveform.PREWARM_LOG[sys.executable].get("more"):
                break
            time.sleep(0.05)
        assert lab_waveform.PREWARM_LOG[sys.executable].get("more") == [other]
        assert spawned == [1] and _w().requests == 2
        # and a later chip on a READY worker is asked of the same process
        assert lab_waveform.prewarm(sys.executable, [other, CLS], start_thread=False)
        assert lab_waveform.PREWARM_LOG[sys.executable]["state"] == "already-ready"
        assert spawned == [1] and _w().requests == 3

    def test_no_env_no_class_or_warm_off_start_nothing(self, labpkg, monkeypatch):
        assert not lab_waveform.prewarm(None, [CLS], start_thread=False)
        assert not lab_waveform.prewarm(sys.executable, [], start_thread=False)
        monkeypatch.setattr(lab_waveform, "WARM", False)
        assert not lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        assert _w() is None


# ---------------------------------------------------- 2. env switch + idle
@needs_quam
class TestRetirement:
    def test_an_env_switch_retires_the_old_worker_now(self, labpkg):
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        w = _w()
        assert lab_waveform.retire_except("C:/another/env/python.exe") == 1
        assert _w() is None and w.proc.poll() is not None

    def test_a_background_retire_never_waits_on_the_kill(self, labpkg, monkeypatch):
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        w = _w()
        real_kill = w.kill
        done = []

        def slow_kill():
            time.sleep(2)
            real_kill()
            done.append(1)
        monkeypatch.setattr(w, "kill", slow_kill)
        t0 = time.perf_counter()
        assert lab_waveform.retire_except("C:/another/env/python.exe", background=True) == 1
        assert time.perf_counter() - t0 < 0.5
        assert _w() is None                     # never asked again, at once
        for _ in range(100):
            if done:
                break
            time.sleep(0.1)
        assert done and w.proc.poll() is not None

    def test_the_same_env_keeps_its_worker(self, labpkg):
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        w = _w()
        assert lab_waveform.retire_except(sys.executable) == 0
        assert _w() is w and w.proc.poll() is None

    def test_an_edited_lab_file_makes_it_cold_again(self, labpkg):
        lab_waveform.prewarm(sys.executable, [CLS], start_thread=False)
        f = labpkg / "cz.py"
        f.write_text(f.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
        assert lab_waveform.worker_state(sys.executable) == "cold"


def test_idle_retirement_is_60_minutes(monkeypatch):
    assert lab_waveform.WARM_IDLE_S == 3600
    armed = []

    class T:
        def __init__(self, interval, fn, args=()):
            armed.append(interval)
            self.daemon = False

        def start(self):
            pass

        def cancel(self):
            pass
    monkeypatch.setattr(lab_waveform.threading, "Timer", T)
    w = lab_waveform._Worker.__new__(lab_waveform._Worker)
    w._idle = None
    w.python_path = "x"
    w._arm_idle()
    assert armed == [3600]


def test_worker_state_names():
    assert lab_waveform.worker_state(None) == "no-env"
    assert lab_waveform.worker_state("C:/nowhere/python.exe") == "cold"


# ------------------------------------------------ 3. when it is started
ROOT = "mylab.root.Quam"


def _chip(tmp_path, state):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    return folder


def _plain_state():
    st = _state()
    for q in st["qubits"].values():
        q["z"]["operations"] = {}
    st["qubit_pairs"] = {}
    st["__class__"] = "quam_builder.architecture.superconducting.qpu.FixedFrequencyQuam"
    return st


@pytest.fixture
def started(tmp_path, monkeypatch):
    """An app whose pre-warm is REAL up to the spawn: the decision runs on its
    thread, and the starter records (python, classes, reason) instead."""
    from quam_state_manager.core import config_generator
    calls = []
    retired = []
    env = {"python": sys.executable}
    monkeypatch.setattr(config_generator, "get_selected_env",
                        lambda inst: env["python"])
    monkeypatch.setattr(lab_waveform, "retire_except",
                        lambda p, **kw: retired.append(p) or 0)
    # the other background warms of a selection / an open are not these pins'
    # subject (and must not spawn the fake interpreter)
    from quam_state_manager.web import routes
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: {})
    monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)

    def make(state, **cfg):
        app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        app.config["SM_LAB_PREWARM_IN_TESTS"] = True
        app.config["SM_LAB_PREWARM_STARTER"] = lambda py, cls, why: calls.append((py, cls, why))
        app.config.update(cfg)
        c = app.test_client()
        c._app = app
        c._folder = _chip(tmp_path, state)
        return c
    yield make, calls, env, retired


def _settle():
    """Join the decision threads (they wait for a quiet server first)."""
    for t in list(threading.enumerate()):
        if t.name == "lab-prewarm-decide":
            t.join(timeout=40)


class TestWhenItStarts:
    def test_a_lab_chip_with_an_env_starts_it_with_the_classes_the_check_imports(self, started):
        make, calls, env, _ = started
        c = make(_state())
        assert c.post("/load", data={"folder": str(c._folder)}).status_code in (200, 302)
        _settle()
        assert len(calls) == 1
        py, classes, why = calls[0]
        assert py == sys.executable and why == "chip-open"
        assert classes == sorted({LAB, GATE, ROOT})

    def test_no_env_selected_starts_nothing(self, started):
        make, calls, env, _ = started
        env["python"] = None
        c = make(_state())
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        assert calls == []

    def test_an_env_that_is_gone_starts_nothing(self, started, tmp_path):
        make, calls, env, _ = started
        env["python"] = str(tmp_path / "gone" / "python.exe")
        c = make(_state())
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        assert calls == []

    def test_a_chip_without_lab_classes_starts_nothing(self, started):
        make, calls, env, _ = started
        c = make(_plain_state())
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        assert calls == []

    def test_the_suite_never_spawns_unless_a_pin_asks(self, started):
        make, calls, env, _ = started
        c = make(_state(), SM_LAB_PREWARM_IN_TESTS=False)
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        assert calls == []

    def test_an_env_switch_retires_the_old_and_starts_the_new(self, started, tmp_path):
        make, calls, env, retired = started
        c = make(_state())
        c.post("/load", data={"folder": str(c._folder)})
        _settle()
        other = tmp_path / "envB" / "python.exe"
        other.parent.mkdir()
        other.write_bytes(b"")
        env["python"] = str(other)
        r = c.post("/generate/select-env", json={"python": str(other)},
                   headers={"Origin": "http://localhost"})
        assert r.status_code == 200, r.data[:300]
        _settle()
        assert retired and retired[-1] == str(other)
        assert calls[-1][0] == str(other) and calls[-1][2] == "env-select"

    def test_an_env_select_never_waits_on_the_old_worker_dying(self, tmp_path, monkeypatch):
        from quam_state_manager.core import config_generator
        from quam_state_manager.web import routes
        monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: {})
        monkeypatch.setattr(routes, "_warm_state_schema_async", lambda *a, **k: None)
        killed = []

        class Slow:
            def kill(self):
                time.sleep(3)
                killed.append(1)
        with lab_waveform._WORKERS_LOCK:
            lab_waveform._WORKERS["C:/old/env/python.exe"] = Slow()
        app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
        new = tmp_path / "envB" / "python.exe"
        new.parent.mkdir()
        new.write_bytes(b"")
        t0 = time.perf_counter()
        r = app.test_client().post("/generate/select-env", json={"python": str(new)})
        assert r.status_code == 200
        assert time.perf_counter() - t0 < 2.0, "the request waited on the kill"
        assert "C:/old/env/python.exe" not in lab_waveform._WORKERS
        for _ in range(60):
            if killed:
                break
            time.sleep(0.1)
        assert killed == [1]

    def test_the_request_never_waits_for_the_decision(self, started, monkeypatch):
        from quam_state_manager.web import routes
        make, calls, env, _ = started
        gate = threading.Event()
        real = routes._lab_prewarm_classes
        monkeypatch.setattr(routes, "_lab_prewarm_classes",
                            lambda store: (gate.wait(10), real(store))[1])
        c = make(_state())
        t0 = time.perf_counter()
        assert c.post("/load", data={"folder": str(c._folder)}).status_code in (200, 302)
        assert time.perf_counter() - t0 < 8
        assert calls == []                 # still deciding, off the request
        gate.set()
        _settle()
        assert len(calls) == 1


# -------------------------------------------------------- 4. the UI's state
class TestTheStatusRoute:
    def test_the_status_names_the_worker_state(self, started, monkeypatch):
        make, calls, env, _ = started
        c = make(_state())
        monkeypatch.setattr(lab_waveform, "worker_state", lambda p: "starting")
        j = c.get("/api/lab/worker-status").get_json()
        assert j["state"] == "starting" and j["env"] == sys.executable

    def test_lab_watch_carries_it_for_a_lab_path(self, started, monkeypatch):
        make, calls, env, _ = started
        c = make(_state())
        c.post("/load", data={"folder": str(c._folder)})
        monkeypatch.setattr(lab_waveform, "worker_state", lambda p: "cold")
        j = c.post("/field/lab-watch", json={"paths": [
            "qubit_pairs.q1-q2.macros.czl.flux_pulse_qubit.flat_length"]}).get_json()
        assert j == {"lab": True, "worker": "cold"}
        j = c.post("/field/lab-watch", json={"paths": ["qubits.q1.f_01"]}).get_json()
        assert j == {"lab": False}


# ------------------------------------------ 5. the delete step says so
class TestTheDeleteStep:
    def _detail(self, started, path):
        make, calls, env, _ = started
        st = _state()
        st["qubits"]["q3"]["xy"]["operations"]["x180"] = {
            "__class__": "quam.components.pulses.SquarePulse", "amplitude": 0.1, "length": 100}
        c = make(st, SM_LAB_PREWARM_IN_TESTS=False)
        c.post("/load", data={"folder": str(c._folder)})
        return c.get(f"/pulse/detail?path={path}").get_data(as_text=True)

    def test_a_delete_the_lab_is_asked_about_says_checking(self, started):
        t = self._detail(started, "qubit_pairs.q1-q2.macros.czl.flux_pulse_qubit")
        i = t.index('class="btn-sm pulse-confirm-delete"')
        after = t[i:t.index("</form>", i)]
        assert ('class="htmx-indicator muted pulse-delete-checking" role="status" '
                'data-lab-indicator data-lab-state="cold">Checking with your lab code&hellip;</span>') in after
        # beside the button the press disables
        assert 'hx-disabled-elt="find button[type=submit]"' in t[t.rindex("<form", 0, i):i]

    def test_an_ordinary_delete_never_claims_a_lab_check(self, started):
        t = self._detail(started, "qubits.q3.xy.operations.x180")
        assert "pulse-confirm-delete" in t and "pulse-delete-checking" not in t


def test_the_status_poll_is_not_foreground_work():
    """The "Preparing..." text polls it every 600 ms while a check waits:
    counted as foreground it would keep the server busy and hold back the
    very pre-warm it reports on."""
    from quam_state_manager.core import activity
    assert activity.is_foreground("/api/lab/worker-status") is False
    assert activity.is_foreground("/field/edit") is True


def test_the_prewarm_waits_for_quiet_only_briefly(monkeypatch):
    """The in-process step yields to a busy server, but a subprocess spawn
    holds no GIL and no lock -- it never waits past the cap."""
    from quam_state_manager.core import activity
    from quam_state_manager.web import routes
    assert routes._LAB_PREWARM_QUIET_MAX_S <= 5
    monkeypatch.setattr(routes, "_LAB_PREWARM_QUIET_MAX_S", 0.4)
    monkeypatch.setattr(activity, "busy", lambda quiet_s=0.5: True)
    t0 = time.perf_counter()
    routes._lab_prewarm_wait(lambda: False)
    assert 0.35 <= time.perf_counter() - t0 < 2
    monkeypatch.setattr(activity, "busy", lambda quiet_s=0.5: False)
    t0 = time.perf_counter()
    routes._lab_prewarm_wait(lambda: False)
    assert time.perf_counter() - t0 < 0.3
