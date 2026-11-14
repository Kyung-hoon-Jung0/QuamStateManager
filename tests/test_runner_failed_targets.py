"""A finished Experiment Runner node that marked targets failed is LOUD.

The node's writes for a failed target already reached the chip (a person's
item runs against the live folder, and the write is kept -- the person pressed
run and chooses). The queue item names each failed target and what was written
for it, and one press puts back exactly those values through the
compare-and-swap revert, refusing a value that moved since the run.
"""
import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core import scheduler
from tests.state_model.repros._rig import Chip


def _patch(q, key, old, new):
    return {"op": "replace", "path": f"/quam/qubits/{q}/{key}", "value": new, "old": old}


class TestFailedTargetWrites:
    def test_all_successful_changes_nothing(self):
        assert scheduler.failed_target_writes(
            {"q1": "successful", "q2": "successful"},
            [_patch("q1", "f_01", 1, 2)]) is None

    def test_no_outcomes_changes_nothing(self):
        assert scheduler.failed_target_writes({}, [_patch("q1", "f_01", 1, 2)]) is None
        assert scheduler.failed_target_writes(None, [_patch("q1", "f_01", 1, 2)]) is None

    def test_only_the_failed_targets_writes_are_listed(self):
        info = scheduler.failed_target_writes(
            {"q1": "failed", "q2": "successful", "q10": "successful"},
            [_patch("q1", "f_01", 1, 2), _patch("q2", "f_01", 3, 4),
             _patch("q10", "T1", 5, 6), _patch("q1", "T1", 7, 8)])
        assert info["failed"] == ["q1"] and info["n_targets"] == 3
        assert [w["dotted"] for w in info["writes"]] == ["qubits.q1.f_01", "qubits.q1.T1"]
        note = scheduler.failed_targets_note(info)
        assert note.startswith("1 of 3 targets failed")
        assert "qubits.q1.f_01 (1 -> 2)" in note and "q2" not in note and "q10" not in note

    def test_writes_naming_no_target_are_named_and_never_reverted(self):
        stray = [{"op": "replace", "path": f"/quam/ports/p{i}/offset", "value": i, "old": 0}
                 for i in range(7)]
        info = scheduler.failed_target_writes(
            {"q1": "failed", "q2": "successful"},
            [_patch("q1", "f_01", 1, 2), _patch("q2", "f_01", 3, 4)] + stray)
        assert [w["dotted"] for w in info["writes"]] == ["qubits.q1.f_01"]
        assert info["untied"] == [f"ports.p{i}.offset" for i in range(7)]
        note = scheduler.failed_targets_note(info)
        assert "7 further writes could not be tied to a target" in note
        assert "ports.p4.offset" in note and "ports.p5.offset" not in note
        assert "and 2 more" in note

    def test_a_failed_target_that_wrote_nothing_says_so(self):
        info = scheduler.failed_target_writes({"q1": "failed", "q2": "successful"},
                                              [_patch("q2", "f_01", 3, 4)])
        assert info["writes"] == []
        assert "q1: wrote nothing" in scheduler.failed_targets_note(info)


@pytest.fixture
def chip(tmp_path):
    return Chip(tmp_path)


def _inst(chip):
    from quam_state_manager.web import routes
    with chip.app.test_request_context():
        return routes._sched_inst()


_ITEM_START = "2026-11-13T10:00:00+00:00"
_ITEM_END = "2026-11-13T10:05:00+00:00"


