"""Synthetic pins for the shared lab worker slot and line protocol."""
import io
import os
import json
import sys
import time
from types import SimpleNamespace

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
        for _ in range(100):
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


def test_worker_memory_limit_stops_process(tmp_path, monkeypatch):
    script = tmp_path / "memory.py"
    script.write_text('import time\ntime.sleep(60)\n')
    with lab_worker.machine_lock:
        w = lab_worker.Worker(sys.executable, str(script), "@")
        try:
            monkeypatch.setattr(lab_worker.psutil, "Process", lambda pid: SimpleNamespace(
                memory_info=lambda: SimpleNamespace(rss=3 * 1024**3)))
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
