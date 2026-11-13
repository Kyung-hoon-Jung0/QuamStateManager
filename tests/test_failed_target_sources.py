"""A failed target's writes are found wherever the run recorded them.

QUAlibrate records a run's writes in one of three places: node.json
``patches`` (older releases), node.json ``state_changes.changes`` and the run's
``state_updates.json`` ``operations``. The failed-target note and its revert
read them through one reader, first source present wins. The fixtures below
copy the exact shape of real runs (keys, nesting, extra fields) with generic
ids and values.
"""
import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core import fit_targets, scheduler
from tests.state_model.repros._rig import Chip

# (path under qubits.<q>, old, new) -- q1 is the failed target, q2 succeeded
_Q1 = [("xy/RF_frequency", 5.0100e9, 5.0103e9), ("f_01", 5.0100e9, 5.0103e9),
       ("resonator/operations/readout/integration_weights_angle", 2.6, 2.4)]
_Q2 = [("xy/RF_frequency", 5.5900e9, 5.5901e9), ("f_01", 5.5900e9, 5.5901e9),
       ("resonator/operations/readout/integration_weights_angle", -0.01, 6.27)]
_OUTCOMES = {"q1": "failed", "q2": "successful"}


def _writes():
    return [(q, p, o, n) for q, rows in (("q1", _Q1), ("q2", _Q2)) for p, o, n in rows]


def _state_changes(*, drop_old=()):
    """node.json ``state_changes`` exactly as the node writes it."""
    changes = []
    for q, p, o, n in _writes():
        c = {"key": f"#/qubits/{q}/{p}", "attr": p.rsplit("/", 1)[-1], "old": o, "new": n,
             "accepted_at": "2026-01-01T00:00:00.000000"}
        if (q, p) in drop_old:
            del c["old"]
        changes.append(c)
    return {"recorded_at": "2026-01-01T00:00:00.000000", "changes": changes}


def _state_updates(*, drop_old=()):
    """``state_updates.json`` exactly as the node writes it."""
    ops = []
    for q, p, o, n in _writes():
        op = {"op": "replace", "path": f"/quam/qubits/{q}/{p}", "value": n, "old": o}
        if (q, p) in drop_old:
            del op["old"]
        ops.append(op)
    return {"source": "node.state_updates at save time (proposals, not necessarily accepted)",
            "node": "08_qubit_spectroscopy", "run_id": 7, "operations": ops}


def _node(**extra):
    return {"created_at": "2026-01-01T00:00:00", "metadata": {"name": "08_qubit_spectroscopy"},
            "data": {"outcomes": dict(_OUTCOMES)}, "id": 7, "parents": [], **extra}


def _run_dir(tmp_path, node, updates=None):
    d = tmp_path / "run7"
    d.mkdir()
    (d / "node.json").write_text(json.dumps(node), encoding="utf-8")
    if updates is not None:
        (d / "state_updates.json").write_text(json.dumps(updates), encoding="utf-8")
    return d


Q1_DOTTED = {"qubits.q1.xy.RF_frequency", "qubits.q1.f_01",
             "qubits.q1.resonator.operations.readout.integration_weights_angle"}


class TestTheOneReader:
    def test_patches_win_over_the_other_sources(self, tmp_path):
        patch = {"op": "replace", "path": "/quam/qubits/q1/T1", "value": 2, "old": 1}
        d = _run_dir(tmp_path, _node(patches=[patch], state_changes=_state_changes()),
                     _state_updates())
        writes, source = fit_targets.run_writes(d)
        assert source == "patches"
        assert writes == [{"path": "qubits.q1.T1", "old": 1, "new": 2, "has_old": True}]

    def test_state_changes_win_over_state_updates(self, tmp_path):
        d = _run_dir(tmp_path, _node(state_changes=_state_changes()), _state_updates())
        writes, source = fit_targets.run_writes(d)
        assert source == "state_changes" and len(writes) == 6
        assert writes[1] == {"path": "qubits.q1.f_01", "old": 5.0100e9, "new": 5.0103e9,
                             "has_old": True}

    def test_state_updates_alone(self, tmp_path):
        d = _run_dir(tmp_path, _node(), _state_updates())
        writes, source = fit_targets.run_writes(d)
        assert source == "state_updates" and len(writes) == 6
        assert {w["path"] for w in writes[:3]} == Q1_DOTTED

    def test_no_source_at_all(self, tmp_path):
        assert fit_targets.run_writes(_run_dir(tmp_path, _node())) == ([], None)

    def test_a_missing_old_is_marked(self, tmp_path):
        d = _run_dir(tmp_path, _node(state_changes=_state_changes(drop_old={("q1", "f_01")})))
        writes, _ = fit_targets.run_writes(d)
        assert [w["has_old"] for w in writes].count(False) == 1
        assert next(w for w in writes if w["path"] == "qubits.q1.f_01")["has_old"] is False


@pytest.fixture
def chip(tmp_path):
    return Chip(tmp_path / "rig")


_ITEM_START = "2026-11-13T10:00:00+00:00"
_ITEM_END = "2026-11-13T10:05:00+00:00"