def _run_hook(chip, monkeypatch, tmp_path, outcomes, patches, *, item_info=None,
              name="05_qubit_spec", run_created="2026-11-13T19:01:00+09:00"):
    from quam_state_manager.web import routes
    inst = _inst(chip)
    info = {"name": name, "kind": "node"}
    info.update(item_info or {})
    it = scheduler.add_item(inst, info, ["q1", "q2"])
    with scheduler._QLOCK:      # the worker stamps these around the node's run
        st = scheduler.load_queue(inst)
        q = scheduler._find(st, it["id"])
        q["started_at"], q["ended_at"], q["status"] = _ITEM_START, _ITEM_END, "done"
        scheduler.save_queue(inst, st)
    run_dir = tmp_path / "run7"
    run_dir.mkdir()
    (run_dir / "node.json").write_text(json.dumps({"patches": patches}), encoding="utf-8")
    run = SimpleNamespace(outcomes=outcomes, folder_path=run_dir, experiment_name=name, run_id=7,
                          created_at=run_created, run_start=None, instant_us=None)
    monkeypatch.setattr(routes, "_latest_run_info",
                        lambda: (run, {"uid": "u7", "run_id": 7, "name": name}))
    monkeypatch.setattr(routes, "_active_dataset_stores", lambda: [])
    monkeypatch.setattr(routes, "_enqueue_exp_ingest", lambda *a, **k: None)
    routes._scheduler_refresh_hook(chip.app, inst, str(chip.live), it["id"], "done")
    return inst, scheduler._find(scheduler.load_queue(inst), it["id"])


class TestTheHookRecordsFailedTargets:
    def test_a_partial_failure_is_recorded_without_any_rule(self, chip, monkeypatch, tmp_path):
        inst, item = _run_hook(chip, monkeypatch, tmp_path,
                               {"q1": "failed", "q2": "successful"},
                               [_patch("q1", "f_01", 5.0e9, 5.1e9), _patch("q2", "f_01", 5.2e9, 5.3e9)])
        assert not item.get("on_outcome")
        assert item["failed_targets"] == ["q1"] and item["n_targets"] == 2
        assert [w["dotted"] for w in item["failed_writes"]] == ["qubits.q1.f_01"]
        assert "qubits.q1.f_01" in item["outcome_note"] and "q2" not in item["outcome_note"]
        assert item["failed_folder"] == str(chip.live)

    def test_a_run_inside_the_window_edge_slack_counts(self, chip, monkeypatch, tmp_path):
        # 3 s before the item's start: clock granularity, still this item's run
        inst, item = _run_hook(chip, monkeypatch, tmp_path, {"q1": "failed"},
                               [_patch("q1", "f_01", 1, 2)],
                               run_created="2026-11-13T09:59:57+00:00")
        assert item["failed_targets"] == ["q1"]

    @pytest.mark.parametrize("created", ["2026-11-13T09:58:00+00:00",   # before the item ran
                                         "2026-11-13T19:06:00+09:00",   # after it ended
                                         None])                          # unknown: fail closed
    def test_a_same_named_run_outside_the_window_is_not_this_items(
            self, chip, monkeypatch, tmp_path, created):
        inst, item = _run_hook(chip, monkeypatch, tmp_path, {"q1": "failed"},
                               [_patch("q1", "f_01", 1, 2)], run_created=created)
        assert "failed_targets" not in item and item["outcome_note"] is None
        r = chip.c.post(f"/scheduler/item/{item['id']}/revert-failed", json={})
        assert r.status_code == 409

    def test_all_successful_leaves_the_item_as_today(self, chip, monkeypatch, tmp_path):
        inst, item = _run_hook(chip, monkeypatch, tmp_path,
                               {"q1": "successful", "q2": "successful"},
                               [_patch("q1", "f_01", 1, 2)])
        assert "failed_targets" not in item and item["outcome_note"] is None

    def test_no_outcomes_leaves_the_item_as_today(self, chip, monkeypatch, tmp_path):
        inst, item = _run_hook(chip, monkeypatch, tmp_path, {}, [_patch("q1", "f_01", 1, 2)])
        assert "failed_targets" not in item and item["outcome_note"] is None

    def test_an_agent_item_on_a_scratch_copy_is_not_flagged(self, chip, monkeypatch, tmp_path):
        inst, item = _run_hook(chip, monkeypatch, tmp_path, {"q1": "failed"},
                               [_patch("q1", "f_01", 1, 2)],
                               item_info={"state_path": str(tmp_path / "scratch")})
        assert "failed_targets" not in item


