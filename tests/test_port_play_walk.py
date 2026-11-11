"""Port play walk fixes: a stop asked while connecting ends at once as "stopped",
and a Simulate press runs the same program on a simulator (no hardware, no
safety box). No real QM or simulator is contacted."""
from __future__ import annotations

import json
import sys
import time
import types

import pytest

from quam_state_manager.core import port_test_runs as ptr
from quam_state_manager.generator import run_port_test as runner
from tests.test_port_play import _app, spec  # noqa: F401 - fixture
from tests.test_port_test import _FakeProc, kr  # noqa: F401 - fixture


class _Stuck(_FakeProc):
    """A runner blocked inside connect: it never ends by itself."""

    def poll(self):
        return self.rc


def _run(tmp_path, status, target="opx"):
    procs = []
    r = ptr.Runs(tmp_path, resume=False)
    mode = "opx" if target == "opx" else target
    r.start("py", "x", {"mode": mode, "target": {"host": "h"}}, {"target": target},
            popen=lambda a, **k: procs.append(_Stuck(a)) or procs[-1])
    (r.current["work"] / "_status.json").write_text(json.dumps(status))
    return r, procs


def _wait(cond, s=4.0):
    end = time.time() + s
    while time.time() < end and not cond():
        time.sleep(0.05)
    return cond()


class TestStopWhileConnecting:
    def test_a_stop_before_any_qm_ends_the_runner_at_once_as_stopped(self, tmp_path, kr):
        r, procs = _run(tmp_path, {"phase": "connecting"})
        t0 = time.time()
        assert r.stop(why="Stop pressed")
        assert _wait(lambda: procs[0].rc is not None), "killed promptly, not after the grace"
        assert time.time() - t0 < ptr.STOP_GRACE_S
        st = r.status()
        assert st["state"] == "done" and st["result"]["stopped"] is True and not st["result"]["ok"]

    def test_an_opened_qm_is_never_cut_short(self, tmp_path, kr, monkeypatch):
        monkeypatch.setattr(ptr.Runs, "_recover", lambda self, run: False)
        r, procs = _run(tmp_path, {"phase": "running", "qm_id": "q1"})
        r.stop()
        time.sleep(1.6)
        assert procs[0].rc is None

    def test_a_cloud_simulator_is_never_cut_short(self, tmp_path, kr):
        r, procs = _run(tmp_path, {"phase": "connecting"}, target="saas")
        r.stop()
        time.sleep(1.6)
        assert procs[0].rc is None

    def test_a_stopped_run_that_raised_reads_stopped(self, tmp_path, kr):
        r, procs = _run(tmp_path, {"phase": "running", "qm_id": "q1"})
        work = r.current["work"]
        r.current["stop_at"] = time.time()
        (work / "_status.json").write_text(json.dumps({"phase": "done", "cleaned": True}))
        (work / "_result.json").write_text(json.dumps({"ok": False, "error": "TimeoutError: QM API call timed out"}))
        procs[0].rc = 0
        assert r.status()["result"]["stopped"] is True

    def test_an_unstopped_failure_stays_a_failure(self, tmp_path, kr):
        r, procs = _run(tmp_path, {"phase": "running"})
        work = r.current["work"]
        (work / "_result.json").write_text(json.dumps({"ok": False, "error": "boom"}))
        procs[0].rc = 1
        assert "stopped" not in r.status()["result"]


class _Samples:
    def __init__(self):
        self.con1 = types.SimpleNamespace(analog={"1-1": [0.0, 0.1, 0.1]}, digital={}, analog_sampling_rate=None)


def _mod(calls):
    def simulate(qmm, ns=None):
        calls.append(("simulate", qmm))
        return _Samples(), {}
    return types.SimpleNamespace(simulate=simulate, EXPECTED_PEAK=0.1, CALL_TIMEOUT_S=1.0,
                                 bounded=lambda call, timeout=None: call(),
                                 saas_qmm=lambda client, ver: (types.SimpleNamespace(close=lambda: None), "cloud"))


