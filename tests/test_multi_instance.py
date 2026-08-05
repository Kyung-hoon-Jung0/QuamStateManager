"""Two State Manager windows on one machine (docs/80 Parts 1–2).

Verified empirically before any of this was written: two servers on two ports
sharing one instance directory keep SEPARATE projects cleanly isolated, but a
second window could

  * flip the first window's running experiment to idle and mark its in-flight
    item failed — by doing nothing but POLLING,
  * spawn a SECOND worker over the same queue (two processes driving one OPX),
  * write "cancelled" over a run it did not own, just by being closed.

All three come from the same asymmetry: the queue is a file, but "is a worker
running" lives in this process's memory, so a second process cannot tell a
live sibling from a crashed one. ``run.owner_pid`` + a PID probe makes the
distinction recordable.

The ownership matrix is exercised against a REAL live foreign process (a
spawned python that sleeps), not a mocked pid check — a probe that is wrong
about liveness is exactly the failure this feature would have.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from quam_state_manager.core import instances, scheduler
from quam_state_manager.web.app import create_app


# ----------------------------------------------------------------------
# A genuinely live foreign process
# ----------------------------------------------------------------------

@pytest.fixture
def foreign_pid():
    """A real OS process that outlives the assertion, then is reaped."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Give it a moment to exist as far as the OS is concerned.
    for _ in range(50):
        if instances.pid_alive(proc.pid):
            break
        time.sleep(0.02)
    yield proc.pid
    proc.kill()
    proc.wait(timeout=10)


@pytest.fixture
def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    proc.wait(timeout=30)
    pid = proc.pid
    for _ in range(50):
        if not instances.pid_alive(pid):
            break
        time.sleep(0.02)
    return pid


def _queue(owner_pid=None, owner_port=None, status="running"):
    return {
        "queue": [{
            "id": "aaa", "name": "02a_resonator_spectroscopy", "kind": "node",
            "status": "running" if status == "running" else "queued", "order": 0,
            "enabled": True, "targets": ["qA1"], "started_at": "t",
            "ended_at": None, "source_file": "C:/nodes/02a.py", "error": None,
            "log_file": None, "result_ref": None, "targets_name": "qubits",
            "param_overrides": {}, "label": "", "on_outcome": [],
            "inserted_by": None, "outcome_note": None, "returncode": None,
            "has_hook": False,
        }],
        "run": {"status": status, "current_id": "aaa", "started_at": "t",
                "message": "", "completed_count": 0, "pause_requested": False,
                "worker_pid": None, "owner_pid": owner_pid,
                "owner_port": owner_port},
    }


def _write_queue(inst: Path, state: dict) -> Path:
    inst.mkdir(parents=True, exist_ok=True)
    p = inst / "scheduler_queue.json"
    p.write_text(json.dumps(state), encoding="utf-8")
    return p


def _read_queue(inst: Path) -> dict:
    return json.loads((inst / "scheduler_queue.json").read_text(encoding="utf-8"))


# ======================================================================
# Part 1 — the registry
# ======================================================================

