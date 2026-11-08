"""FB-12 port test, hardware safety: busy check, halt/close, recovery, the
test config's port footprint, the one-time hardware confirmation and the
simulator loopback. No real OPX is ever contacted: ``qm`` is a fake module."""
from __future__ import annotations

import importlib.util
import io
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

from quam_state_manager.core import port_test as pt
from quam_state_manager.core import port_test_runs as ptr
from quam_state_manager.generator import run_port_test as runner
from tests.test_port_test import H, _FakeProc, _config, client, kr  # noqa: F401 - fixtures


# --------------------------------------------------------------------------- fake qm

class FakeQua:
    """Records the QUA calls a program makes (enough for build_program)."""
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def f(*a, **k):
            self.calls.append(name)
            return _Ctx()
        return f


class _Ctx:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def __mul__(self, o): return self
    def __rmul__(self, o): return self
    def __lt__(self, o): return self
    def __add__(self, o): return self
    def __getattr__(self, n): return lambda *a, **k: _Ctx()


@pytest.fixture
def fake_qm(monkeypatch):
    qua = FakeQua()
    qm = types.ModuleType("qm")
    qm.QuantumMachinesManager = object
    qm.SimulationConfig = lambda **k: k
    quam = types.ModuleType("qm.qua")
    for n in ("program", "play", "measure", "wait", "infinite_loop_", "for_", "declare",
              "declare_stream", "stream_processing", "amp"):
        setattr(quam, n, getattr(qua, n))
    sim = types.ModuleType("qm.simulate")
    sim.LoopbackInterface = lambda conns, **k: ("loopback", conns)
    monkeypatch.setitem(sys.modules, "qm", qm)
    monkeypatch.setitem(sys.modules, "qm.qua", quam)
    monkeypatch.setitem(sys.modules, "qm.simulate", sim)
    return qua


