"""run_node (docs/173 S5): the gates as data, the scratch copy, the writes
through the door or into an approval, the record, the lock.

The chassis is real (queue, worker thread, start/cancel, heartbeat); only
``scheduler._run_item`` -- the subprocess -- is a fake that edits the item's
scratch state the way a qualibrate node would (``machine.save()`` onto
QUAM_STATE_PATH). Everything else is the shipped code.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import agent_runs, agent_session, approvals, limits, scheduler
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import agent_api as aa
from quam_state_manager.web.app import create_app

NODE_SRC = '''"""A power-rabi-like node."""
from qualibrate import QualibrationNode

class Parameters(NodeParameters):
    num_shots: int = 100

node = QualibrationNode[Parameters, Quam](name="05_power_rabi", parameters=Parameters())

@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    pass
'''
GRAPH_SRC = '''"""A graph."""
from qualibrate import QualibrationGraph
g = QualibrationGraph(name="graph_x", parameters=Parameters(), nodes={})
g.run()
'''
HOOKLESS_SRC = '''"""A utility node with no custom_param."""
from qualibrate import QualibrationNode
node = QualibrationNode[Parameters, Quam](name="util_no_hook", parameters=Parameters())
'''
AGENT = {"X-SM-Agent": "claude"}
HUMAN = {"X-SM-Actor": "kyunghoon"}


def _wait(pred, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return pred()


# ---------------------------------------------------------------- fixtures

@pytest.fixture
def inst(tmp_path):
    return tmp_path / "_app_instance"


@pytest.fixture
def app(inst):
    return create_app(testing=True, instance_path=str(inst))


@pytest.fixture
def synth_folder(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def cal(tmp_path):
    d = tmp_path / "calibrations"
    d.mkdir()
    (d / "05_power_rabi.py").write_text(NODE_SRC, encoding="utf-8")
    (d / "graph_x.py").write_text(GRAPH_SRC, encoding="utf-8")
    (d / "util_no_hook.py").write_text(HOOKLESS_SRC, encoding="utf-8")
    return d


@pytest.fixture
def c(app, synth_folder, cal, inst):
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    with app.app_context():
        from quam_state_manager.web import routes as r
        # env/folder/timeout are machine-wide (_shared.json); Dry run is per chip scope (docs/80)
        scheduler.save_settings(r._sched_inst(), {"env_python": sys.executable, "calibrations_folder": str(cal),
                                                  "global_simulate": False, "default_timeout_s": 60})
    yield client
    reg = app.config.get("agent_run_registry")
    scope = None
    with app.app_context():
        from quam_state_manager.web import routes as r
        try:
            scope = r._sched_inst()
        except Exception:  # noqa: BLE001
            pass
    if scope:
        try:
            scheduler.cancel(scope)
        except Exception:  # noqa: BLE001
            pass
    if reg:
        for m in reg.runs.values():
            reg.wait(m["key"], 15)


def _chip(c):
    return c.get("/api/agent/chip").get_json()["name"]


def _journal(c, inst):
    return journal_mod.read(str(inst), _chip(c), datetime.now().strftime("%Y-%m-%d")) or ""


def _arm(c):
    r = c.post("/api/agent/session/arm", json={}, headers=HUMAN)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


class FakeRun:
    """Stands in for the node subprocess: edits qubits.qA1.f_01 on the item's
    scratch state (+1 MHz), optionally lingers until cancel, or fails."""

    def __init__(self, *, delta=1e6, sleep=0.0, fail=None, until_cancel=False, extra=None):
        self.delta, self.sleep, self.fail, self.until_cancel, self.extra = delta, sleep, fail, until_cancel, extra
        self.calls: list[dict] = []

    def __call__(self, instance_path, item, settings, runner):
        self.calls.append(dict(item))
        sp = item.get("state_path")
        t0 = time.time()
        while self.until_cancel and not runner["cancel"].is_set() and time.time() - t0 < 30:
            time.sleep(0.05)
        if self.sleep:
            time.sleep(self.sleep)
        if runner["cancel"].is_set():
            return {"status": "cancelled", "error": "cancelled by user", "returncode": None, "log_file": None}
        if self.fail:
            return {"status": "failed", "error": self.fail, "returncode": 1, "log_file": None}
        if sp and self.delta:
            p = Path(sp) / "state.json"
            st = json.loads(p.read_text(encoding="utf-8"))
            st["qubits"]["qA1"]["f_01"] = st["qubits"]["qA1"]["f_01"] + self.delta
            if self.extra:
                for k, v in self.extra.items():
                    st["qubits"]["qA1"][k] = v
            p.write_text(json.dumps(st), encoding="utf-8")
        return {"status": "done", "error": None, "returncode": 0, "log_file": None}


@pytest.fixture
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(scheduler, "_run_item", fr)
    return fr


def _run(c, **kw):
    body = {"node": "05_power_rabi", "targets": ["qA1"], "reason": "verify the pin", "wait_s": 20}
    body.update(kw)
    return c.post("/api/agent/run-node", json=body, headers=AGENT)


# ------------------------------------------------------------------ pure

class TestPure:
    def test_diff_states_changed_created_deleted_nan(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(), b.mkdir()
        (a / "state.json").write_text(json.dumps({"q": {"f": 1.0, "gone": 2, "nan": float("nan"), "same": [1, 2]},
                                                  "w": "#/x"}), encoding="utf-8")
        (a / "wiring.json").write_text(json.dumps({"wiring": {"p": 1}}), encoding="utf-8")
        (b / "state.json").write_text(json.dumps({"q": {"f": 1.5, "new": 3, "nan": float("nan"), "same": [1, 2]},
                                                  "w": "#/y"}), encoding="utf-8")
        (b / "wiring.json").write_text(json.dumps({"wiring": {"p": 2}}), encoding="utf-8")
        writes, trunc = agent_runs.diff_states(a, b)
        by = {w["path"]: w for w in writes}
        assert not trunc
        assert by["q.f"] == {"path": "q.f", "old": 1.0, "new": 1.5}
        assert by["q.new"].get("created") and by["q.gone"].get("deleted")
        assert "q.nan" not in by and "q.same.0" not in by
        assert by["w"]["new"] == "#/y" and by["wiring.p"]["new"] == 2

    def test_diff_states_type_change_counts(self, tmp_path):
        a, b = tmp_path / "a", tmp_path / "b"
        a.mkdir(), b.mkdir()
        (a / "state.json").write_text(json.dumps({"x": 1}), encoding="utf-8")
        (b / "state.json").write_text(json.dumps({"x": 1.0}), encoding="utf-8")
        writes, _ = agent_runs.diff_states(a, b)
        assert [w["path"] for w in writes] == ["x"]

    def test_classify(self):
        assert agent_runs.classify("done", None, "") == "ok"
        assert agent_runs.classify("cancelled", "cancelled by user", "") == "cancelled"
        assert agent_runs.classify("skipped", "dry", "") == "skipped"
        assert agent_runs.classify("failed", "boom", "Traceback ...\nFailed to connect to QM at 10.0.0.1") == "hardware_contention"
        assert agent_runs.classify("failed", "Error: connection refused", "") == "hardware_contention"
        assert agent_runs.classify("failed", "timed out after 10s", "") == "timeout"
        assert agent_runs.classify("failed", "ValueError: fit", "") == "node_error"

    def test_resolve_node_by_name_stem_prefix(self, cal, inst):
        info, avail = agent_runs.resolve_node(str(cal), "05_power_rabi")
        assert info and info.name == "05_power_rabi" and Path(info.file).name == "05_power_rabi.py"
        assert agent_runs.resolve_node(str(cal), "05_power_rabi.py")[0].name == "05_power_rabi"
        assert agent_runs.resolve_node(str(cal), "power_rabi")[0] is None, "a bare family is not a file name"
        assert agent_runs.resolve_node(str(cal), "05_power")[0].name == "05_power_rabi", "a unique prefix"
        assert agent_runs.resolve_node(str(cal), "nope")[0] is None
        assert {i.name for i in avail} >= {"05_power_rabi", "graph_x", "util_no_hook"}
        assert agent_runs.resolve_node(None, "x") == (None, [])

    def test_limits_hold(self):
        lim = dict(limits.DEFAULTS, max_delta={"power_rabi": 0.5}, max_writes_per_plan=3)
        w = [{"path": "a", "old": 1.0, "new": 1.2}]
        assert agent_runs.limits_hold(lim, "power_rabi", w, 0) is None
        assert "max_delta" in agent_runs.limits_hold(lim, "power_rabi", [{"path": "a", "old": 1.0, "new": 2.0}], 0)
        assert agent_runs.limits_hold(lim, "other", [{"path": "a", "old": 1.0, "new": 9.0}], 0) is None
        assert "max_writes_per_plan" in agent_runs.limits_hold(lim, None, w * 2, 2)

    def test_approvals_file(self, tmp_path):
        ap = approvals.add(tmp_path, "PJ", kind="writes", node="n", targets=["q1"], writes=[{"path": "p", "old": 1, "new": 2}],
                           reason="r", why_held="mode ask-writes", actor="by_claude", run_id=5)
        assert approvals.pending(tmp_path, "PJ")[0]["id"] == ap["id"]
        assert approvals.decide(tmp_path, "PJ", ap["id"], status="approved", who="human:k",
                                writes=[{"path": "p", "old": 1, "new": 3}])["writes"][0]["new"] == 3
        assert approvals.get(tmp_path, "PJ", ap["id"])["writes_original"][0]["new"] == 2
        assert approvals.pending(tmp_path, "PJ") == []
        assert approvals.decide(tmp_path, "PJ", ap["id"], status="rejected", who="x") is None, "decided once"
        with pytest.raises(ValueError):
            approvals.add(tmp_path, "PJ", kind="weird", node=None, targets=None, writes=None, reason=None,
                          why_held="", actor="x")

    def test_chassis_state_path_per_item(self):
        assert scheduler.state_path_for({"state_path": "D:/scratch"}, {"quam_state_path": "D:/chip"}) == "D:/scratch"
        assert scheduler.state_path_for({"state_path": None}, {"quam_state_path": "D:/chip"}) == "D:/chip"
        assert scheduler.state_path_for({}, {}) is None
        assert scheduler._new_item({"file": "f", "name": "n", "state_path": "S"}, [])["state_path"] == "S"
        assert scheduler._new_item({"file": "f", "name": "n"}, [])["state_path"] is None

    def test_run_item_passes_the_item_state_path_to_the_runner(self, tmp_path, monkeypatch, cal):
        """The one chassis change: argv carries the ITEM's scratch path."""
        seen = {}

        class _P:
            pid = 1
            returncode = 0

            def wait(self, timeout=None):
                return 0

        def spawn(argv, log_path):
            seen["argv"] = argv
            (tmp_path / "w").mkdir(exist_ok=True)
            return _P(), open(log_path, "w")
        monkeypatch.setattr(scheduler, "_spawn", spawn)
        monkeypatch.setattr(scheduler, "_classify_result", lambda wd, rc: ("done", None))
        monkeypatch.setattr(scheduler, "_persist_worker_pid", lambda *a: None)
        item = scheduler._new_item({"file": str(cal / "05_power_rabi.py"), "name": "05_power_rabi", "kind": "node",
                                    "has_hook": True, "targets_name": "qubits", "state_path": str(tmp_path / "scratch")}, ["qA1"])
        settings = {"env_python": sys.executable, "quam_state_path": str(tmp_path / "chip"), "global_simulate": False,
                    "default_timeout_s": 5}
        runner = {"cancel": threading.Event(), "proc": None, "proc_lock": threading.Lock()}
        out = scheduler._run_item(str(tmp_path), item, settings, runner)
        assert out["status"] == "done"
        assert seen["argv"][seen["argv"].index("--state-path") + 1] == str(tmp_path / "scratch")