class TestRegistry:
    def test_register_then_read_back(self, tmp_path):
        instances.register(tmp_path, port=5173)
        me = [p for p in instances.peers(tmp_path, include_self=True)
              if p.pid == os.getpid()]
        assert len(me) == 1
        assert me[0].port == 5173
        assert me[0].label == f"port 5173 · PID {os.getpid()}"

    def test_our_own_entry_is_not_a_peer(self, tmp_path):
        instances.register(tmp_path, port=5173)
        assert instances.peers(tmp_path) == []

    def test_a_dead_process_entry_is_dropped_on_read(self, tmp_path, dead_pid):
        instances.register(tmp_path, port=5173)
        d = tmp_path / "instances"
        stale = d / f"{dead_pid}.json"
        stale.write_text(json.dumps({"pid": dead_pid, "port": 5999,
                                     "chip_path": "", "roots": []}),
                         encoding="utf-8")
        os.utime(stale, (time.time() - 60, time.time() - 60))
        assert instances.peers(tmp_path) == []
        assert not stale.exists(), "the registry must clean up after a crash"

    def test_a_live_process_entry_is_a_peer(self, tmp_path, foreign_pid):
        d = tmp_path / "instances"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{foreign_pid}.json").write_text(json.dumps({
            "pid": foreign_pid, "port": 5174, "chip_path": "C:/chips/alpha",
            "chip_fs_key": "", "chip_name": "Alpha", "roots": [],
            "updated_utc": "2026-08-05T00:00:00+00:00"}), encoding="utf-8")
        peers = instances.peers(tmp_path)
        assert [p.pid for p in peers] == [foreign_pid]
        assert peers[0].label == f"port 5174 · PID {foreign_pid}"

    def test_a_corrupt_entry_is_dropped_not_raised(self, tmp_path):
        d = tmp_path / "instances"
        d.mkdir(parents=True, exist_ok=True)
        bad = d / "424242.json"
        bad.write_text("{ not json", encoding="utf-8")
        os.utime(bad, (time.time() - 60, time.time() - 60))
        assert instances.peers(tmp_path) == []

    def test_update_merges_and_derives_the_path_key(self, tmp_path):
        instances.register(tmp_path, port=5173)
        instances.update(tmp_path, chip_path=str(tmp_path / "chipA"),
                         chip_name="Alpha")
        rec = json.loads((tmp_path / "instances" / f"{os.getpid()}.json")
                         .read_text(encoding="utf-8"))
        assert rec["port"] == 5173, "an unrelated update must not drop the port"
        assert rec["chip_name"] == "Alpha"
        assert rec["chip_fs_key"], "the canonical path key is derived, not asked for"

    def test_deregister_removes_the_entry(self, tmp_path):
        instances.register(tmp_path, port=5173)
        instances.deregister(tmp_path)
        assert instances.peers(tmp_path, include_self=True) == []

    def test_a_read_only_instance_dir_never_raises(self, tmp_path, monkeypatch):
        """Bookkeeping must never be able to stop the app."""
        def boom(*a, **k):
            raise OSError("read-only")
        monkeypatch.setattr(instances.safe_io, "atomic_write_json", boom)
        instances.register(tmp_path, port=1)     # must not raise
        instances.update(tmp_path, chip_name="x")
        assert instances.peers(tmp_path) == []


class TestConflicts:
    def test_the_same_state_path_in_two_windows_is_a_conflict(self, tmp_path,
                                                              foreign_pid):
        chip = tmp_path / "chips" / "alpha"
        chip.mkdir(parents=True)
        d = tmp_path / "instances"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{foreign_pid}.json").write_text(json.dumps({
            "pid": foreign_pid, "port": 5174, "chip_path": str(chip),
            "chip_fs_key": instances._fs_key(chip), "chip_name": "Alpha",
            "roots": [], "updated_utc": "2026-08-05T00:00:00+00:00"}),
            encoding="utf-8")
        out = instances.conflicts(tmp_path, chip_path=chip)
        assert [p.pid for p in out["same_chip"]] == [foreign_pid]

    def test_a_different_chip_is_not_a_conflict(self, tmp_path, foreign_pid):
        a = tmp_path / "chips" / "alpha"
        b = tmp_path / "chips" / "beta"
        a.mkdir(parents=True)
        b.mkdir(parents=True)
        d = tmp_path / "instances"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{foreign_pid}.json").write_text(json.dumps({
            "pid": foreign_pid, "port": 5174, "chip_path": str(b),
            "chip_fs_key": instances._fs_key(b), "chip_name": "Beta",
            "roots": [], "updated_utc": "2026-08-05T00:00:00+00:00"}),
            encoding="utf-8")
        out = instances.conflicts(tmp_path, chip_path=a)
        assert out["same_chip"] == []
        assert [p.pid for p in out["peers"]] == [foreign_pid]

    def test_a_shared_data_folder_is_deliberately_not_a_conflict(self, tmp_path,
                                                                 foreign_pid):
        """Two experiment lines on one chip out of one data folder is a real
        workflow. Warning about it would train users to ignore the banner that
        carries the warning that matters; the loss it used to cause was fixed
        at the source (docs/80 Part 0-4)."""
        a = tmp_path / "chips" / "alpha"
        b = tmp_path / "chips" / "beta"
        shared = tmp_path / "data"
        for p in (a, b, shared):
            p.mkdir(parents=True)
        d = tmp_path / "instances"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{foreign_pid}.json").write_text(json.dumps({
            "pid": foreign_pid, "port": 5174, "chip_path": str(b),
            "chip_fs_key": instances._fs_key(b), "chip_name": "Beta",
            "roots": [str(shared)], "updated_utc": "2026-08-05T00:00:00+00:00"}),
            encoding="utf-8")
        out = instances.conflicts(tmp_path, chip_path=a)
        assert out["same_chip"] == []


