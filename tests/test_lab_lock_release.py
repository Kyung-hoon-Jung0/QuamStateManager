"""The machine lock is released on every path, also when the OS unlock raises.

A failing OS unlock (Windows answers PermissionError Errno 13) used to leave the
thread lock taken, so every later gate check in that SM answered "busy"."""
import threading

import pytest

from quam_state_manager.core import lab_waveform, lab_worker


@pytest.fixture
def lock(tmp_path, monkeypatch):
    lk = lab_worker._MachineLock()
    lk.path = tmp_path / "machine.lock"
    return lk


def _free_from_another_thread(lk) -> bool:
    out = []
    def probe():
        got = lk.acquire(blocking=False)
        out.append(got)
        if got:
            lk.release()
    t = threading.Thread(target=probe)
    t.start()
    t.join(5)
    return bool(out and out[0])


def test_a_failing_os_unlock_still_frees_the_lock(lock, monkeypatch):
    assert lock.acquire()

    def boom(fh):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(lock, "_os_unlock", boom)
    lock.release()                       # never raises into the caller
    assert _free_from_another_thread(lock), "the lock stayed taken"


def test_normal_release_frees(lock):
    assert lock.acquire()
    lock.release()
    assert _free_from_another_thread(lock)


def test_gate_check_after_a_failing_release_is_not_busy(tmp_path, monkeypatch):
    lk = lab_worker._MachineLock()
    lk.path = tmp_path / "m.lock"
    monkeypatch.setattr(lab_waveform, "_machine_lock", lambda p: lk)
    py = tmp_path / "python.exe"
    py.write_text("")
    monkeypatch.setattr(lab_worker, "_lab_dir", lambda p: None)
    calls = []

    def run_cold(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            monkeypatch.setattr(lk, "_os_unlock", lambda fh: (_ for _ in ()).throw(PermissionError(13, "x")))
            raise RuntimeError("the runner raised")
        return {"ok": False, "error": "e", "timed_out": False}
    monkeypatch.setattr(lab_waveform, "_run_cold", run_cold)
    first = lab_waveform.run_gate_check(str(py), "b", "a", ["m"], lock_wait=0.2)
    assert first["status"] == "crash"
    second = lab_waveform.run_gate_check(str(py), "b", "a", ["m"], lock_wait=0.2)
    assert second["status"] != "busy" and len(calls) == 2



def test_release_with_a_handle_already_closed_frees_the_lock(lock):
    assert lock.acquire()
    lock.depth.fh.close()                 # a closed handle raises ValueError on seek / unlock
    lock.release()
    assert _free_from_another_thread(lock)
