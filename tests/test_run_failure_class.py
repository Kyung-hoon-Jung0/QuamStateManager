"""docs/249: a failed run says WHAT failed, and a crash leaves an honest state.

A-12 / D-10 / C-09: an unreachable QM host used to be filed as
``hardware_contention`` -- "the OPX is held elsewhere; do NOT retry" -- so an
agent told the person the hardware was busy when the host was simply not
there. The failure texts below are the client's own words, captured by
running ``QuantumMachinesManager(host="127.0.0.1", port=1)`` (and an
unresolvable ``.invalid`` name, a local listener that never answers, a local
HTTP server that is not a QM gateway) in the lab envs; the contention texts
are quoted from ``qualang_tools/multi_user/multi_user_tools.py`` and
``qm/exceptions.py``.

D-13: after an SM crash the queue kept SM's OWN dead row, and the gate told
the agent a person had to clear it (``queue_not_empty``). A-14: a run a crash
cut off showed "interrupted" while the journal said nothing, no webhook fired
and the session stayed armed.

The chassis is real; only ``scheduler._run_item`` is a fake (as in
test_agent_runs).
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import agent_runs, agent_session, limits, scheduler
from quam_state_manager.core import journal as journal_mod
from tests.test_agent_runs import (  # noqa: F401  (fixtures are used by name)
    AGENT, FakeRun, _arm, _armed_plan, _chip, _journal, _run, _wait, app, c, cal, inst, synth_folder,
)

# ------------------------------------------------------------------ the texts

# what a node's _result.json carried on the rig (quam_builder wraps the qm error)
REFUSED_ERROR = ("ConnectionError: Failed to connect to Quantum Machines Manager: Failed to detect to "
                 "QuantumMachines server, failed to connect to cluster 'rig_cluster'. Tried connecting to "
                 "127.0.0.1:1.")
# what the qm logger printed before that (server_detector.detect_server), as captured
REFUSED_LOG = (
    "2026-10-03 18:44:58,897 - qm - ERROR    - Failed to detect to QuantumMachines server, failed to connect "
    "to cluster 'rig_cluster'. Tried connecting to 127.0.0.1:1.\r\n"
    "Errors:\r\n"
    "127.0.0.1:1: [WinError 10061] No connection could be made because the target machine actively refused it\r\n"
    "Traceback (most recent call last):\r\n"
    "  File \"...\\httpx\\_transports\\default.py\", line 118, in map_httpcore_exceptions\r\n"
    "httpx.ConnectError: [WinError 10061] No connection could be made because the target machine actively "
    "refused it\r\n")


def _detector(target: str, why: str) -> tuple[str, str]:
    err = ("QmServerDetectionError: Failed to detect to QuantumMachines server, failed to connect to a cluster. "
           f"Tried connecting to {target}.")
    log = (f"2026-10-03 18:45:48,234 - qm - ERROR    - Failed to detect to QuantumMachines server, failed to "
           f"connect to a cluster. Tried connecting to {target}.\r\nErrors:\r\n{target}: {why}\r\n")
    return err, log


DNS = _detector("qm-host.invalid:80", "[Errno 11001] getaddrinfo failed")
SILENT = _detector("127.0.0.1:13498", "timed out")
NOT_QM = _detector("127.0.0.1:13502", "Server disconnected")
LINUX_REFUSED = _detector("127.0.0.1:1", "[Errno 111] Connection refused")
# qualang_tools/multi_user/multi_user_tools.py, qm_session
QOP_BUSY_LOG = "2026-10-03 19:00:00,000 - qm.api.frontend_api - ERROR - QOP is busy. Waiting for it to free up for 600s...\r\n"
CONTENTION_GIVE_UP = "TimeoutError: While waiting for QOP to free, reached timeout: 100s"


# ------------------------------------------------------------------ pure: the table

CASES = [
    # (error, log tail, class, expected `what`)
    (REFUSED_ERROR, REFUSED_LOG, "host_unreachable", "QM host unreachable at 127.0.0.1:1 (connection refused)"),
    (REFUSED_ERROR, "", "host_unreachable", "QM host unreachable at 127.0.0.1:1"),   # cause never invented
    (*LINUX_REFUSED, "host_unreachable", "QM host unreachable at 127.0.0.1:1 (connection refused)"),
    (*DNS, "host_unreachable", "QM host unreachable at qm-host.invalid:80 (the host name did not resolve)"),
    (*SILENT, "host_unreachable", "QM host unreachable at 127.0.0.1:13498 (no answer, timed out)"),
    (*NOT_QM, "host_unreachable",
     "QM host unreachable at 127.0.0.1:13502 (something answered there, but not a QM server)"),
    ("QMConnectionError: Encountered connection error from QOP: details: Socket closed, status:  "
     "StatusCode.UNAVAILABLE", "", "host_unreachable", "lost the connection to the QM server"),
    ("ConnectionError: Failed to connect to Quantum Machines Manager: Failed to load TLS credentials from the "
     "provided paths: x", "", "host_unreachable",
     "could not connect to the QM server: ConnectionError: Failed to connect to Quantum Machines Manager: "
     "Failed to load TLS credentials from the provided paths: x"),
    # contention: the client's own words
    (CONTENTION_GIVE_UP, QOP_BUSY_LOG, "hardware_contention",
     "the hardware is busy: another quantum machine or job holds what this node needs (waited 100s for it to free)"),
    (CONTENTION_GIVE_UP, "", "hardware_contention", None),          # its give-up alone, the log tail cut off
    ("Exception: ", "Can not open QM, see the following errors:\r\nPHYSICAL CONFIG ERROR in key \"x\" [y] : "
                    "Resources already locked\r\n", "hardware_contention", None),
    ("Exception: ", "PHYSICAL CONFIG ERROR ... port 1 cannot be used because it isn't shareable in other QM.\r\n",
     "hardware_contention", None),
    ("AnotherJobIsRunning: Another job is running on the QM. Halt it first", "", "hardware_contention", None),
    # SM's own limit while qm_session still waited for the busy QOP: the honest cause is the contention
    ("timed out after 60s (run_node timeout_s)", QOP_BUSY_LOG, "hardware_contention", None),
    # ... but a wait that DID open the QM is history, not the cause
    ("timed out after 60s (run_node timeout_s)", QOP_BUSY_LOG + "INFO - Opening QM\r\n...measuring\r\n",
     "timeout", "timed out after 60s (run_node timeout_s)"),
    ("QMTimeoutError: A timeout of 60 seconds was reached. The timeout value can be configured either in the "
     "relevant API call (if supported) or when creating the QuantumMachinesManager instance.", "", "timeout", None),
    ("QMTimeoutError: Job 7 did not reach any state of ['Running'] within 30 seconds", "", "timeout", None),
    ("timed out after 10s", "", "timeout", "timed out after 10s"),
    ("ValueError: fit did not converge", "", "node_error", None),
]


@pytest.mark.parametrize("error,tail,cls,what", CASES)
def test_the_failure_says_what_failed(error, tail, cls, what):
    assert agent_runs.classify("failed", error, tail) == cls
    info = agent_runs.failure_info(cls, error, tail)
    if cls == "node_error":
        assert info is None
        return
    if what is not None:
        assert info["what"] == what
    if cls == "host_unreachable":
        assert info["retry"] == "no" and "do not retry" in info["how"].lower() and "held elsewhere" not in info["how"]
    if cls == "hardware_contention":
        assert "do NOT retry" in info["how"] and info["retry"] == "after_it_ends"


def test_classes_are_one_list():
    for cls in {c[2] for c in CASES} | {"ok", "cancelled", "skipped", "unattributed", "interrupted"}:
        assert cls in agent_runs.CLASSES


def test_unreachable_detail_names_target_cluster_cause():
    d = agent_runs.unreachable_detail(REFUSED_ERROR, REFUSED_LOG)
    assert d == {"target": "127.0.0.1:1", "cluster": "rig_cluster", "cause": "connection refused"}
    assert agent_runs.unreachable_detail("boom", "")["target"] is None


def test_connect_failure_never_reads_as_mid_run_drop():
    info = agent_runs.failure_info("host_unreachable", REFUSED_ERROR, REFUSED_LOG)
    assert "never reached the hardware" in info["how"]
    drop = agent_runs.failure_info("host_unreachable", "QMConnectionError: Encountered connection error from QOP: "
                                   "details: x, status:  StatusCode.UNAVAILABLE", "")
    assert "dropped while the node ran" in drop["how"] and "never reached" not in drop["how"]


# ------------------------------------------------------------------ through run_node

class LoggingFakeRun(FakeRun):
    """FakeRun that also writes the item's stdout log first, where the chassis's
    ``tail_log`` reads it -- the run's own words reach the classifier."""

    def __init__(self, *, log: str = "", worker_pid=None, **kw):
        super().__init__(**kw)
        self.log, self.worker_pid = log, worker_pid

    def __call__(self, instance_path, item, settings, runner):
        if self.log:
            (scheduler._logs_dir(instance_path) / f"{item['id']}.log").write_text(self.log, encoding="utf-8")
        if self.worker_pid:
            scheduler._persist_worker_pid(instance_path, self.worker_pid)
            time.sleep(1.6)                      # the driver polls once a second
            scheduler._persist_worker_pid(instance_path, None)
        return super().__call__(instance_path, item, settings, runner)


