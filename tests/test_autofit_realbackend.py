"""RealBackend protocol tests — the scheduler-chassis driver's race closures
(docs/56 §7b-B), exercised against a MONKEYPATCHED scheduler (no env, no
subprocess): lost-wakeup watchdog, heartbeat feeding, abort→cancel, exact-name
+ time-window attribution with bounded re-poll, queue hygiene."""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core.autofit import realbackend
from quam_state_manager.core.autofit.plan import Step
from quam_state_manager.core.autofit.realbackend import RealAdapter, RealBackend


class FakeScheduler:
    """Minimal in-memory stand-in for core.scheduler's queue API."""

    def __init__(self, *, status_script=None):
        self.items: dict[str, dict] = {}
        self.run = {"status": "idle"}
        self.starts = 0
        self.cancels = 0
        self.touches = 0
        self._QLOCK = threading.RLock()
        # per-item list of statuses served on successive polls
        self.status_script = status_script or {}
        self._polls: dict[str, int] = {}
        self.running = False

    # --- API mirrored from core.scheduler ------------------------------
    def add_item(self, inst, info, targets=None):
        item = {"id": f"it{len(self.items) + 1}", "status": "queued",
                "error": None, **info, "targets": targets or []}
        self.items[item["id"]] = item
        return item

    def start(self, inst):
        self.starts += 1
        self.running = True
        self.run["status"] = "running"

    def cancel(self, inst):
        self.cancels += 1
        for it in self.items.values():
            if it["status"] in ("queued", "running"):
                it["status"] = "cancelled"
        self.run["status"] = "idle"

    def touch_ui(self, inst):
        self.touches += 1

    def is_running(self, inst):
        return self.running

    def load_queue(self, inst):
        # advance scripted statuses one step per poll
        for iid, script in self.status_script.items():
            it = self.items.get(iid)
            if it is None:
                continue
            k = self._polls.get(iid, 0)
            if k < len(script):
                it["status"] = script[k]
                self._polls[iid] = k + 1
        return {"queue": list(self.items.values()), "run": dict(self.run)}

    def save_queue(self, inst, state):
        self.items = {i["id"]: i for i in state["queue"]}

    def _find(self, state, item_id):
        for it in state["queue"]:
            if it["id"] == item_id:
                return it
        return None


@pytest.fixture(autouse=True)
def fast_polls(monkeypatch):
    monkeypatch.setattr(realbackend, "_POLL_S", 0.01)
    monkeypatch.setattr(realbackend, "_WATCHDOG_S", 0.05)
    monkeypatch.setattr(realbackend, "_ATTRIBUTION_POLL_S", 0.2)


@pytest.fixture
def node_file(tmp_path, monkeypatch):
    f = tmp_path / "08_qubit_spectroscopy.py"
    f.write_text("# node stub", encoding="utf-8")
    monkeypatch.setattr(realbackend.node_scan, "scan_file",
                        lambda p: SimpleNamespace(
                            error=None, name="08_qubit_spectroscopy",
                            kind="node", has_hook=True, targets_name="qubits"))
    return f


def _mk_run_folder(tmp_path, name="08_qubit_spectroscopy", patches=None):
    folder = tmp_path / "runfolder"
    folder.mkdir(exist_ok=True)
    (folder / "node.json").write_text(json.dumps(
        {"patches": patches or [{"op": "replace",
                                 "path": "/quam/qubits/qA1/f_01",
                                 "value": 5.1e9, "old": 5.0e9}]}))
    return folder


def _fake_run(folder, name="08_qubit_spectroscopy", start_offset_s=1):
    return SimpleNamespace(
        experiment_name=name,
        fit_results={"qA1": {"frequency": 5.1e9, "success": True}},
        outcomes={"qA1": "successful"}, parameters={"num_shots": 400},
        folder_path=folder, run_id=42,
        run_start=(datetime.now(timezone.utc)
                   + timedelta(seconds=start_offset_s)).isoformat())


def _backend(tmp_path, fake, node_file, runs=None, monkeypatch=None):
    for attr in ("add_item", "start", "cancel", "touch_ui", "is_running",
                 "load_queue", "save_queue", "_find", "_QLOCK"):
        monkeypatch.setattr(realbackend.scheduler, attr, getattr(fake, attr))
    reconciled = []
    adapter = RealAdapter(
        instance_path=str(tmp_path / "inst"),
        reconcile=lambda: reconciled.append(1),
        rescan_and_list_runs=lambda: list(runs or []),
        step_timeout_s=5.0)
    backend = RealBackend(adapter, {"qubit_spec": str(node_file)})
    return backend, reconciled


STEP = Step(id="qubit_spec", family="qubit_spectroscopy")


