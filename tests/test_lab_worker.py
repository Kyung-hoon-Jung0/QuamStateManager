"""Synthetic pins for the shared lab worker slot and line protocol."""
import io
import os
import json
import sys
import time
from types import SimpleNamespace

import pytest

from quam_state_manager.core import lab_worker, lab_waveform
from quam_state_manager.generator import run_interactive_replot as runner


def test_plot_serve_multiple_requests_and_bad_line(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"run":"a"}\ninvalid\n{"run":"b"}\n'))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(runner, "replay", lambda args: {"figures": [args.run], "errors": []})
    assert runner.serve() == 0
    results = [json.loads(line[len(runner.SERVE_MARK):]) for line in out.getvalue().splitlines()]
    assert len(results) == 3
    assert results[0]["figures"] == ["a"] and results[2]["figures"] == ["b"]
    assert results[1]["status"] == "error"


def test_worker_reuses_then_retires_across_kinds(tmp_path):
    script = tmp_path / "worker.py"
    script.write_text('import sys,json\nfor line in sys.stdin:\n print("@"+json.dumps({"status":"ok","sources":{},"echo":json.loads(line)}),flush=True)\n')
    with lab_worker.machine_lock:
        a = lab_worker.Worker(sys.executable, str(script), "@")
        try:
            assert a.ask({"kind": "plot"}, 5)["echo"] == {"kind": "plot"}
            assert a.ask({"kind": "plot"}, 5) and a.requests == 2
            b = lab_worker.Worker(sys.executable, str(script), "@")
            try:
                assert a.proc.poll() is not None
                assert b.ask({"kind": "waveform"}, 5)
            finally:
                b.kill()
        finally:
            a.kill()


def test_idle_expiry_and_global_env_lock(tmp_path):
    script = tmp_path / "idle.py"
    script.write_text('import sys\nfor line in sys.stdin:\n print(\'@{"status":"ok"}\',flush=True)\n')
    with lab_worker.machine_lock:
        w = lab_worker.Worker(sys.executable, str(script), "@", idle_s=.05)
        assert w.ask({}, 5)
    try:
        # Descendant discovery on Windows can outlast the old two-second poll.
        for _ in range(500):
            if w.proc.poll() is not None:
                break
            time.sleep(.02)
        assert w.proc.poll() is not None
        assert lab_waveform._env_lock("a") is lab_waveform._env_lock("b") is lab_worker.machine_lock
    finally:
        w.kill()


def test_worker_timeout_and_source_freshness(tmp_path):
    script = tmp_path / "blocked.py"
    script.write_text('import time\ntime.sleep(60)\n')
    with lab_worker.machine_lock:
        w = lab_worker.Worker(sys.executable, str(script), "@")
        try:
            assert w.ask({}, .1) is None and w.timed_out
            w.sources[str(script)] = [0, 0]
            assert not w.fresh()
        finally:
            w.kill()


@pytest.mark.parametrize("kind", ["plot", "waveform"])
def test_idle_callback_before_clock_deadline_is_rescheduled(monkeypatch, kind):
    timers = []
    clock = SimpleNamespace(now=100.)
    killed = []

    def timer(delay, callback):
        scheduled = SimpleNamespace(delay=delay, callback=callback, daemon=False,
                                    cancel=lambda: None, start=lambda: None)
        timers.append(scheduled)
        return scheduled

    cls = lab_worker.Worker if kind == "plot" else lab_waveform._Worker
    worker = cls.__new__(cls)
    worker._idle = None
    worker.idle_s = .05
    worker.python_path = "synthetic"
    monkeypatch.setattr(lab_waveform, "WARM_IDLE_S", .05)
    monkeypatch.setattr(lab_waveform, "_retire", lambda *args: killed.append(True))
    monkeypatch.setattr(lab_worker.threading, "Timer", timer)
    monkeypatch.setattr(lab_worker.time, "monotonic", lambda: clock.now)
    monkeypatch.setattr(worker, "kill", lambda: killed.append(True))
    worker._arm_idle()
    deadline = worker._idle_deadline
    clock.now = deadline - .003
    timers[0].callback()
    assert killed == [], "A timer must honor the monotonic deadline"
    assert len(timers) == 2, "An early callback must schedule the remaining idle interval"
    assert 0 < timers[1].delay <= .0031 and worker._idle_deadline == deadline
    clock.now = deadline
    timers[1].callback()
    assert killed == [True], "The rescheduled callback must expire the worker at its deadline"