def _hook(chip, monkeypatch, run_dir):
    """Run the post-node hook on *run_dir* as this item's run; returns the item."""
    from quam_state_manager.web import routes
    with chip.app.test_request_context():
        inst = routes._sched_inst()
    name = "08_qubit_spectroscopy"
    it = scheduler.add_item(inst, {"name": name, "kind": "node"}, ["q1", "q2"])
    with scheduler._QLOCK:
        st = scheduler.load_queue(inst)
        q = scheduler._find(st, it["id"])
        q["started_at"], q["ended_at"], q["status"] = _ITEM_START, _ITEM_END, "done"
        scheduler.save_queue(inst, st)
    run = SimpleNamespace(outcomes=dict(_OUTCOMES), folder_path=run_dir, experiment_name=name,
                          run_id=7, created_at="2026-11-13T10:01:00+00:00", run_start=None,
                          instant_us=None)
    monkeypatch.setattr(routes, "_latest_run_info",
                        lambda: (run, {"uid": "u7", "run_id": 7, "name": name}))
    monkeypatch.setattr(routes, "_active_dataset_stores", lambda: [])
    monkeypatch.setattr(routes, "_enqueue_exp_ingest", lambda *a, **k: None)
    routes._scheduler_refresh_hook(chip.app, inst, str(chip.live), it["id"], "done")
    return inst, scheduler._find(scheduler.load_queue(inst), it["id"])


def _node_wrote_live(chip):
    """The node's live write for both targets."""
    for q, p, _o, n in _writes():
        chip.set_live(f"qubits.{q}." + p.replace("/", "."), n)


def _leaf(state, q, p):
    cur = state["qubits"][q]
    for s in p.split("/"):
        cur = cur[s]
    return cur


@pytest.mark.parametrize("files", ["state_changes", "state_updates"])
class TestEachSourceListsAndReverts:
    def _dir(self, tmp_path, files, **kw):
        if files == "state_changes":
            return _run_dir(tmp_path, _node(state_changes=_state_changes(**kw)))
        return _run_dir(tmp_path, _node(), _state_updates(**kw))

    def test_failed_writes_are_listed_and_revert(self, chip, monkeypatch, tmp_path, files):
        _node_wrote_live(chip)
        inst, item = _hook(chip, monkeypatch, self._dir(tmp_path, files))
        assert item["failed_targets"] == ["q1"]
        assert {w["dotted"] for w in item["failed_writes"]} == Q1_DOTTED
        assert "wrote nothing" not in item["outcome_note"]
        assert "still wrote" in item["outcome_note"] and "q2" not in item["outcome_note"]
        r = chip.c.post(f"/scheduler/item/{item['id']}/revert-failed", json={})
        body = r.get_json()
        assert r.status_code == 200 and body["ok"], body
        live = chip.live_state()
        for p, o, _n in _Q1:
            assert _leaf(live, "q1", p) == o
        for p, _o, n in _Q2:
            assert _leaf(live, "q2", p) == n          # a successful target is never reverted

    def test_live_changed_since_refuses_that_leaf(self, chip, monkeypatch, tmp_path, files):
        _node_wrote_live(chip)
        inst, item = _hook(chip, monkeypatch, self._dir(tmp_path, files))
        chip.set_live("qubits.q1.f_01", 5.0200e9)    # someone moved it after the run
        body = chip.c.post(f"/scheduler/item/{item['id']}/revert-failed", json={}).get_json()
        live = chip.live_state()
        assert live["qubits"]["q1"]["f_01"] == 5.0200e9
        assert live["qubits"]["q1"]["xy"]["RF_frequency"] == 5.0100e9
        assert [c["path"] for c in body["result"]["refused"]] == ["qubits.q1.f_01"]

    def test_a_missing_old_is_named_and_never_reverted_blind(
            self, chip, monkeypatch, tmp_path, files):
        _node_wrote_live(chip)
        inst, item = _hook(chip, monkeypatch,
                           self._dir(tmp_path, files, drop_old={("q1", "f_01")}))
        assert "did not record the old value of qubits.q1.f_01" in item["outcome_note"]
        body = chip.c.post(f"/scheduler/item/{item['id']}/revert-failed", json={}).get_json()
        live = chip.live_state()
        assert live["qubits"]["q1"]["f_01"] == 5.0103e9       # still the node's value
        assert live["qubits"]["q1"]["xy"]["RF_frequency"] == 5.0100e9
        refused = body["result"]["refused"]
        assert [c["path"] for c in refused] == ["qubits.q1.f_01"]
        assert "did not record" in refused[0]["reason"]


def test_patches_behave_as_before(chip, monkeypatch, tmp_path):
    patches = [{"op": "replace", "path": "/quam/qubits/q1/f_01", "value": 5.0103e9,
                "old": 5.0100e9},
               {"op": "replace", "path": "/quam/qubits/q2/f_01", "value": 5.5901e9,
                "old": 5.5900e9}]
    chip.set_live("qubits.q1.f_01", 5.0103e9)
    inst, item = _hook(chip, monkeypatch,
                       _run_dir(tmp_path, _node(patches=patches,
                                                state_changes=_state_changes())))
    assert [w["dotted"] for w in item["failed_writes"]] == ["qubits.q1.f_01"]
    assert item["failed_writes"][0]["path"] == "qubits.q1.f_01"
    body = chip.c.post(f"/scheduler/item/{item['id']}/revert-failed", json={}).get_json()
    assert body["ok"] and chip.live_state()["qubits"]["q1"]["f_01"] == 5.0100e9


def test_no_write_source_says_wrote_nothing(chip, monkeypatch, tmp_path):
    inst, item = _hook(chip, monkeypatch, _run_dir(tmp_path, _node()))
    assert item["failed_targets"] == ["q1"] and item["failed_writes"] == []
    assert "q1: wrote nothing" in item["outcome_note"]
    r = chip.c.post(f"/scheduler/item/{item['id']}/revert-failed", json={})
    assert r.status_code == 409