# ----------------------------------------------------------------- gates

class TestGates:
    def test_the_door_is_the_agents(self, c):
        assert c.post("/api/agent/run-node", json={"node": "x", "reason": "r"}).status_code == 403
        assert _run(c, reason="").status_code == 400
        r = _run(c, targets=["nope"])
        assert r.status_code == 400 and "unknown targets" in r.get_json()["error"]
        assert c.post("/api/agent/run-node", json={"node": "", "reason": "r"}, headers=AGENT).status_code == 400

    def test_no_chip(self, app):
        assert app.test_client().post("/api/agent/run-node", json={"node": "x", "reason": "r"}, headers=AGENT).status_code == 409

    def test_gate_order_and_data(self, c, inst, fake_run, monkeypatch):
        chip = _chip(c)
        r = _run(c, node="nope").get_json()
        assert r["refused"] == "node_not_found" and "05_power_rabi" in r["available"]
        assert _run(c, node="graph_x").get_json()["refused"] == "not_a_node"
        assert _run(c, node="util_no_hook").get_json()["refused"] == "not_a_node"
        r = _run(c).get_json()
        assert r["refused"] == "no_start_token" and "Arm" in r["how"]
        _arm(c)
        agent_session.request_stop(str(inst), chip, who="human:k", mode="after_run")
        r = _run(c).get_json()
        assert r["refused"] == "stopped_by_human" and r["by"] == "human:k"
        assert not agent_session.load(str(inst), chip).get("start_token"), "a Stop takes the token back"
        _arm(c)
        assert agent_session.load(str(inst), chip).get("agent_stop") is None, "Arm clears the stop"
        limits.save(str(inst), chip, {"stop_by": "00:00"})
        assert _run(c).get_json()["refused"] == "past_stop_by"
        limits.save(str(inst), chip, {"stop_by": ""})
        monkeypatch.setattr(aa, "_human_ran_recently", lambda now, ar, ev: {"run_id": 9, "node": "x", "ts": now - 60})
        r = _run(c).get_json()
        assert r["refused"] == "human_active" and r["run"]["run_id"] == 9
        monkeypatch.setattr(aa, "_human_ran_recently", lambda now, ar, ev: {"run_id": 9, "node": "x", "ts": now - 3 * 3600})
        # too old for the window -> passes this gate (and the run happens)
        r = _run(c).get_json()
        assert r["ok"] is True

    def test_orphan_running_and_simulate_gates(self, c, inst, fake_run):
        _arm(c)
        with c.application.app_context():
            from quam_state_manager.web import routes as rt
            scope = rt._sched_inst()
        st = scheduler.load_queue(scope)
        st["run"].update({"status": "running", "worker_pid": os.getpid(), "owner_pid": None})
        scheduler.save_queue(scope, st)
        r = _run(c).get_json()
        assert r["refused"] == "orphan_running" and r["worker_pid"] == os.getpid()
        st["run"].update({"status": "idle", "worker_pid": None})
        scheduler.save_queue(scope, st)
        scheduler.save_settings(scope, {"global_simulate": True})
        limits.save(str(inst), _chip(c), {"mode": "auto"})
        assert _run(c).get_json()["refused"] == "simulate_on_in_auto"
        limits.save(str(inst), _chip(c), {"mode": "ask-writes"})
        assert _run(c).get_json()["ok"] is True, "ask-writes may run under Dry run"

    def test_no_env_and_no_folder(self, c, inst, fake_run):
        _arm(c)
        shared = Path(str(inst)) / "scheduler" / "_shared.json"
        shared.write_text(json.dumps({"calibrations_folder": "D:/nope"}), encoding="utf-8")
        assert _run(c).get_json()["refused"] == "no_env"
        shared.write_text(json.dumps({"env_python": sys.executable}), encoding="utf-8")
        assert _run(c).get_json()["refused"] == "no_calibrations_folder"

    def test_ask_all_files_a_run_request_then_runs_on_approval(self, c, inst, fake_run):
        chip = _chip(c)
        _arm(c)
        limits.save(str(inst), chip, {"mode": "auto"})
        # the SESSION's mode wins when a session exists; make one in ask-all
        agent_session.save(str(inst), chip, mode="ask-all", backend="claude", owner="human")
        r = _run(c).get_json()
        assert r["refused"] == "awaiting_approval" and r["needs"] == "run" and r["approval"]["kind"] == "run"
        aid = r["approval"]["id"]
        assert c.get("/api/agent/chip").get_json()["waiting"] == 1
        assert "waiting for approval (mode ask-all)" in _journal(c, inst)
        assert c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=AGENT).status_code == 403
        assert c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=HUMAN).get_json()["ok"]
        # nothing pending now: a bogus or foreign approval_id must still not run anything
        r = _run(c, approval_id="ap-bogus").get_json()
        assert r["refused"] == "awaiting_approval" and "APPROVED run request" in r["how"]
        r = _run(c, approval_id=aid).get_json()
        assert r["ok"] and r["status"] == "done" and r["result"]["status"] == "done"
        assert r["result"]["approval"], "ask-all: the WRITES still wait too"