def test_worker_memory_limit_stops_process(tmp_path, monkeypatch):
    script = tmp_path / "memory.py"
    script.write_text('import time\ntime.sleep(60)\n')
    with lab_worker.machine_lock:
        w = lab_worker.Worker(sys.executable, str(script), "@")
        try:
            monkeypatch.setattr(lab_worker.psutil.Process, "memory_info", lambda self:
                                SimpleNamespace(rss=3 * 1024**3))
            assert w.ask({}, .2) is None
            assert w.proc.poll() is not None
        finally:
            w.kill()


def test_slot_retires_worker_in_another_parent(tmp_path):
    import os
    import subprocess
    from pathlib import Path
    script = tmp_path / "waiting.py"
    script.write_text('import time\ntime.sleep(60)\n')
    with lab_worker.machine_lock:
        w = lab_worker.Worker(sys.executable, str(script), "@")
    code = (
        'import sys\nfrom quam_state_manager.core import lab_worker\n'
        'with lab_worker.machine_lock:\n'
        ' w=lab_worker.Worker(sys.executable,sys.argv[1],"@")\n'
        ' w.kill()\n'
    )
    try:
        proc = subprocess.run([sys.executable, "-B", "-c", code, str(script)],
                              cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15,
                              env=dict(os.environ, TMPDIR=str(tmp_path), TMP=str(tmp_path), TEMP=str(tmp_path)))
        assert proc.returncode == 0, proc.stderr
        assert w.proc.poll() is not None
    finally:
        w.kill()


def test_slot_directory_is_movable():
    import subprocess
    import sys
    env = dict(os.environ, QSM_LAB_WORKER_DIR=os.path.join("x", "slot"))
    out = subprocess.run([sys.executable, "-c", "from quam_state_manager.core import lab_worker as w;"
                          "print(w._RECORD.parent, w.machine_lock.path.parent)"],
                         capture_output=True, text=True, env=env, check=True).stdout.split()
    assert out == [os.path.join("x", "slot")] * 2


def test_worker_retirement_stops_child_processes(tmp_path):
    import psutil
    script = tmp_path / "children.py"
    script.write_text('import sys,json,subprocess\n'
                      'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"])\n'
                      'for line in sys.stdin:\n'
                      ' print("@"+json.dumps({"status":"ok","child":p.pid}),flush=True)\n')
    child = None
    with lab_worker.machine_lock:
        worker = lab_worker.Worker(sys.executable, str(script), "@")
        try:
            answer = worker.ask({}, 5)
            assert answer and answer.get("child")
            child = psutil.Process(answer["child"])
            lab_worker.retire_except()
            assert not child.is_running(), "Retiring the slot must stop plotting helpers too"
        finally:
            worker.kill()
            if child is not None and child.is_running():
                child.kill()
                child.wait(timeout=5)


def test_expired_idle_callback_cannot_retire_a_recently_used_worker(monkeypatch):
    worker = lab_worker.Worker.__new__(lab_worker.Worker)
    worker._idle = None
    worker.idle_s = 3600
    killed = []
    monkeypatch.setattr(worker, "kill", lambda: killed.append(True))
    worker._arm_idle()
    try:
        # A cancelled callback already waiting for the machine lock can still run.
        worker._expire()
        assert killed == [], "An old callback must honor the renewed idle deadline"
        worker._idle_deadline = time.monotonic() - 1
        worker._expire()
        assert killed == [True]
    finally:
        worker._idle.cancel()