def _record(chip, writes_for, folder=None):
    inst = _inst(chip)
    it = scheduler.add_item(inst, {"name": "05_qubit_spec", "kind": "node"}, ["q1", "q2"])
    info = scheduler.failed_target_writes({"q1": "failed", "q2": "successful"}, writes_for)
    scheduler.set_item_failed_targets(inst, it["id"], info, folder or str(chip.live))
    with scheduler._QLOCK:      # the worker finished the item
        st = scheduler.load_queue(inst)
        scheduler._find(st, it["id"])["status"] = "done"
        scheduler.save_queue(inst, st)
    return inst, it["id"]


class TestRevertTheFailedTargets:
    def _node_wrote(self, chip):
        """The node's live write: q1 (failed) and q2 (successful) both moved."""
        chip.set_live("qubits.q1.f_01", 5.11e9)
        chip.set_live("qubits.q1.T1", 3.0e-05)
        chip.set_live("qubits.q2.f_01", 5.33e9)
        return [_patch("q1", "f_01", 5.01e9, 5.11e9), _patch("q1", "T1", 2.1e-05, 3.0e-05),
                _patch("q2", "f_01", 5.13e9, 5.33e9)]

    def test_only_the_failed_targets_values_come_back(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        body = r.get_json()
        assert r.status_code == 200 and body["ok"], body
        live = chip.live_state()["qubits"]
        assert live["q1"]["f_01"] == 5.01e9 and live["q1"]["T1"] == 2.1e-05
        assert live["q2"]["f_01"] == 5.33e9          # a successful target is never reverted
        assert {x["path"] for x in body["result"]["reverted"]} == {"qubits.q1.f_01", "qubits.q1.T1"}
        item = scheduler._find(scheduler.load_queue(inst), iid)
        assert item["failed_revert"]["ok"] and not item["failed_revert"]["refused"]

    def test_a_value_edited_since_is_refused_and_named(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        chip.set_live("qubits.q1.T1", 9.9e-05)        # the person changed it after the run
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        body = r.get_json()
        live = chip.live_state()["qubits"]
        assert live["q1"]["T1"] == 9.9e-05            # kept
        assert live["q1"]["f_01"] == 5.01e9           # the other leaf still reverts
        assert [c["path"] for c in body["result"]["refused"]] == ["qubits.q1.T1"]
        assert "qubits.q1.T1" in body["message"] and "not reverted" in body["message"]
        # gl-aat: plain words for a person; the machine reason and code are kept
        assert "CAS" not in body["message"] and "patch" not in body["message"]
        assert "the live value changed since the run (now 9.9e-05)" in body["message"]
        ref = body["result"]["refused"][0]
        assert ref["code"] == "changed" and "CAS" in ref["reason"]
        assert body["result"]["remaining"] == []       # the button goes: nothing a press could put back

    def test_a_full_revert_leaves_nothing_to_offer(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        body = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        assert body["ok"] and body["result"]["remaining"] == []
        again = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        assert again.status_code == 409 and "nothing is left" in again.get_json()["error"]

    def test_a_second_press_reverts_only_what_is_left(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        # a first revert that stopped as a whole leaves both values to put back
        scheduler.set_item_failed_revert(inst, iid, {"ok": False, "reverted": [], "remaining": ["qubits.q1.T1"],
                                                     "refused": [{"path": None, "reason": "x"}]})
        chip.set_live("qubits.q1.f_01", 5.0e9)        # moved away since: not offered
        body = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        assert [x["path"] for x in body["result"]["reverted"]] == ["qubits.q1.T1"], body
        assert chip.live_state()["qubits"]["q1"]["f_01"] == 5.0e9     # never written

    def _status_item(self, chip, iid):
        st = chip.c.get("/scheduler/status").get_json()
        return [x for x in st["queue"] if x["id"] == iid][0]

    def test_a_refused_value_that_comes_back_is_retried(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        chip.set_live("qubits.q1.T1", 9.9e-05)
        first = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        assert first["result"]["remaining"] == []
        assert self._status_item(chip, iid)["revert_retryable"] == []
        chip.set_live("qubits.q1.T1", 3.0e-05)         # holds the run's value again
        chip.c.post("/state/sync", data={"mode": "pull"})
        assert self._status_item(chip, iid)["revert_retryable"] == ["qubits.q1.T1"]
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        body = r.get_json()
        assert r.status_code == 200 and [x["path"] for x in body["result"]["reverted_now"]] == \
            ["qubits.q1.T1"], body
        assert chip.live_state()["qubits"]["q1"]["T1"] == 2.1e-05
        res = body["result"]
        # accumulated across presses: both reverted, the earlier refusal superseded
        assert {x["path"] for x in res["reverted"]} == {"qubits.q1.f_01", "qubits.q1.T1"}
        assert res["refused"] == [] and res["remaining"] == []
        assert len(res["presses"]) == 1 and "not reverted" in res["presses"][0]["message"]
        assert self._status_item(chip, iid)["revert_retryable"] == []

    def test_a_value_that_moved_away_and_stayed_offers_nothing(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        chip.set_live("qubits.q1.T1", 9.9e-05)
        chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        chip.c.post("/state/sync", data={"mode": "pull"})
        assert self._status_item(chip, iid)["revert_retryable"] == []
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        assert r.status_code == 409 and "nothing is left to revert" in r.get_json()["error"]
        assert chip.live_state()["qubits"]["q1"]["T1"] == 9.9e-05

    def test_earlier_classifications_are_kept_across_presses(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        scheduler.set_item_failed_revert(inst, iid, {
            "ok": False, "reverted": [], "remaining": ["qubits.q1.T1"], "message": "first",
            "unchanged": [{"path": "qubits.q9.x", "value": 1}],
            "refused": [{"path": "qubits.q8.y", "reason": "r", "text": "t"}]})
        res = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()["result"]
        assert [u["path"] for u in res["unchanged"]] == ["qubits.q9.x"]
        assert [c["path"] for c in res["refused"]] == ["qubits.q8.y"]
        assert res["presses"][-1]["message"] == "first"

    def test_a_fresh_row_has_no_retry_stamp_and_blind_writes_never_retry(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        assert "revert_retryable" not in self._status_item(chip, iid)
        from quam_state_manager.web.routes import _revert_retryable_paths
        ws = [{"dotted": "a", "has_old": False, "value": 2}, {"dotted": "b", "old": 2, "value": 2},
              {"dotted": "c", "old": 1, "value": 2}]
        assert _revert_retryable_paths(ws, lambda k: 2) == ["c"]

    def test_a_legacy_record_keeps_what_was_reverted_and_never_calls_it_changed(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        chip.set_live("qubits.q1.T1", 9.9e-05)        # changed outside after the run
        first = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        assert [x["path"] for x in first["result"]["reverted"]] == ["qubits.q1.f_01"]
        # a record written before `remaining` existed
        legacy = dict(first["result"]); legacy.pop("remaining")
        for k in ("reverted_now", "already_reverted", "unchanged", "presses"):
            legacy.pop(k, None)
        scheduler.set_item_failed_revert(inst, iid, legacy)
        body = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        res = body["result"]
        assert [c["path"] for c in res["refused"]] == ["qubits.q1.T1"]   # still changed, still named
        assert [x["path"] for x in res["reverted"]] == ["qubits.q1.f_01"]  # earlier outcome kept
        assert res["reverted_now"] == [] and res["remaining"] == []
        assert "reverted earlier 1: qubits.q1.f_01" in body["message"]
        assert "qubits.q1.f_01: the live value changed" not in body["message"]
        assert res["presses"] and res["presses"][-1]["message"] == legacy["message"]
        assert chip.live_state()["qubits"]["q1"]["T1"] == 9.9e-05        # never written
        item = scheduler._find(scheduler.load_queue(inst), iid)
        assert [x["path"] for x in item["failed_revert"]["reverted"]] == ["qubits.q1.f_01"]

    def test_a_value_already_back_at_its_old_value_is_not_attempted(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        chip.set_live("qubits.q1.T1", 2.1e-05)        # put back by hand
        body = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        res = body["result"]
        assert body["ok"], body
        assert [x["path"] for x in res["reverted"]] == ["qubits.q1.f_01"]
        assert [a["path"] for a in res["already_reverted"]] == ["qubits.q1.T1"]
        assert res["refused"] == [] and res["remaining"] == []
        assert "already reverted 1: qubits.q1.T1" in body["message"]

    def test_a_write_that_changed_nothing_is_not_counted_as_reverted(self, chip):
        chip.set_live("qubits.q1.f_01", 5.11e9)
        inst, iid = _record(chip, [_patch("q1", "f_01", 5.11e9, 5.11e9)])
        body = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={}).get_json()
        res = body["result"]
        assert res["reverted"] == [] and res["already_reverted"] == []
        assert [u["path"] for u in res["unchanged"]] == ["qubits.q1.f_01"]
        assert res["remaining"] == [] and "unchanged by the run 1" in body["message"]

    def test_unapplied_edits_refuse_before_writing(self, chip):
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        chip.c.post("/state/sync", data={"mode": "pull"})
        chip.edit("qubits.q3.f_01", "5.5e9")
        before = chip.live_state()
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        err = r.get_json()["error"]
        assert r.status_code == 409 and "unapplied" in err and "apply or discard them first" in err
        assert chip.live_state() == before

    def test_a_running_queue_refuses_and_says_to_wait(self, chip, monkeypatch):
        from quam_state_manager.web import routes
        patches = self._node_wrote(chip)
        inst, iid = _record(chip, patches)
        before = chip.live_state()
        monkeypatch.setattr(routes.scheduler, "is_active", lambda *a, **k: True)
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        body = r.get_json()
        assert r.status_code == 409 and body["error"] == "scheduler_running"
        assert "Experiment Runner is running" in body["message"]
        assert "until it finishes or is cancelled" in body["message"]
        assert chip.live_state() == before

    def test_nothing_written_for_a_failed_target_is_a_409(self, chip):
        inst, iid = _record(chip, [_patch("q2", "f_01", 1, 2)])
        r = chip.c.post(f"/scheduler/item/{iid}/revert-failed", json={})
        assert r.status_code == 409


def test_the_queue_row_says_it_and_offers_the_revert():
    import os
    import subprocess
    from pathlib import Path
    r = subprocess.run(["node", str(Path(__file__).with_name("runner_failed_targets_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8", timeout=60,
                       env=dict(os.environ, NODE_PATH=os.environ.get("NODE_PATH") or str(
                           Path(__file__).resolve().parents[1] / "node_modules")))
    assert r.returncode == 0 and "16 checks passed" in r.stdout, r.stdout + r.stderr


class TestCompactRowNamesTheMetricTarget:
    def _run(self, outcomes):
        from pathlib import Path
        from quam_state_manager.core.dataset import RunInfo, _compact_row
        run = RunInfo(run_id=1, experiment_name="t1", date="2026-01-01", time="01:00:00",
                      folder_path=Path("."), outcomes=outcomes,
                      fit_results={"q10": {"T1": 1e-5}, "q2": {"T1": 2e-5}})
        run.key_metric = "20 us"
        return _compact_row(run)

    def test_a_failure_names_the_lowest_target(self):
        assert self._run({"q2": "failed", "q10": "successful"})["mq"] == "q2"

    def test_a_clean_run_row_is_unchanged(self):
        assert "mq" not in self._run({"q2": "successful", "q10": "successful"})
        assert "mq" not in self._run({})