@pytest.fixture
def notified(monkeypatch):
    got: list = []
    monkeypatch.setattr(limits, "notify", lambda inst_, chip_, event, payload=None: got.append((event, payload)) or {})
    return got


class TestThroughRunNode:
    def test_unreachable_host_is_named_and_never_called_contention(self, c, inst, monkeypatch, notified):
        fr = LoggingFakeRun(fail=REFUSED_ERROR, log=REFUSED_LOG)
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        r = _run(c).get_json()
        res = r["result"]
        assert res["classification"] == "host_unreachable", res
        assert res["failure"]["what"] == "QM host unreachable at 127.0.0.1:1 (connection refused)"
        assert res["failure"]["retry"] == "no" and res["failure"]["cluster"] == "rig_cluster"
        assert "held elsewhere" not in (r.get("how") or ""), "the agent is never told the OPX is busy"
        j = _journal(c, inst)
        assert "QM host unreachable at 127.0.0.1:1 (connection refused); check the network / host config" in j
        assert "hardware contention" not in j
        rec = json.loads((Path(str(inst)) / "agent_runs" / "index.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        assert rec["classification"] == "host_unreachable"
        assert _wait(lambda: notified)
        ev, payload = notified[-1]
        assert ev == "agent_failure" and payload["classification"] == "host_unreachable"
        assert payload["what"] == "QM host unreachable at 127.0.0.1:1 (connection refused)"

    def test_sm_time_limit_during_a_busy_wait_is_contention(self, c, inst, monkeypatch):
        fr = LoggingFakeRun(until_cancel=True, log=QOP_BUSY_LOG)
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        r = _run(c, timeout_s=1, wait_s=15).get_json()
        assert "timed out after 1s" in r["result"]["error"]
        assert r["result"]["classification"] == "hardware_contention", r["result"]
        assert "busy" in r["result"]["failure"]["what"]

    def test_the_worker_pid_is_kept_in_the_runs_record(self, c, inst, monkeypatch):
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            fr = LoggingFakeRun(worker_pid=sleeper.pid)
            monkeypatch.setattr(scheduler, "_run_item", fr)
            _arm(c)
            r = _run(c).get_json()
            meta = json.loads((Path(str(inst)) / "agent_runs" / r["key"] / "meta.json").read_text(encoding="utf-8"))
            assert meta["worker_pid"] == sleeper.pid, "a restart must be able to tell a live orphan from a dead one"
        finally:
            sleeper.kill()
            sleeper.wait(10)


# ------------------------------------------------------------------ D-13: SM's own leftover rows

def _scope(app):
    with app.app_context():
        from quam_state_manager.web import routes as r
        return r._sched_inst()


def _leftover(app, inst, cal, *, row_status="running", meta_status="interrupted", worker_pid=None,
              owner_pid=None, key="20261003-160722-f7a91a"):
    """A row exactly as a crashed SM leaves it: run_node's scratch state_path,
    the run's meta beside it, the chassis' run claim still on the file."""
    root = Path(str(inst)) / "agent_runs" / key
    (root / "quam_state").mkdir(parents=True, exist_ok=True)
    (root / "meta.json").write_text(json.dumps({"key": key, "status": meta_status, "node": "05_power_rabi",
                                                "since": time.time() - 60}), encoding="utf-8")
    scope = _scope(app)
    item = scheduler.add_item(scope, {
        "file": str(Path(str(cal)) / "05_power_rabi.py"), "name": "05_power_rabi", "kind": "node", "has_hook": True,
        "targets_name": "qubits", "label": "agent: 05_power_rabi (by_claude)",
        "state_path": str(root / "quam_state"), "baseline_path": str(root / "before")}, targets=["qA1"])
    with scheduler._QLOCK:
        st = scheduler.load_queue(scope)
        for it in st["queue"]:
            if it["id"] == item["id"]:
                it["status"] = row_status
        if row_status == "running":
            st["run"].update({"status": "running", "current_id": item["id"], "worker_pid": worker_pid,
                              "owner_pid": owner_pid, "owner_port": None})
        scheduler.save_queue(scope, st)
    return scope, item


def _queue_ids(scope):
    with scheduler._QLOCK:
        return [it["id"] for it in scheduler.load_queue(scope)["queue"]]


@pytest.fixture
def sleeper():
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield p
    p.kill()
    p.wait(10)


class TestLeftoverRows:
    @pytest.mark.parametrize("row_status", ["running", "queued"])
    def test_a_dead_own_row_neither_blocks_nor_runs_first(self, app, c, inst, cal, monkeypatch, row_status):
        c.get("/api/agent/runs/agent")                       # the registry exists (the restart already happened)
        scope, dead = _leftover(app, inst, cal, row_status=row_status)
        fr = FakeRun()
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        r = _run(c).get_json()
        assert r.get("refused") is None, r
        assert r["result"]["status"] == "done", r["result"]
        assert len(fr.calls) == 1 and fr.calls[0]["id"] != dead["id"], "the dead row never runs under this click"
        assert dead["id"] not in _queue_ids(scope)
        assert "removed 1 leftover row(s) from the run queue (`05_power_rabi`)" in _journal(c, inst)

    def test_a_live_orphan_is_named_as_one(self, app, c, inst, cal, monkeypatch, sleeper):
        c.get("/api/agent/runs/agent")
        scope, row = _leftover(app, inst, cal, row_status="running", worker_pid=sleeper.pid)
        fr = FakeRun()
        monkeypatch.setattr(scheduler, "_run_item", fr)
        _arm(c)
        # docs/253: the run names the plan its Start armed (unnamed, the token gate answers first)
        r = c.post("/api/agent/run-node", json={"node": "05_power_rabi", "targets": ["qA1"], "reason": "x",
                                                "wait_s": 5, "plan_id": _armed_plan(c)}, headers=AGENT)
        body = r.get_json()
        assert r.status_code == 409 and body["refused"] == "orphan_running", body
        assert body["worker_pid"] == sleeper.pid and "still running" in body["how"]
        assert row["id"] in _queue_ids(scope) and not fr.calls, "a live node process is never swept"

    def test_a_row_another_driver_still_owns_blocks(self, app, c, inst, cal, monkeypatch):
        c.get("/api/agent/runs/agent")
        scope, row = _leftover(app, inst, cal, row_status="queued", meta_status="running")
        monkeypatch.setattr(scheduler, "_run_item", FakeRun())
        _arm(c)
        body = _run(c).get_json()
        assert body["refused"] == "queue_not_empty", body
        assert row["id"] in _queue_ids(scope)

    def test_a_live_foreign_owner_keeps_its_rows(self, app, c, inst, cal, monkeypatch, sleeper):
        c.get("/api/agent/runs/agent")
        scope, row = _leftover(app, inst, cal, row_status="running", owner_pid=sleeper.pid)
        assert agent_runs.sweep_leftover_rows(scope) == []
        monkeypatch.setattr(scheduler, "_run_item", FakeRun())
        _arm(c)
        body = _run(c).get_json()
        assert body["refused"] == "queue_not_empty", body
        assert row["id"] in _queue_ids(scope)

    def test_a_persons_row_still_blocks(self, app, c, inst, cal, monkeypatch):
        scope = _scope(app)
        scheduler.add_item(scope, {"file": str(Path(str(cal)) / "05_power_rabi.py"), "name": "05_power_rabi",
                                   "kind": "node", "has_hook": True, "targets_name": "qubits"}, targets=["qA1"])
        monkeypatch.setattr(scheduler, "_run_item", FakeRun())
        _arm(c)
        assert _run(c).get_json()["refused"] == "queue_not_empty"


# ------------------------------------------------------------------ A-14: the interrupted run

def _in_flight(inst, *, chip="chip-0001", key="20261003-160201-aa201b", name="labchip", worker_pid=None,
               armed_offset=-5.0):
    t0 = time.time() - 30
    root = Path(str(inst)) / "agent_runs" / key
    root.mkdir(parents=True, exist_ok=True)
    meta = {"key": key, "chip": chip, "node": "11_power_rabi", "targets": ["qB2"], "status": "running",
            "since": t0, "plan_id": None, "result": None}
    if name:
        meta["chip_name"] = name
    if worker_pid:
        meta["worker_pid"] = worker_pid
    (root / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    agent_session.save(str(inst), chip, start_token="tok123", armed_by="human:tester", armed_at=t0 + armed_offset,
                       run_key=key, claimed_by_tool="run_node")
    return chip, key


def _today():
    return datetime.now().strftime("%Y-%m-%d")


class TestInterrupted:
    def test_said_once_disarmed_journaled_webhooked(self, tmp_path, notified):
        inst = tmp_path / "inst"
        chip, key = _in_flight(inst)
        reg = agent_runs.Registry(inst)
        m = reg.get(key)
        assert m["status"] == "interrupted"
        assert m["result"]["classification"] == "interrupted"
        assert "SM did not collect what it wrote" in m["result"]["failure"]["how"]
        s = agent_session.load(str(inst), chip)
        assert s["start_token"] is None and s["run_key"] is None and s["claimed_by_tool"] is None
        j = journal_mod.read(str(inst), "labchip", _today())
        line = ("✗ ran `11_power_rabi` on qB2 interrupted: SM restarted while this run was in flight; nothing was "
                "applied; the session was disarmed -- running again takes a person's Start on a plan")
        assert line in j
        assert _wait(lambda: notified)
        ev, payload = notified[0]
        assert ev == "agent_failure" and payload["classification"] == "interrupted" and payload["disarmed"] is True
        agent_runs.Registry(inst)                              # a second restart says nothing new
        time.sleep(0.3)
        assert journal_mod.read(str(inst), "labchip", _today()).count("interrupted:") == 1
        assert len(notified) == 1

    def test_a_person_who_re_armed_since_keeps_the_arm(self, tmp_path, notified):
        inst = tmp_path / "inst"
        chip, key = _in_flight(inst, armed_offset=+10.0)
        agent_runs.Registry(inst)
        s = agent_session.load(str(inst), chip)
        assert s["start_token"] == "tok123" and s["run_key"] is None
        assert "disarmed" not in journal_mod.read(str(inst), "labchip", _today())

    def test_a_live_node_process_is_named(self, tmp_path, notified, sleeper):
        inst = tmp_path / "inst"
        chip, key = _in_flight(inst, worker_pid=sleeper.pid)
        m = agent_runs.Registry(inst).get(key)
        assert f"PID {sleeper.pid}" in m["result"]["error"] and "may still be driving the OPX" in m["result"]["error"]

    def test_no_chip_name_writes_no_journal_anywhere(self, tmp_path, notified):
        inst = tmp_path / "inst"
        chip, key = _in_flight(inst, name=None)
        agent_runs.Registry(inst)
        jroot = journal_mod.root(str(inst))
        assert not (jroot.exists() and list(jroot.rglob("*.md"))), "never a guessed journal name"
        assert agent_session.load(str(inst), chip)["start_token"] is None

    def test_start_keeps_the_journal_name_and_scope(self, tmp_path, monkeypatch):
        reg = agent_runs.Registry(tmp_path / "inst")
        monkeypatch.setattr(agent_runs.Registry, "_drive", lambda self, *a, **k: None)
        adapter = agent_runs.RunAdapter(
            instance_path=str(tmp_path / "inst"), chip="chip-0001", scope=str(tmp_path / "scope"),
            live_folder="", working_folder="", settings=dict, human_recent=lambda w: None, list_runs=list,
            stage=lambda *a: {}, journal=lambda *a, **k: None, wake=lambda: None, set_lock=lambda i: None,
            notify=lambda e, p: None, queue_state=dict, own_runner_alive=lambda: False, chip_name="labchip")

        class _Info:
            name, file = "05_power_rabi", "x.py"
        meta = reg.start(agent_runs.RunRequest(node="05_power_rabi", targets=["qA1"]), adapter,
                         node_info=_Info(), session=None, lim={})
        on_disk = json.loads((tmp_path / "inst" / "agent_runs" / meta["key"] / "meta.json").read_text(encoding="utf-8"))
        assert on_disk["chip_name"] == "labchip" and on_disk["scope"] == str(tmp_path / "scope")