def script_module(tmp_path, test="cw", element="q1.xy", basis="chip", **kw):
    cfg, plan = pt.build(_config(), element, test, basis, **kw)
    p = tmp_path / f"s_{test}_{basis}.py"
    p.write_text(pt.render_script(cfg, plan), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("pt_script_" + test, p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.CALL_TIMEOUT_S = 0.5
    return mod, p


class Job:
    def __init__(self, status="Done", jid="j1"):
        self.status, self.id = status, jid
        self.halted = 0

    def halt(self):
        self.halted += 1


class QmV2:
    """The v2 API: get_jobs() returns JobData whose status is 'In queue'/'Running'/..."""
    def __init__(self, qid, jobs=(), config=None):
        self.id, self.jobs, self.config = qid, list(jobs), config or {"controllers": {}}
        self.closed = 0

    def get_jobs(self):
        return self.jobs

    def get_config(self):
        return self.config

    def close(self):
        self.closed += 1


class _Queue:
    def __init__(self, jobs):
        self.pending_jobs = tuple(jobs)


class QmV1:
    """The legacy API: no get_jobs; a queue plus get_running_job; status strings
    'pending'/'running'/'loading'/'completed'."""
    def __init__(self, qid, pending=(), running=None):
        self.id, self.queue, self.running = qid, _Queue(pending), running
        self.closed = 0

    def get_running_job(self):
        return self.running

    def get_config(self):
        return {"controllers": {}}

    def close(self):
        self.closed += 1


class Qmm:
    def __init__(self, qms=(), hang_open=False):
        self.qms = {q.id: q for q in qms}
        self.opened = []
        self.hang_open = hang_open

    def list_open_qms(self):
        return list(self.qms)

    def get_qm(self, qid):
        return self.qms[qid]

    def open_qm(self, config, close_other_machines=True):
        assert close_other_machines is False, "never close another user's QM"
        if self.hang_open:
            time.sleep(2.0)
        q = QmV2("ours")
        q.execute = lambda prog: Job("Running", "our-job")
        self.opened.append(q)
        return q


# --------------------------------------------------------------------------- busy check

class TestBusyCheck:
    @pytest.mark.parametrize("status", ["In queue", "Running", "Processing", "running", "pending",
                                        "loading", "unknown", "Something new", None])
    def test_v2_every_non_terminal_status_is_busy(self, tmp_path, fake_qm, status):
        mod, _ = script_module(tmp_path)
        qmm = Qmm([QmV2("other", [Job(status)])])
        assert mod.busy_jobs(qmm) == [("other", "j1")]
        with pytest.raises(RuntimeError, match="busy"):
            mod.run_on_hardware(qmm, max_s=1)
        assert not qmm.opened

    @pytest.mark.parametrize("status", ["Done", "Canceled", "Error", "completed", "cancelled",
                                        "FAILED", " done "])
    def test_v2_terminal_statuses_are_idle(self, tmp_path, fake_qm, status):
        mod, _ = script_module(tmp_path)
        assert mod.busy_jobs(Qmm([QmV2("other", [Job(status)])])) == []

    @pytest.mark.parametrize("pending,running,busy", [
        ([], None, False), ([Job("pending")], None, True), ([], Job("running"), True),
        ([], Job("loading"), True), ([], Job("completed"), False)])
    def test_v1_queue_and_running_job(self, tmp_path, fake_qm, pending, running, busy):
        mod, _ = script_module(tmp_path)
        assert bool(mod.busy_jobs(Qmm([QmV1("old", pending, running)]))) is busy

    def test_a_callable_status_is_read(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        j = Job()
        j.status = lambda: "Running"
        assert mod.busy_jobs(Qmm([QmV2("x", [j])]))

    def test_a_failed_check_refuses(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        qmm = Qmm()
        qmm.list_open_qms = lambda: (_ for _ in ()).throw(ConnectionError("no answer"))
        with pytest.raises(RuntimeError, match="Could not check"):
            mod.run_on_hardware(qmm, max_s=1)
        assert not qmm.opened

    def test_another_qm_holding_one_of_our_ports_refuses(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)  # q1.xy: con1 / fem 1 / out 2
        holder = QmV2("peer", [], {"controllers": {"con1": {"fems": {"1": {"analog_outputs": {"2": {}}}}}}})
        other = QmV2("far", [], {"controllers": {"con1": {"fems": {"1": {"analog_outputs": {"1": {}}}}}}})
        qmm = Qmm([holder, other])
        assert mod.busy_jobs(qmm) == [("peer", "ports reserved")]
        with pytest.raises(RuntimeError, match="busy"):
            mod.run_on_hardware(qmm, max_s=1)
        assert holder.closed == 0 and other.closed == 0


# --------------------------------------------------------------------------- halt / close

class TestHaltClose:
    def test_a_run_records_ids_halts_and_closes_only_its_own_qm(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        peer = QmV2("peer", [Job("Done")])
        qmm = Qmm([peer])
        rec = {}
        jobs = []
        stop = threading.Event()

        def execute(prog):
            jobs.append(Job("Running", "our-job"))
            stop.set()
            return jobs[-1]
        orig = qmm.open_qm

        def open_qm(c, close_other_machines=True):
            q = orig(c, close_other_machines)
            q.execute = execute
            return q
        qmm.open_qm = open_qm
        mod.run_on_hardware(qmm, should_stop=stop.is_set, max_s=5, record=lambda **k: rec.update(k))
        ours = qmm.opened[0]
        assert rec["qm_id"] == "ours" and rec["job_id"] == "our-job" and rec["cleaned"] is True
        assert jobs[0].halted == 1 and ours.closed == 1, "halt + close exactly once"
        assert peer.closed == 0

    def test_the_deadline_halts_a_cw_run(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        qmm = Qmm()
        t0 = time.monotonic()
        mod.run_on_hardware(qmm, max_s=0.5)
        assert time.monotonic() - t0 < 3 and qmm.opened[0].closed == 1

    def test_a_bad_duration_is_refused(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        for bad in (0, -1, 1e9, float("nan")):
            with pytest.raises(ValueError):
                mod.run_on_hardware(Qmm(), max_s=bad)

    def test_a_stuck_open_qm_times_out_and_the_late_qm_is_closed(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        qmm = Qmm(hang_open=True)
        with pytest.raises(TimeoutError):
            mod.run_on_hardware(qmm, max_s=5)
        deadline = time.time() + 5
        while time.time() < deadline and not (qmm.opened and qmm.opened[0].closed):
            time.sleep(0.05)
        assert qmm.opened[0].closed == 1, "a QM that opened late is closed, not leaked"

    def test_a_failing_halt_still_closes(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        q = QmV2("ours")
        j = Job()
        j.halt = lambda: (_ for _ in ()).throw(RuntimeError("halt failed"))
        with pytest.raises(RuntimeError):
            mod.halt_close(q, j)
        assert q.closed == 1

    def test_the_cli_is_bounded_and_stops_through_its_finally(self, tmp_path, fake_qm):
        _mod, p = script_module(tmp_path)
        text = p.read_text(encoding="utf-8")
        assert 'add_argument("--seconds", type=float, default=120.0' in text
        assert "signal.signal(signal.SIGTERM, stop_signal)" in text
        assert "QuantumMachinesManager(timeout=CALL_TIMEOUT_S" in text
        handler = text[text.index("def stop_signal"):text.index("signal.signal(signal.SIGINT")]
        assert "raise" not in handler, "a signal only flags the stop; the run's finally halts + closes"


# --------------------------------------------------------------------------- the runner helper

class TestRunnerLifetime:
    def test_end_of_file_on_stdin_stops_the_run(self):
        ev = threading.Event()
        runner.watch_stdin(io.StringIO(""), ev).join(2)
        assert ev.is_set()

    def test_a_stop_line_stops_the_run(self):
        r, w = __import__("os").pipe()
        stream = open(r, "r")
        ev = threading.Event()
        th = runner.watch_stdin(stream, ev)
        time.sleep(0.2)
        assert not ev.is_set()
        __import__("os").write(w, b"stop\n")
        th.join(2)
        assert ev.is_set()
        __import__("os").close(w)

    def test_recover_closes_only_the_recorded_qm(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        ours, peer = QmV2("ours"), QmV2("peer")
        job = Job("Running", "jx")
        ours.get_job = lambda jid: job if jid == "jx" else None
        runner.recover(mod, Qmm([ours, peer]), {"qm_id": "ours", "job_id": "jx"})
        assert job.halted == 1 and ours.closed == 1 and peer.closed == 0

    def test_recover_with_no_qm_opened_is_a_no_op_but_opening_is_unverifiable(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        peer = QmV2("peer")
        runner.recover(mod, Qmm([peer]), {"phase": "checking for running jobs"})
        with pytest.raises(RuntimeError):
            runner.recover(mod, Qmm([peer]), {"phase": "opening a QM"})
        assert peer.closed == 0


def _hw_run(tmp_path, procs, status, cls=None):
    r = ptr.Runs(tmp_path)
    cls = cls or _Stubborn
    r.start("py", "x", {"mode": "opx", "target": {"host": "h"}}, {"target": "opx"},
            popen=lambda a, **k: procs.append(cls(a)) or procs[-1])
    (r.current["work"] / "_status.json").write_text(json.dumps(status))
    return r


class _Stubborn(_FakeProc):
    """A runner that never ends by itself."""
    def poll(self):
        return self.rc


class TestRunsNeverKillBeforeHalt:
    def test_stop_asks_over_stdin_and_the_stop_file(self, tmp_path, kr):
        procs = []
        r = _hw_run(tmp_path, procs, {"qm_id": "q7"})
        assert r.stop()
        assert procs[0].got.splitlines()[-1] == "stop"
        assert json.loads(procs[0].got.splitlines()[0])["mode"] == "opx"
        assert (r.current["work"] / "stop").exists()
        assert not getattr(procs[0], "closed", False), "stdin stays open while the run lives"

    def test_a_stalled_hardware_runner_is_not_killed_until_its_qm_is_closed(self, tmp_path, monkeypatch, kr):
        monkeypatch.setattr(ptr, "STOP_GRACE_S", 0.2)
        calls = []
        monkeypatch.setattr(ptr.Runs, "_recover", lambda self, run: calls.append(1) and False)
        procs = []
        r = _hw_run(tmp_path, procs, {"qm_id": "q7"})
        r.stop()
        time.sleep(2.5)
        assert calls, "a fresh helper was asked to halt + close"
        assert procs[0].rc is None, "never hard-killed while the QM is not confirmed closed"
        assert "not confirmed closed" in r.status()["stop_why"]
        monkeypatch.setattr(ptr.Runs, "_recover", lambda self, run: True)
        deadline = time.time() + 5
        while time.time() < deadline and procs[0].rc is None:
            time.sleep(0.1)
        assert procs[0].rc == -9

    def test_a_dead_runner_with_an_open_qm_stays_running_until_recovered(self, tmp_path, monkeypatch, kr):
        monkeypatch.setattr(ptr, "RECOVER_RETRY_S", 0.1)
        procs = []
        r = _hw_run(tmp_path, procs, {"qm_id": "q7", "phase": "running"})
        work = r.current["work"]
        tries = []

        def rec(self, run):
            tries.append(1)
            if len(tries) >= 3:
                (work / "_recovery.json").write_text(json.dumps({"ok": True}))
                return True
            return False
        monkeypatch.setattr(ptr.Runs, "_recover", rec)
        procs[0].rc = 1
        st = r.status()
        assert st["state"] == "running" and st["cleanup_pending"] and st["qm_id"] == "q7"
        deadline = time.time() + 5
        while time.time() < deadline and len(tries) < 3:
            time.sleep(0.05)
        time.sleep(0.2)
        assert len(tries) >= 3 and r.status()["state"] == "done"
        assert getattr(procs[0], "closed", False), "stdin is closed once the runner ended"

    def test_a_restarted_sm_picks_up_an_unclean_hardware_run(self, tmp_path, monkeypatch, kr):
        monkeypatch.setattr(ptr.Runs, "_recover", lambda self, run: False)
        procs = []
        r = _hw_run(tmp_path, procs, {"qm_id": "q7", "job_id": "j3"})
        rid = r.current["id"]
        r2 = ptr.Runs(tmp_path)  # a new SM process: the runner's PID is not alive
        st = r2.status()
        assert st["id"] == rid and st["state"] == "running" and st["cleanup_pending"]
        assert st["qm_id"] == "q7" and st["job_id"] == "j3" and st["resumed"]
        with pytest.raises(RuntimeError, match="not confirmed closed"):
            r2.start("py", "x", {"mode": "saas"}, {}, popen=lambda a, **k: _FakeProc(a))
        assert r2.stop(rid), "Stop on a resumed run runs the recovery helper"

    def test_a_clean_finished_run_is_not_resumed(self, tmp_path, kr):
        procs = []
        r = _hw_run(tmp_path, procs, {"qm_id": "q7", "cleaned": True})
        procs[0].rc = 0
        assert ptr.Runs(tmp_path).current is None
        assert r.status()["state"] == "done"

    def test_the_runner_request_carries_the_duration_cap(self, tmp_path, kr):
        procs = []
        _hw_run(tmp_path, procs, {})
        assert json.loads(procs[0].got.splitlines()[0])["max_s"] == ptr.MAX_RUN_S == 120.0


# --------------------------------------------------------------------------- the test config footprint

class TestFootprint:
    def test_chip_basis_claims_only_the_tested_ports(self):
        cfg, _ = pt.build(_config(), "q1.xy", "cw", "chip")
        fems = cfg["controllers"]["con1"]["fems"]
        assert set(fems) == {"1"} and set(fems["1"]) == {"type", "analog_outputs"}
        assert set(fems["1"]["analog_outputs"]) == {"2"}

    def test_chip_basis_never_carries_another_ports_dc_offset(self):
        c = _config()
        c["controllers"]["con1"]["fems"]["5"]["analog_outputs"]["1"]["offset"] = 0.3
        cfg, _ = pt.build(c, "q1.xy", "cw", "chip")
        assert "5" not in cfg["controllers"]["con1"]["fems"]

    def test_the_cap_counts_the_ports_dc_offset(self):
        c = _config()
        c["controllers"]["con1"]["fems"]["5"]["analog_outputs"]["1"]["offset"] = 0.3
        with pytest.raises(pt.PortTestError, match="DC offset 0.3"):
            pt.build(c, "q1.z", "cw", "chip", amplitude=0.25, amp_cap=0.5)
        cfg, plan = pt.build(c, "q1.z", "cw", "chip", amplitude=0.15, amp_cap=0.5)
        assert any("DC offset" in n for n in plan["notes"])
        c["pulses"]["q1.z.const.pulse"]["waveforms"]["single"] = "q1.xy.big.I"  # peak 0.9
        _, plan = pt.build(c, "q1.z", "pulse", "chip", operation="const", amp_cap=0.5)
        assert plan["expected_peak"] == pytest.approx(0.2)
        c["controllers"]["con1"]["fems"]["5"]["analog_outputs"]["1"]["offset"] = -0.6
        with pytest.raises(pt.PortTestError, match="no room"):
            pt.build(c, "q1.z", "pulse", "chip", operation="const", amp_cap=0.5)

    def test_the_marker_drives_only_the_chosen_dig_port(self):
        c = _config()
        c["elements"]["q1.xy"]["digitalInputs"] = {"switch": {"port": ["con1", 1, 7], "delay": 57, "buffer": 18}}
        c["controllers"]["con1"]["fems"]["1"]["digital_outputs"] = {"7": {}, "3": {"inverted": False}}
        cfg, plan = pt.build(c, "q1.xy", "pulse", "chip", operation="x180", marker_port=["con1", 1, 3])
        el = cfg["elements"]["q1.xy"]
        assert el["digitalInputs"] == {"port_test_trigger": {"port": ["con1", 1, 3], "delay": 0, "buffer": 0}}
        assert set(cfg["controllers"]["con1"]["fems"]["1"]["digital_outputs"]) == {"3"}
        cfg, _ = pt.build(c, "q1.xy", "cw", "chip")
        assert "digitalInputs" not in cfg["elements"]["q1.xy"]
        assert "digital_outputs" not in cfg["controllers"]["con1"]["fems"]["1"]

    def test_loopback_keeps_its_input_and_names_the_simulated_cable(self):
        cfg, plan = pt.build(_config(), "q1.resonator", "loopback", "chip")
        fem = cfg["controllers"]["con1"]["fems"]["1"]
        assert set(fem["analog_outputs"]) == {"1"} and set(fem["analog_inputs"]) == {"1"}
        assert plan["loopback"] == [["con1", 1, 1, "con1", 1, 1]]


# --------------------------------------------------------------------------- simulator loopback (D-5)

class FakeSimJob:
    def __init__(self):
        self.result_handles = types.SimpleNamespace(
            wait_for_all_values=lambda: None,
            get=lambda k: types.SimpleNamespace(fetch_all=lambda: [0.0, 0.01, 0.02]),
            __contains__=None)

    def get_simulated_samples(self):
        return "samples"


class _Handles(dict):
    def wait_for_all_values(self, *a, **k):
        return True

    def get(self, k):
        return types.SimpleNamespace(fetch_all=lambda: self[k])


class TestSimLoopback:
    def test_loopback_simulation_connects_out_to_in_and_returns_the_adc(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path, test="loopback", element="q1.resonator")
        seen = {}

        class SimQmm:
            def simulate(self, config, prog, simcfg):
                seen.update(simcfg)
                job = types.SimpleNamespace(result_handles=_Handles(adc1=[0.0, 0.02, 0.01]),
                                            get_simulated_samples=lambda: "samples")
                return job
        samples, adc = mod.simulate(SimQmm())
        assert seen["simulation_interface"] == ("loopback", [("con1", 1, 1, "con1", 1, 1)])
        assert adc == {"adc1": [0.0, 0.02, 0.01]} and samples == "samples"

    def test_a_cw_simulation_has_no_loopback(self, tmp_path, fake_qm):
        mod, _ = script_module(tmp_path)
        seen = {}

        class SimQmm:
            def simulate(self, config, prog, simcfg):
                seen.update(simcfg)
                return types.SimpleNamespace(get_simulated_samples=lambda: "s")
        assert mod.simulate(SimQmm()) == ("s", {})
        assert "simulation_interface" not in seen


# --------------------------------------------------------------------------- the one-time confirmation

def _set_opx(client):
    client.post("/port-test/settings", json={"opx": {"host": "10.0.0.1", "cluster": "c", "port": ""}}, headers=H)


RUN = {"element": "q1.xy", "test": "cw", "basis": "chip", "amp": "0.2", "target": "opx"}


def _confirm(client, body=None):
    b = dict(body or RUN)
    b.update(hardware_ok=True, opx={"host": "10.0.0.1", "cluster": "c", "port": ""})
    return client.post("/port-test/confirm", json=b, headers=H)


class TestConfirmToken:
    def test_a_hardware_run_needs_a_fresh_server_token(self, client):
        _set_opx(client)
        assert client.post("/port-test/run", json={**RUN, "hardware_ok": True}, headers=H).status_code == 400
        assert not client.started
        tok = _confirm(client).get_json()["token"]
        r = client.post("/port-test/run", json={**RUN, "confirm_token": tok}, headers=H)
        assert r.status_code == 200, r.get_json()
        assert client.started[-1]["mode"] == "opx" and client.started[-1]["max_s"] == 120.0

    def test_a_token_is_single_use(self, client):
        _set_opx(client)
        tok = _confirm(client).get_json()["token"]
        client.post("/port-test/run", json={**RUN, "confirm_token": tok}, headers=H)
        from quam_state_manager.web.port_test_routes import runs
        with client.application.app_context():
            runs().current["proc"].rc = 0
        r = client.post("/port-test/run", json={**RUN, "confirm_token": tok}, headers=H)
        assert r.status_code == 400 and len(client.started) == 1

    def test_a_token_expires(self, client, monkeypatch):
        _set_opx(client)
        tok = _confirm(client).get_json()["token"]
        real = time.monotonic
        monkeypatch.setattr("quam_state_manager.web.port_test_routes.time.monotonic", lambda: real() + 61)
        assert client.post("/port-test/run", json={**RUN, "confirm_token": tok}, headers=H).status_code == 400
        assert not client.started

    def test_a_token_is_bound_to_what_was_confirmed(self, client):
        _set_opx(client)
        tok = _confirm(client).get_json()["token"]
        r = client.post("/port-test/run", json={**RUN, "amp": "0.4", "confirm_token": tok}, headers=H)
        assert r.status_code == 400 and not client.started
        tok = _confirm(client).get_json()["token"]
        client.post("/port-test/settings", json={"opx": {"host": "10.0.0.2"}}, headers=H)
        r = client.post("/port-test/run", json={**RUN, "confirm_token": tok}, headers=H)
        assert r.status_code == 400 and not client.started

    def test_the_confirm_needs_the_tick(self, client):
        r = client.post("/port-test/confirm", json=RUN, headers=H)
        assert r.status_code == 400 and "token" not in r.get_json()


# --------------------------------------------------------------------------- Settings window (D-4)

class TestSettingsWindow:
    def test_the_settings_window_loads_the_saved_targets(self, client):
        page = client.get("/instrument").get_data(as_text=True)
        assert 'id="settings-port-targets"' in page and 'hx-get="/port-test/settings/panel"' in page

    def test_targets_are_edited_there_and_the_password_only_shows_as_saved(self, client, kr):
        r = client.post("/port-test/settings/panel", data={
            "opx_host": "10.1.1.1", "opx_cluster": "cl", "opx_port": "9510",
            "saas_email": "a@b.c", "saas_host": "s.example", "saas_version": "",
            "saas_password": "pw-panel", "do": "save"}, headers=H)
        html = r.get_data(as_text=True)
        assert r.status_code == 200 and 'value="10.1.1.1"' in html and "pw-panel" not in html
        assert "saved (OS keyring)" in html
        assert ptr.load_settings(client.application.instance_path)["opx"]["port"] == "9510"
        assert list(kr.store.values()) == ["pw-panel"]
        page = client.get("/port-test?element=q1.xy").get_data(as_text=True)
        assert 'value="10.1.1.1"' in page, "the page and the Settings window share one store"
        html = client.post("/port-test/settings/panel", data={
            "opx_host": "10.1.1.1", "saas_email": "a@b.c", "saas_host": "s.example",
            "do": "remove_password"}, headers=H).get_data(as_text=True)
        assert "not saved" in html and not kr.store

    def test_without_a_keyring_the_window_says_so_and_saves_nothing(self, client, monkeypatch):
        monkeypatch.setattr(ptr, "_keyring", lambda: None)
        html = client.post("/port-test/settings/panel", data={
            "saas_email": "a@b.c", "saas_host": "s", "saas_password": "pw-x", "do": "save"},
            headers=H).get_data(as_text=True)
        assert 'id="pt-tg-nokeyring"' in html and "cannot be saved" in html
        for p in Path(client.application.instance_path).rglob("*"):
            assert not p.is_file() or b"pw-x" not in p.read_bytes()

    def test_the_json_settings_door_refuses_a_password_without_a_keyring(self, client, monkeypatch):
        monkeypatch.setattr(ptr, "_keyring", lambda: None)
        r = client.post("/port-test/settings", json={"saas": {"email": "a@b.c", "host": "h"},
                                                     "password": "pw-y"}, headers=H)
        assert r.status_code == 400 and "keyring" in r.get_json()["error"]


def test_the_runner_answers_while_its_stdin_stays_open(tmp_path):
    """A real runner process with stdin held open must still import and answer
    (a reader thread on the pipe used to hang later imports on Windows)."""
    import subprocess
    (tmp_path / "script.py").write_text("import json\nCALL_TIMEOUT_S = 1\n", encoding="utf-8")
    p = subprocess.Popen([sys.executable, str(Path(runner.__file__)), str(tmp_path / "script.py"), str(tmp_path)],
                         stdin=subprocess.PIPE, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        p.stdin.write(json.dumps({"mode": "nope"}) + "\n")
        p.stdin.flush()
        deadline = time.time() + 45
        while time.time() < deadline and not (tmp_path / "_result.json").exists():
            time.sleep(0.2)
        assert "unknown mode" in (tmp_path / "_result.json").read_text(encoding="utf-8")
    finally:
        p.stdin.close()
        p.wait(30)