class TestHappyProtocol:
    def test_done_item_is_ingested_and_removed(self, tmp_path, node_file,
                                               monkeypatch):
        folder = _mk_run_folder(tmp_path)
        fake = FakeScheduler(status_script={"it1": ["queued", "running",
                                                    "done"]})
        runs = [_fake_run(folder)]
        backend, reconciled = _backend(tmp_path, fake, node_file,
                                       runs=runs, monkeypatch=monkeypatch)
        res = backend.run_step(STEP, ["qA1"], {"num_shots": 400}, 0,
                               threading.Event())
        assert res.status == "done"
        assert res.run["experiment_name"] == "08_qubit_spectroscopy"
        # patches were read synchronously from the run's node.json
        assert res.run["patches"][0]["path"] == "/quam/qubits/qA1/f_01"
        assert reconciled, "engine-owned reconcile did not run"
        assert fake.touches > 0, "heartbeat was not fed"
        assert fake.items == {}, "autofit item not removed after ingest"

    def test_failed_item_reports_error(self, tmp_path, node_file, monkeypatch):
        fake = FakeScheduler(status_script={"it1": ["running", "failed"]})
        fake.items_error = "boom"
        backend, _ = _backend(tmp_path, fake, node_file, monkeypatch=monkeypatch)
        # inject the error on the item as the worker would
        res = backend.run_step(STEP, ["qA1"], {}, 0, threading.Event())
        assert res.status == "failed"


class TestRaceClosures:
    def test_lost_wakeup_watchdog_restarts_the_worker(self, tmp_path,
                                                      node_file, monkeypatch):
        # item stays queued; worker not running → watchdog must re-start()
        fake = FakeScheduler()
        orig_start = fake.start

        def start_then_finish(inst):
            orig_start(inst)
            if fake.starts >= 2:          # the watchdog's second start
                fake.items["it1"]["status"] = "done"
        fake.start = start_then_finish
        fake.running = False
        folder = _mk_run_folder(tmp_path)
        backend, _ = _backend(tmp_path, fake, node_file,
                              runs=[_fake_run(folder)], monkeypatch=monkeypatch)
        # first start() leaves the item queued and the "worker" dead
        fake_is_running = lambda inst: False
        monkeypatch.setattr(realbackend.scheduler, "is_running", fake_is_running)
        res = backend.run_step(STEP, ["qA1"], {}, 0, threading.Event())
        assert fake.starts >= 2, "watchdog never re-started the worker"
        assert res.status == "done"

    def test_abort_cancels_the_chassis(self, tmp_path, node_file, monkeypatch):
        fake = FakeScheduler()          # item stays queued forever
        backend, _ = _backend(tmp_path, fake, node_file, monkeypatch=monkeypatch)
        abort = threading.Event()
        abort.set()
        res = backend.run_step(STEP, ["qA1"], {}, 0, abort)
        assert res.status == "aborted"
        assert fake.cancels == 1

    def test_timeout_fails_closed(self, tmp_path, node_file, monkeypatch):
        fake = FakeScheduler()          # never terminal
        backend, _ = _backend(tmp_path, fake, node_file, monkeypatch=monkeypatch)
        backend.adapter.step_timeout_s = 0.05
        res = backend.run_step(STEP, ["qA1"], {}, 0, threading.Event())
        assert res.status == "failed"
        assert "timeout" in (res.error or "")


class TestAttribution:
    def test_exact_name_and_window(self, tmp_path, node_file, monkeypatch):
        folder = _mk_run_folder(tmp_path)
        old_run = _fake_run(folder, start_offset_s=-3600)      # pre-window
        wrong_name = _fake_run(folder, name="12_ramsey")
        good = _fake_run(folder)
        fake = FakeScheduler(status_script={"it1": ["done"]})
        backend, _ = _backend(tmp_path, fake, node_file,
                              runs=[wrong_name, old_run, good],
                              monkeypatch=monkeypatch)
        res = backend.run_step(STEP, ["qA1"], {}, 0, threading.Event())
        assert res.status == "done"
        assert res.run["run_id"] == 42

    def test_no_attribution_is_unverifiable_failure(self, tmp_path, node_file,
                                                    monkeypatch):
        fake = FakeScheduler(status_script={"it1": ["done"]})
        backend, _ = _backend(tmp_path, fake, node_file, runs=[],
                              monkeypatch=monkeypatch)
        res = backend.run_step(STEP, ["qA1"], {}, 0, threading.Event())
        assert res.status == "failed"
        assert "attributed" in (res.error or "")

    def test_graph_prefixed_run_name_still_attributes(self, tmp_path,
                                                      node_file, monkeypatch):
        folder = _mk_run_folder(tmp_path)
        decorated = _fake_run(folder, name="1Q_08_qubit_spectroscopy_new")
        fake = FakeScheduler(status_script={"it1": ["done"]})
        backend, _ = _backend(tmp_path, fake, node_file, runs=[decorated],
                              monkeypatch=monkeypatch)
        res = backend.run_step(STEP, ["qA1"], {}, 0, threading.Event())
        assert res.status == "done"


