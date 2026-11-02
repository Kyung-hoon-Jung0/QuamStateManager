"""Measure foreground check queueing without importing an external stack."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from quam_state_manager.core import lab_waveform
from quam_state_manager.core.interactive_plots import replot


def test_machine_lock_lab_check_waits_for_slow_replay(tmp_path, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    checking = threading.Event()
    ran_check = threading.Event()

    def slow_worker(*args):
        entered.set()
        assert release.wait(10)
        return {"figures": [], "errors": []}

    def check_worker(*args):
        ran_check.set()
        return {"ok": False, "error": "synthetic answer"}

    monkeypatch.setattr(replot, "_warm_request", slow_worker)
    monkeypatch.setattr(lab_waveform, "cached", lambda *a: None)
    monkeypatch.setattr(lab_waveform, "_env_sig", lambda *a: "fake")
    monkeypatch.setattr(lab_waveform, "_run", check_worker)

    def check():
        checking.set()
        t0 = time.monotonic()
        result = lab_waveform.draw("fake", [("example.Pulse", {})])
        return time.monotonic() - t0, result

    with ThreadPoolExecutor(max_workers=2) as pool:
        replay = pool.submit(replot._invoke, "fake", tmp_path, None, 10)
        try:
            assert entered.wait(5)
            pending = pool.submit(check)
            assert checking.wait(5)
            # Events set up the contention; the deliberate hold is the measured
            # fake worker cost, not a dependency on actual subprocess startup.
            assert not ran_check.wait(.4), "check entered while replay was using its worker"
        finally:
            release.set()
        elapsed, result = pending.result(timeout=5)
        assert replay.result(timeout=5) == {"figures": [], "errors": []}
    assert elapsed >= .35 and ran_check.is_set()
    assert result[0]["reason"] == "run-failed"
    print(f"lab check wait: {elapsed:.3f}s for a 0.400s replay hold")