class TestTheAppPublishesItself:
    def test_creating_an_app_registers_it_and_a_request_records_the_port(
            self, tmp_path):
        inst = tmp_path / "_inst"
        app = create_app(testing=True, instance_path=str(inst))
        entry = inst / "instances" / f"{os.getpid()}.json"
        assert entry.exists(), "create_app must announce the process"
        app.test_client().get("/", headers={"Host": "127.0.0.1:5199"})
        rec = json.loads(entry.read_text(encoding="utf-8"))
        assert rec["port"] == 5199, "the port is captured from the first request"
        assert scheduler._OWN_PORT == 5199

    def test_loading_a_chip_publishes_it(self, tmp_path):
        inst = tmp_path / "_inst"
        chip = tmp_path / "chip"
        chip.mkdir()
        (chip / "state.json").write_text(json.dumps({
            "qubits": {"qA1": {"id": "qA1"}}, "qubit_pairs": {},
            "active_qubit_names": ["qA1"],
            "extras": {"chip_name": "Alpha"}}), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps({
            "wiring": {}, "network": {"host": "127.0.0.1"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(inst))
        app.test_client().post("/load", data={"folder": str(chip)})
        rec = json.loads((inst / "instances" / f"{os.getpid()}.json")
                         .read_text(encoding="utf-8"))
        assert Path(rec["chip_path"]) == chip
        assert rec["chip_fs_key"]


# ======================================================================
# Part 2 — scheduler run ownership
# ======================================================================

class TestOwnershipMatrix:
    """poll / start / cancel × who owns the run.

    The invariant that matters most is the FIRST row: with no owner recorded,
    every behaviour must be byte-identical to before this feature existed, so
    crash recovery — the thing that mechanism was built for — is untouched.
    """

    def test_no_owner_still_reconciles_a_crashed_worker(self, tmp_path):
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=None))
        assert scheduler.is_active(inst) is False
        after = _read_queue(inst)
        assert after["run"]["status"] == "idle"
        assert after["queue"][0]["status"] == "failed"
        assert "interrupted" in after["queue"][0]["error"]

    def test_a_dead_owner_reconciles_too(self, tmp_path, dead_pid):
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=dead_pid, owner_port=5174))
        assert scheduler.is_active(inst) is False
        after = _read_queue(inst)
        assert after["run"]["status"] == "idle"
        assert after["run"]["owner_pid"] is None, "a released claim is cleared"

    def test_a_live_foreign_owner_survives_our_poll_untouched(self, tmp_path,
                                                              foreign_pid):
        """THE reproduced defect: polling alone killed the other window's run."""
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=foreign_pid, owner_port=5174))
        assert scheduler.is_active(inst) is True, "the chip really IS being driven"
        after = _read_queue(inst)
        assert after["run"]["status"] == "running"
        assert after["queue"][0]["status"] == "running"
        assert after["queue"][0]["error"] is None
        assert after["run"]["owner_pid"] == foreign_pid
        assert str(foreign_pid) in after["run"]["message"]
        assert "5174" in after["run"]["message"], "name the window by its port"

    def test_repeated_polls_do_not_rewrite_the_queue(self, tmp_path, foreign_pid):
        inst = tmp_path / "_i"
        p = _write_queue(inst, _queue(owner_pid=foreign_pid, owner_port=5174))
        scheduler.is_active(inst)
        mtime = p.stat().st_mtime_ns
        for _ in range(3):
            scheduler.is_active(inst)
        assert p.stat().st_mtime_ns == mtime, "a steady poll must not churn the file"

    def test_start_refuses_while_a_live_foreign_owner_holds_the_run(
            self, tmp_path, foreign_pid):
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=foreign_pid, owner_port=5174))
        with pytest.raises(scheduler.ForeignRunnerError) as exc:
            scheduler.start(inst)
        assert exc.value.pid == foreign_pid
        assert "same OPX" in str(exc.value), "say WHY it is refused"
        assert scheduler.is_running(inst) is False, "no second worker was spawned"

    def test_start_is_allowed_once_the_owner_is_gone(self, tmp_path, dead_pid):
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=dead_pid, status="idle"))
        try:
            run = scheduler.start(inst)
            assert run["status"] == "running"
            assert run["owner_pid"] == os.getpid(), "we claim what we start"
        finally:
            scheduler.cancel(inst)
            scheduler._RUNNERS.pop(str(inst), None)

    def test_closing_a_window_does_not_cancel_someone_elses_run(
            self, tmp_path, foreign_pid):
        """main._kill_scheduler runs on window close and reaches cancel()."""
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=foreign_pid, owner_port=5174))
        scheduler._RUNNERS.pop(str(inst), None)
        scheduler.cancel(inst)
        after = _read_queue(inst)
        assert after["run"]["status"] == "running"
        assert after["run"]["message"] != "cancelled"
        assert after["queue"][0]["status"] == "running"

    def test_cancelling_our_own_idle_queue_still_works(self, tmp_path):
        inst = tmp_path / "_i"
        _write_queue(inst, _queue(owner_pid=os.getpid(), status="idle"))
        scheduler._RUNNERS.pop(str(inst), None)
        scheduler.cancel(inst)
        after = _read_queue(inst)
        assert after["run"]["status"] == "idle"
        assert after["run"]["message"] == "cancelled"
        assert after["run"]["owner_pid"] is None