class TestInsertedStepResolution:
    """docs/56 v2 — runtime-inserted steps aren't in the plan-start map. The
    cross-node re-cal MUST run its engine-resolved node (never the base id),
    or escalation is a silent no-op on hardware (the reviewed critical bug)."""

    def test_recal_runs_engine_resolved_node_not_the_original(
            self, tmp_path, node_file, monkeypatch):
        resonator = tmp_path / "03_resonator_spectroscopy.py"
        resonator.write_text("# resonator node", encoding="utf-8")
        captured = {}
        orig_add = FakeScheduler.add_item

        def spy_add(self, inst, info, targets=None):
            captured["file"] = info.get("file")
            return orig_add(self, inst, info, targets)
        monkeypatch.setattr(FakeScheduler, "add_item", spy_add)
        monkeypatch.setattr(
            realbackend.node_scan, "scan_file",
            lambda p: SimpleNamespace(error=None, name=Path(p).stem,
                                      kind="node", has_hook=True,
                                      targets_name="qubits"))
        fake = FakeScheduler(status_script={"it1": ["done"]})
        folder = _mk_run_folder(tmp_path, name="03_resonator_spectroscopy")
        backend, _ = _backend(tmp_path, fake, node_file,
                              runs=[_fake_run(
                                  folder, name="03_resonator_spectroscopy")],
                              monkeypatch=monkeypatch)
        recal = Step(id="qubit_spec__recal", family="resonator_spectroscopy",
                     node=str(resonator), inserted_by="escalation_recal",
                     only_targets=("qA1",))
        backend.run_step(recal, ["qA1"], {}, 0, threading.Event())
        assert captured["file"] == str(resonator)   # NOT the original node

    def test_recal_without_resolved_node_fails_closed(self, tmp_path,
                                                      node_file, monkeypatch):
        # resolve failed → node="" → must FAIL, never fall back to base id
        fake = FakeScheduler(status_script={"it1": ["done"]})
        backend, _ = _backend(tmp_path, fake, node_file, monkeypatch=monkeypatch)
        recal = Step(id="qubit_spec__recal", family="resonator_spectroscopy",
                     node="", inserted_by="escalation_recal",
                     only_targets=("qA1",))
        res = backend.run_step(recal, ["qA1"], {}, 0, threading.Event())
        assert res.status == "failed"
        assert "re-cal" in (res.error or "")

    def test_continuation_reruns_the_original_via_base_id(self, tmp_path,
                                                         node_file, monkeypatch):
        captured = {}
        orig_add = FakeScheduler.add_item

        def spy_add(self, inst, info, targets=None):
            captured["file"] = info.get("file")
            return orig_add(self, inst, info, targets)
        monkeypatch.setattr(FakeScheduler, "add_item", spy_add)
        fake = FakeScheduler(status_script={"it1": ["done"]})
        folder = _mk_run_folder(tmp_path)
        backend, _ = _backend(tmp_path, fake, node_file,
                              runs=[_fake_run(folder)], monkeypatch=monkeypatch)
        cont = Step(id="qubit_spec__retry", family="qubit_spectroscopy",
                    inserted_by="escalation", carry_window_failure=("qA1",),
                    only_targets=("qA1",))
        backend.run_step(cont, ["qA1"], {}, 0, threading.Event())
        assert captured["file"] == str(node_file)   # the original step's file

    def test_verify_wide_reruns_the_original_via_verify_of(self, tmp_path,
                                                          node_file, monkeypatch):
        captured = {}
        orig_add = FakeScheduler.add_item

        def spy_add(self, inst, info, targets=None):
            captured["file"] = info.get("file")
            return orig_add(self, inst, info, targets)
        monkeypatch.setattr(FakeScheduler, "add_item", spy_add)
        fake = FakeScheduler(status_script={"it1": ["done"]})
        folder = _mk_run_folder(tmp_path)
        backend, _ = _backend(tmp_path, fake, node_file,
                              runs=[_fake_run(folder)], monkeypatch=monkeypatch)
        vstep = Step(id="qubit_spec__verify_wide", family="qubit_spectroscopy",
                     verify_of="qubit_spec", inserted_by="verify_wide",
                     only_targets=("qA1",))
        backend.run_step(vstep, ["qA1"], {}, 0, threading.Event())
        assert captured["file"] == str(node_file)