# ------------------------------------------------------------------- run

class TestRun:
    def test_auto_applies_through_the_door_and_records(self, c, inst, fake_run, synth_folder):
        chip = _chip(c)
        _arm(c)
        limits.save(str(inst), chip, {"mode": "auto"})
        r = _run(c).get_json()
        assert r["ok"] and r["status"] == "done", r
        res = r["result"]
        assert res["status"] == "done" and res["classification"] in ("ok", "unattributed")
        assert [w["path"] for w in res["writes"]] == ["qubits.qA1.f_01"]
        assert res["applied"] is True and res["group_id"].startswith("agent:") and res["approval"] is None
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 6250000000.0 + 1e6, "the door wrote the chip"
        assert c.get("/api/agent/chip").get_json()["pending"] == 0
        # the fake ran on the SCRATCH copy, never the chip
        assert "agent_runs" in fake_run.calls[0]["state_path"] and str(synth_folder) not in fake_run.calls[0]["state_path"]
        assert fake_run.calls[0]["targets"] == ["qA1"] and fake_run.calls[0]["label"].startswith("agent: 05_power_rabi")
        j = _journal(c, inst)
        assert "running `05_power_rabi` on qA1" in j and "because: verify the pin" in j
        assert "ran `05_power_rabi` on qA1 (1 write(s) applied)" in j
        idx = (Path(str(inst)) / "agent_runs" / "index.jsonl").read_text(encoding="utf-8")
        rec = json.loads(idx.splitlines()[-1])
        assert rec["actor"] == "by_claude" and rec["node"] == "05_power_rabi" and rec["applied"] is True
        assert c.get("/api/agent/chip").get_json()["run_active"] is None
        assert c.application.config.get("agent_edit_lock") is None
        # the queue holds nothing of ours afterwards
        with c.application.app_context():
            from quam_state_manager.web import routes as rt
            assert scheduler.load_queue(rt._sched_inst())["queue"] == []
        runs = c.get("/api/agent/runs/agent").get_json()["runs"]
        assert runs[0]["key"] == r["key"] and runs[0]["result"]["applied"]

    def test_ask_writes_parks_an_approval_the_human_applies(self, c, inst, fake_run, synth_folder):
        chip = _chip(c)
        _arm(c)
        r = _run(c).get_json()
        res = r["result"]
        assert res["applied"] is False and res["approval"]["kind"] == "writes" and res["why_held"] == "mode ask-writes"
        assert "wait for the human's approval" in r["how"]
        assert c.get("/api/agent/chip").get_json()["pending"] == 0, "nothing in the tray"
        assert c.get("/api/agent/chip").get_json()["waiting"] == 1
        assert "1 write(s) waiting for approval (mode ask-writes)" in _journal(c, inst)
        # the chain on that target stops
        assert _run(c).get_json()["refused"] == "awaiting_approval"
        # another target may run (qA2 exists in the synth chip)
        pend = c.get("/api/agent/approvals").get_json()
        aid = pend["pending"][0]["id"]
        assert pend["pending"][0]["writes"][0]["path"] == "qubits.qA1.f_01"
        # the human edits the row and approves
        edited = [dict(pend["pending"][0]["writes"][0], new=6250000000.0 + 2e6)]
        d = c.post(f"/api/agent/approvals/{aid}/approve", json={"writes": edited}, headers=HUMAN).get_json()
        assert d["ok"] and d["stage"]["applied"] is True
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 6250000000.0 + 2e6
        assert "approved 1 write(s) from `05_power_rabi` -- applied" in _journal(c, inst)
        assert c.get("/api/agent/chip").get_json()["waiting"] == 0

    def test_reject_leaves_the_chip_alone(self, c, inst, fake_run, synth_folder):
        _arm(c)
        aid = _run(c).get_json()["result"]["approval"]["id"]
        d = c.post(f"/api/agent/approvals/{aid}/reject", json={"note": "not today"}, headers=HUMAN).get_json()
        assert d["ok"] and d["approval"]["status"] == "rejected"
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 6250000000.0
        assert "rejected writes from `05_power_rabi`: not today" in _journal(c, inst)
        assert c.post(f"/api/agent/approvals/{aid}/reject", json={}, headers=HUMAN).status_code == 404

    def test_limits_hold_in_auto(self, c, inst, fake_run):
        chip = _chip(c)
        _arm(c)
        fam = agent_runs.family_key("05_power_rabi")
        assert fam, "the families table knows power rabi"
        limits.save(str(inst), chip, {"mode": "auto", "max_delta": {fam: 1.0}})
        res = _run(c).get_json()["result"]
        assert res["applied"] is False and res["why_held"].startswith("max_delta") and res["approval"]

    def test_created_keys_are_not_staged_but_named(self, c, inst, fake_run):
        chip = _chip(c)
        _arm(c)
        limits.save(str(inst), chip, {"mode": "auto"})
        fake_run.extra = {"brand_new_key": 1}
        res = _run(c).get_json()["result"]
        assert res["applied"] is True
        assert [u["path"] for u in res["unstaged"]] == ["qubits.qA1.brand_new_key"] and "new/removed key" in res["unstaged"][0]["why"]

    def test_stop_now_cancels_the_running_node(self, c, inst, monkeypatch):
        chip = _chip(c)
        fr = FakeRun(until_cancel=True)
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        agent_session.save(str(inst), chip, backend="claude", owner="human", mode="ask-writes")
        r = _run(c, wait_s=1).get_json()
        assert r["ok"] and r["status"] == "running" and "run_wait" in r["how"]
        key = r["key"]
        assert c.get("/api/agent/chip").get_json()["run_active"]["node"] == "05_power_rabi"
        # the edit lock, meanwhile
        e = c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "1"})
        assert e.status_code == 409 and e.get_json()["error"] == "agent_running"
        assert "05_power_rabi를 돌리는 중 — 편집이 잠겼습니다" in e.get_json()["message"]
        b = c.post("/state/apply-to-live", data={"seen_changes": "0"}, headers={"Accept": "application/json"})
        assert b.status_code == 409 and b.get_json()["error"] == "agent_running"
        e2 = c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "2"}, headers=AGENT)
        assert e2.status_code == 409 and e2.get_json()["error"] == "agent_running", "the agent waits too"
        assert c.post("/api/agent/session/stop", json={"mode": "now"}).get_json()["ok"]
        m = c.get(f"/api/agent/run/{key}?wait_s=15").get_json()
        assert m["status"] == "ended" and m["result"]["status"] == "cancelled" and "stopped now" in m["result"]["error"]
        assert m["result"]["classification"] == "cancelled" and m["result"]["applied"] is False
        assert "✗ ran `05_power_rabi` on qA1 cancelled: stopped now by" in _journal(c, inst)
        assert c.application.config.get("agent_edit_lock") is None
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "1"}).status_code == 200

    def test_timeout_cancels(self, c, inst, monkeypatch):
        fr = FakeRun(until_cancel=True)
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        r = _run(c, timeout_s=1, wait_s=15).get_json()
        assert r["result"]["classification"] == "timeout" and "timed out after 1s" in r["result"]["error"]

    def test_hardware_contention_is_named_and_not_retried(self, c, inst, monkeypatch):
        fr = FakeRun(fail="qm.QmQuaException: Failed to connect to QM at 10.1.1.1:80")
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        r = _run(c).get_json()
        assert r["result"]["classification"] == "hardware_contention" and "do NOT retry" in r["how"]
        assert "hardware contention" in _journal(c, inst)
        rec = json.loads((Path(str(inst)) / "agent_runs" / "index.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert rec["classification"] == "hardware_contention" and rec["outcome"] == "failed"

    def test_run_active_gate_then_run_wait(self, c, inst, monkeypatch):
        fr = FakeRun(sleep=2.0)
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        r = _run(c, wait_s=0.2).get_json()
        assert r["status"] in ("starting", "running")
        r2 = _run(c).get_json()
        assert r2["refused"] == "run_active" and r2["run"]["key"] == r["key"]
        m = c.get(f"/api/agent/run/{r['key']}?wait_s=15").get_json()
        assert m["status"] == "done"
        assert c.get("/api/agent/run/nope").status_code == 404

    def test_attribution_names_the_run(self, c, inst, fake_run, monkeypatch):
        now = datetime.now()

        class _DS:
            def rescan_if_stale(self):
                return True

            def list_runs(self):
                # the run folder appears only once the node has run (else the human_active gate reads it)
                fresh = [{"run_id": 777, "experiment_name": "05_power_rabi", "qubits": ["qA1"], "status": "successful",
                          "date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M:%S"), "outcomes": {"qA1": "successful"}}] \
                    if fake_run.calls else []
                return fresh + [{"run_id": 700, "experiment_name": "05_power_rabi", "qubits": ["qA1"], "status": "successful",
                                 "date": "2020-01-01", "time": "00:00:00"}]
        monkeypatch.setattr(aa, "_ds", lambda: _DS())
        _arm(c)
        limits.save(str(inst), _chip(c), {"mode": "auto"})
        res = _run(c).get_json()["result"]
        assert res["run_id"] == 777 and res["classification"] == "ok"
        assert "ran `05_power_rabi` on qA1 → #777 (1 write(s) applied)" in _journal(c, inst)
        rec = json.loads((Path(str(inst)) / "agent_runs" / "index.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert rec["run_id"] == 777

    def test_attribution_never_takes_an_old_run(self, c, inst, fake_run, monkeypatch):
        """Only the 2020 run under the node's name exists: no attribution,
        never that one (exact provenance, docs/78 §7b-B2)."""
        class _DS:
            def rescan_if_stale(self):
                return True

            def list_runs(self):
                return [{"run_id": 700, "experiment_name": "05_power_rabi", "qubits": ["qA1"], "status": "successful",
                         "date": "2020-01-01", "time": "00:00:00"}]
        monkeypatch.setattr(aa, "_ds", lambda: _DS())
        _arm(c)
        limits.save(str(inst), _chip(c), {"mode": "auto"})
        res = _run(c).get_json()["result"]
        assert res["run_id"] is None and res["classification"] == "unattributed"
        assert "→ #" not in _journal(c, inst)

    def test_no_dataset_store_means_no_attribution_wait(self, c, inst, fake_run):
        """A chip with no run folder at all: the run answers at once, it does
        not poll 6 s for a folder that cannot appear."""
        _arm(c)
        limits.save(str(inst), _chip(c), {"mode": "auto"})
        t0 = time.time()
        res = _run(c).get_json()["result"]
        assert res["applied"] is True
        assert time.time() - t0 < 4.5, "no store -> no attribution poll"

    def test_a_refused_door_parks_the_writes_and_clears_the_tray(self, c, inst, monkeypatch, synth_folder):
        """The live files move while the node runs (someone saved outside SM):
        the door refuses stale_live, the agent's group comes back OUT of the
        tray, and the writes wait as an approval naming the refusal."""
        fr = FakeRun()
        monkeypatch.setattr(scheduler, "_run_item", fr)

        def drift_then_run(instance_path, item, settings, runner):
            st = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
            st["qubits"]["qA1"]["T1"] = 4242
            (synth_folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
            return fr(instance_path, item, settings, runner)
        monkeypatch.setattr(scheduler, "_run_item", drift_then_run)
        _arm(c)
        limits.save(str(inst), _chip(c), {"mode": "auto"})
        res = _run(c).get_json()["result"]
        assert res["applied"] is False and res["approval"] and res["why_held"].startswith("apply refused: stale_live")
        assert c.get("/api/agent/chip").get_json()["pending"] == 0, "the refused group is not left in the tray"
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 6250000000.0 and live["qubits"]["qA1"]["T1"] == 4242
        # approving while the chip is still moved keeps the approval PENDING with the reason
        aid = res["approval"]["id"]
        d = c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=HUMAN)
        assert d.status_code == 409 and "stale_live" in d.get_json()["error"] and "take live" in d.get_json()["how"]
        assert c.get("/api/agent/chip").get_json()["waiting"] == 1 and c.get("/api/agent/chip").get_json()["pending"] == 0
        # the human takes live, then approves: applied on top of the moved chip
        assert c.post("/state/sync", data={"mode": "discard"}).status_code in (200, 302)
        d = c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["stage"]["applied"] is True
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["f_01"] == 6250000000.0 + 1e6 and live["qubits"]["qA1"]["T1"] == 4242

    def test_apply_refuses_a_humans_rows_for_an_agent(self, c, inst):
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9999"}).status_code == 200
        r = c.post("/state/apply-to-live", data={"seen_changes": "1"}, headers={**AGENT, "Accept": "application/json"})
        assert r.status_code == 409 and r.get_json()["conflict"] == "human_groups" and r.get_json()["paths"] == ["qubits.qA1.T1"]
        assert c.get("/api/agent/chip").get_json()["pending"] == 1

    def test_undo_mine_stops_at_the_humans_edit(self, c, inst):
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "9999"}).status_code == 200
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T2ramsey", "value": "2e-6"}, headers=AGENT).status_code == 200
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.chi", "value": "-1"}, headers=AGENT).status_code == 200
        d = c.post("/api/agent/undo-mine", json={}, headers=AGENT).get_json()
        assert sorted(d["reverted"]) == ["qubits.qA1.T2ramsey", "qubits.qA1.chi"]
        assert d["stopped_at"] == {"path": "qubits.qA1.T1", "actor": "human"} and d["pending"] == 1

    def test_live_diff_and_stale_since(self, c, inst, synth_folder):
        d = c.get("/api/agent/live-diff").get_json()
        assert d["ok"] and d["count"] == 0
        st = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA1"]["T1"] = 12345
        time.sleep(0.05)
        (synth_folder / "state.json").write_text(json.dumps(st), encoding="utf-8")
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": "1"}).status_code == 200
        d = c.get("/api/agent/live-diff").get_json()
        assert [x["path"] for x in d["changed"]] == ["qubits.qA1.T1"] and d["changed"][0]["live"] == 12345
        assert d["overlap"] == ["qubits.qA1.T1"] and d["live_diverged"] is True
        chip = c.get("/api/agent/chip").get_json()
        assert chip["live_diverged"] is True and isinstance(chip["stale_since"], float)
        assert isinstance(c.get("/api/agent/state?path=qubits.qA1.T1").get_json()["stale_since"], float)

    def test_arm_is_a_persons_click_and_journaled(self, c, inst):
        assert c.post("/api/agent/session/arm", json={}, headers=AGENT).status_code == 403
        d = _arm(c)
        assert d["session"]["armed"] is True and d["session"]["armed_by"] == "human:kyunghoon"
        assert "armed by human:kyunghoon" in _journal(c, inst)
        assert c.post("/api/agent/session/disarm", json={}, headers=HUMAN).get_json()["session"]["armed"] is False
        assert "disarmed by human:kyunghoon" in _journal(c, inst)