class TestRunSim:
    def test_the_local_simulator_is_used_when_it_answers(self, monkeypatch):
        calls = []
        monkeypatch.setattr(runner, "connection", lambda mod, t: "local-qmm")
        res = runner.run_sim(_mod(calls), {"local": {"host": "h"}, "saas": None}, {}, lambda **k: None, lambda: False)
        assert res["simulated"] is True and res["backend"] == "local"
        assert calls == [("simulate", "local-qmm")]
        assert res["traces"]["con1:1-1"]["peak"] == pytest.approx(0.1)

    def test_an_unreachable_qop_falls_back_to_the_cloud(self, monkeypatch):
        def refuse(mod, t):
            raise ConnectionError("no QOP")
        monkeypatch.setattr(runner, "connection", refuse)
        fake = types.ModuleType("qm_saas")
        fake.QmSaas = lambda **kw: "client"
        fake.QOPVersion = str
        monkeypatch.setitem(sys.modules, "qm_saas", fake)
        calls = []
        res = runner.run_sim(_mod(calls), {"local": {"host": "h"},
                                           "saas": {"email": "e", "host": "s", "password": "pw"}},
                             {}, lambda **k: None, lambda: False)
        assert res["backend"] == "saas" and res["simulated"] is True
        assert calls == [("simulate", "cloud")] and "no QOP" in res["local_error"]

    def test_no_simulator_says_so(self, monkeypatch):
        with pytest.raises(RuntimeError, match="No simulator"):
            runner.run_sim(_mod([]), {"local": None, "saas": None}, {}, lambda **k: None, lambda: False)


class TestSimRoute:
    def test_simulate_needs_no_safety_box_and_never_drives_hardware(self, spec, tmp_path, monkeypatch, kr):
        app, manager = _app(tmp_path, monkeypatch)
        monkeypatch.setenv("SAAS_EMAIL", "e@x")
        monkeypatch.setenv("SAAS_PASSWORD", "secret-pw")
        monkeypatch.setenv("SAAS_HOST", "saas.example")
        captured = {}

        def start(python, script, request, meta):
            captured.update(script=script, request=request, meta=meta)
            return {"id": "sim1"}
        monkeypatch.setattr(manager, "start", start)
        body = dict(spec=spec, python=sys.executable, con="con2", slot=3, port=5, offset=.1, max_s=12, action="sim")
        client = app.test_client()
        c = client.post("/port-play/confirm", json=body)
        assert c.status_code == 200, c.json
        r = client.post("/port-play/run", json=dict(body, confirm_token=c.json["token"]))
        assert r.status_code == 200 and r.json["simulated"] is True, r.json
        req = captured["request"]
        assert req["mode"] == "sim" and "host" not in req["target"]
        assert req["target"]["local"]["host"] == "scope-host"
        assert req["target"]["saas"]["password"] == "secret-pw"
        assert "secret-pw" not in json.dumps(captured["meta"]) and "secret-pw" not in captured["script"]
        assert captured["meta"]["target"] == "sim" and captured["meta"]["plan"]["simulated"] is True
        # a hardware play still needs the box
        assert client.post("/port-play/confirm", json=dict(body, action="play")).status_code == 400

    def test_a_sim_token_does_not_authorize_a_hardware_play(self, spec, tmp_path, monkeypatch, kr):
        app, manager = _app(tmp_path, monkeypatch)
        monkeypatch.setattr(manager, "start", lambda *a: pytest.fail("must not start"))
        body = dict(spec=spec, python=sys.executable, con="con2", slot=3, port=5, offset=.1, max_s=12, action="sim")
        client = app.test_client()
        tok = client.post("/port-play/confirm", json=body).json["token"]
        r = client.post("/port-play/run", json=dict(body, action="play", hardware_ok=True, confirm_token=tok))
        assert r.status_code == 400


def test_walk_selfcheck_jsdom():
    """F1-F9 on the client, through the real port-play.js + generate.js."""
    import os
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH", "D:/work_laptop/statemanager-cfb/node_modules"))
    r = subprocess.run(["node", str(root / "tests" / "port_play_walk_selfcheck.cjs")],
                       capture_output=True, text=True, env=env, timeout=120)
    if "Cannot find module 'jsdom'" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "port play walk selfcheck ok" in r.stdout