class TestTheRouteSurfacesIt:
    def test_start_answers_409_naming_the_other_window(self, tmp_path, foreign_pid):
        inst = tmp_path / "_inst"
        inst.mkdir(parents=True, exist_ok=True)
        _write_queue(inst, _queue(owner_pid=foreign_pid, owner_port=5174))
        app = create_app(testing=True, instance_path=str(inst))
        r = app.test_client().post("/scheduler/start", json={"force": True})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error"] == "scheduler_foreign_owner"
        assert body["owner"]["pid"] == foreign_pid
        assert "5174" in body["owner"]["label"]

    def test_the_status_poll_names_the_owning_window(self, tmp_path, foreign_pid):
        inst = tmp_path / "_inst"
        inst.mkdir(parents=True, exist_ok=True)
        _write_queue(inst, _queue(owner_pid=foreign_pid, owner_port=5174))
        app = create_app(testing=True, instance_path=str(inst))
        body = app.test_client().get("/scheduler/status").get_json()
        assert body["foreign_owner"]["pid"] == foreign_pid
        assert "port 5174" in body["foreign_owner"]["label"]

    def test_no_foreign_owner_key_when_the_run_is_ours(self, tmp_path):
        inst = tmp_path / "_inst"
        inst.mkdir(parents=True, exist_ok=True)
        _write_queue(inst, _queue(owner_pid=os.getpid(), status="idle"))
        app = create_app(testing=True, instance_path=str(inst))
        body = app.test_client().get("/scheduler/status").get_json()
        assert "foreign_owner" not in body