def test_cold_replay_stops_at_memory_limit(tmp_path, monkeypatch):
    script = tmp_path / "cold.py"
    script.write_text('import time\ntime.sleep(60)\n')
    observed = []
    real_popen = __import__("subprocess").Popen

    def spawn(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        observed.append(proc)
        return proc

    monkeypatch.setattr("subprocess.Popen", spawn)
    monkeypatch.setattr(lab_worker.psutil.Process, "memory_info", lambda self:
                        SimpleNamespace(rss=3 * 1024**3))
    error = None
    try:
        lab_worker.run([sys.executable, str(script)], timeout=.3, capture_output=True, text=True)
    except OSError as exc:
        error = str(exc)
    except __import__("subprocess").TimeoutExpired:
        pass
    assert error and "memory limit" in error
    assert len(observed) == 1 and observed[0].poll() is not None


def test_cold_replay_returns_output_and_timeout_stops_process():
    import subprocess
    import pytest
    result = lab_worker.run([sys.executable, "-c", "print('complete')"],
                            timeout=5, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "complete"
    with pytest.raises(subprocess.TimeoutExpired):
        lab_worker.run([sys.executable, "-c", "import time; time.sleep(60)"],
                       timeout=.1, capture_output=True, text=True)


def test_waveform_expired_callback_honors_renewed_deadline(monkeypatch):
    worker = lab_waveform._Worker.__new__(lab_waveform._Worker)
    worker._idle = None
    worker.python_path = "synthetic"
    retired = []
    monkeypatch.setattr(lab_waveform, "_retire", lambda *args: retired.append(args))
    worker._arm_idle()
    try:
        worker._expire()
        assert retired == []
        worker._idle_deadline = time.monotonic() - 1
        worker._expire()
        assert retired == [(worker.python_path, worker)]
    finally:
        worker._idle.cancel()


def test_registration_failure_does_not_leak_a_worker(tmp_path, monkeypatch):
    script = tmp_path / "registration.py"
    script.write_text('import time\ntime.sleep(60)\n')
    spawned = []

    def refuse(worker):
        spawned.append(worker.proc)
        raise OSError("Cannot persist worker ownership")

    monkeypatch.setattr(lab_worker, "register", refuse)
    with lab_worker.machine_lock:
        try:
            lab_worker.Worker(sys.executable, str(script), "@")
        except OSError:
            pass
        try:
            assert len(spawned) == 1 and spawned[0].poll() is not None
        finally:
            for proc in spawned:
                proc.kill()
                proc.wait(timeout=5)


def test_cold_waveform_obeys_the_memory_limit(tmp_path, monkeypatch):
    script = tmp_path / "cold_waveform.py"
    script.write_text('import time\ntime.sleep(60)\n')
    monkeypatch.setattr("quam_state_manager.core.config_generator._script_path", lambda name: script)
    monkeypatch.setattr(lab_waveform, "TIMEOUT_S", .3)
    monkeypatch.setattr(lab_worker.psutil.Process, "memory_info", lambda self:
                        SimpleNamespace(rss=3 * 1024**3))
    with lab_worker.machine_lock:
        result = lab_waveform._run_cold(sys.executable, [])
    assert result.get("ok") is False and "memory limit" in result.get("error", "")


def test_cold_waveform_preserves_result_file_protocol(tmp_path, monkeypatch):
    script = tmp_path / "cold_waveform.py"
    script.write_text('import sys,json\nfrom pathlib import Path\n'
                      'Path(sys.argv[sys.argv.index("--out")+1]).write_text('
                      'json.dumps({"status":"ok","items":[{"value":4}],"sources":{}}))\n')
    monkeypatch.setattr("quam_state_manager.core.config_generator._script_path", lambda name: script)
    with lab_worker.machine_lock:
        result = lab_waveform._run_cold(sys.executable, [])
    assert result == {"ok": True, "error": None, "items": [{"value": 4}], "sources": {}}


def test_retirement_keeps_the_original_process_identity(monkeypatch):
    stopped = []
    original = SimpleNamespace(children=lambda **kwargs: [], kill=lambda: stopped.append("original"))
    reused = SimpleNamespace(children=lambda **kwargs: [], kill=lambda: stopped.append("reused"))
    worker = lab_worker.Worker.__new__(lab_worker.Worker)
    worker._idle = None
    worker._process = original
    worker.proc = SimpleNamespace(pid=1234, stdin=io.StringIO(), wait=lambda **kwargs: None,
                                  kill=lambda: stopped.append("handle"))
    monkeypatch.setattr(lab_worker.psutil, "Process", lambda pid: reused)
    worker.kill()
    assert stopped == ["original"], "A retired worker PID must not identify a later process"


def test_cold_waveform_reports_decode_failures(tmp_path, monkeypatch):
    script = tmp_path / "encoding.py"
    script.write_text('pass\n')
    monkeypatch.setattr("quam_state_manager.core.config_generator._script_path", lambda name: script)
    def decoding_failure(*args, **kwargs):
        raise UnicodeDecodeError("utf-8", bytes([255]), 0, 1, "invalid byte")
    monkeypatch.setattr(lab_worker, "run", decoding_failure)
    result = None
    with lab_worker.machine_lock:
        try:
            result = lab_waveform._run_cold(sys.executable, [])
        except UnicodeError:
            pass
    assert result is not None and result.get("ok") is False
    assert "decode" in result.get("error", "")