class TestPidProbe:
    def test_it_knows_a_live_process(self, foreign_pid):
        assert instances.pid_alive(foreign_pid) is True

    def test_it_knows_a_dead_process(self, dead_pid):
        assert instances.pid_alive(dead_pid) is False

    def test_garbage_is_not_alive(self):
        assert instances.pid_alive(None) is False
        assert instances.pid_alive("nope") is False
        assert instances.pid_alive(-1) is False
        assert instances.pid_alive(0) is False

    def test_the_scheduler_alias_is_the_same_function(self):
        """One implementation, so the orphan reconciler and the registry can
        never disagree about who is alive."""
        assert scheduler._pid_alive is instances.pid_alive


# ======================================================================
# Part 3 — the banner (reported, never blocked)
# ======================================================================

class TestSameChipBanner:
    def _app_with_chip(self, tmp_path):
        inst = tmp_path / "_inst"
        chip = tmp_path / "chip"
        chip.mkdir()
        (chip / "state.json").write_text(json.dumps({
            "qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9}}, "qubit_pairs": {},
            "active_qubit_names": ["qA1"],
            "extras": {"chip_name": "Alpha"}}), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps({
            "wiring": {}, "network": {"host": "127.0.0.1"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(inst))
        c = app.test_client()
        c.post("/load", data={"folder": str(chip)})
        return app, c, inst, chip

    def _seed_peer(self, inst: Path, pid: int, chip: Path, port=5174):
        d = inst / "instances"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "port": port, "chip_path": str(chip),
            "chip_fs_key": instances._fs_key(chip), "chip_name": "Alpha",
            "roots": [], "updated_utc": "2026-08-05T00:00:00+00:00"}),
            encoding="utf-8")

    def test_no_banner_when_this_is_the_only_window(self, tmp_path):
        _app, c, _inst, _chip = self._app_with_chip(tmp_path)
        html = c.get("/instances/banner").get_data(as_text=True)
        assert "multi-instance-banner" not in html

    def test_the_banner_names_the_other_window_and_the_hazard(
            self, tmp_path, foreign_pid):
        _app, c, inst, chip = self._app_with_chip(tmp_path)
        self._seed_peer(inst, foreign_pid, chip)
        html = c.get("/instances/banner").get_data(as_text=True)
        assert "multi-instance-banner" in html
        assert "port 5174" in html
        assert "overwrite each other" in html, "state the consequence, not just the fact"

    def test_a_different_chip_gets_no_banner(self, tmp_path, foreign_pid):
        _app, c, inst, _chip = self._app_with_chip(tmp_path)
        other = tmp_path / "otherchip"
        other.mkdir()
        self._seed_peer(inst, foreign_pid, other)
        html = c.get("/instances/banner").get_data(as_text=True)
        assert "multi-instance-banner" not in html

    def test_a_dead_window_leaves_no_banner(self, tmp_path, dead_pid):
        _app, c, inst, chip = self._app_with_chip(tmp_path)
        self._seed_peer(inst, dead_pid, chip)
        html = c.get("/instances/banner").get_data(as_text=True)
        assert "multi-instance-banner" not in html

    def test_the_banner_never_blocks_editing(self, tmp_path, foreign_pid):
        """Reported, not enforced: the user keeps working."""
        _app, c, inst, chip = self._app_with_chip(tmp_path)
        self._seed_peer(inst, foreign_pid, chip)
        r = c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01",
                                        "value": "5.1e9"})
        assert r.status_code == 200, r.get_data(as_text=True)[:200]
        assert r.get_json()["ok"] is True

    def test_the_slot_is_wired_into_every_page(self, tmp_path):
        _app, c, _inst, _chip = self._app_with_chip(tmp_path)
        html = c.get("/qubits").get_data(as_text=True)
        assert 'id="multi-instance-slot"' in html
        assert "/instances/banner" in html
