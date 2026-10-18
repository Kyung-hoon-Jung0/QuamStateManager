"""v1.5.1 SB-F6: a chip folder moved while it held unapplied work.

Reopened at the new path the chip read "In sync" with no rows and no
notice; the draft and the tray rows stayed under the old folder's working
copy, reachable only by opening a path that is gone. The working-copy GC then
deleted that copy (its files equal its sync point: "clean") and left its tray
checkpoint orphaned.

Now the first open at the new path COPIES the old copy's unapplied work to
the chip as one refused checkpoint when the new folder holds the content the
old copy was synced with (R2 M5): the sync panel lists it, naming the old
folder, with Restore (staged, collision-checked) and Discard; a notice says
so once. GC keeps any copy that still has a checkpoint, and a copy it does
delete takes its tray bookkeeping with it.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray, working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _state():
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i} for i in (1, 2, 3)}
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


def _write(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


_APPS: dict = {}


def _app(inst):
    """The SM of this instance, its open chips forgotten (a restart). One app
    per instance: a second create_app would purge the old copy as a test
    leftover (its chip lived under the temp dir and is gone), which is the
    startup scrub's job, not the subject here."""
    routes._quam_cache.clear()
    app = _APPS.get(str(inst))
    if app is None:
        app = _APPS[str(inst)] = create_app(testing=True, instance_path=str(inst))
    app.config["contexts"].clear()
    app.config["active_context"] = None
    return app, app.test_client()


def _ctx(app):
    return app.config["contexts"][app.config["active_context"]]


@pytest.fixture
def moved(tmp_path):
    """A chip with one staged edit (q3) whose folder is then moved."""
    before = tmp_path / "before" / "quam_state"
    after = tmp_path / "after" / "quam_state"
    _write(before, _state())
    inst = tmp_path / "_inst"
    app, c = _app(inst)
    assert c.post("/load", data={"folder": str(before)}).status_code in (200, 302)
    r = c.post("/field/edit", data={"dot_path": "qubits.q3.T1", "value": "3.3e-05"})
    assert r.status_code == 200
    pending_tray.flush_all()
    old_key = _ctx(app)["working_copy"].key
    shutil.move(str(tmp_path / "before"), str(tmp_path / "after"))
    return {"inst": inst, "before": before, "after": after, "old_key": old_key,
            "tmp": tmp_path, "app": app, "c": c}


class TestTheWorkFollowsTheChip:
    def test_the_new_path_lists_the_left_behind_edit_and_says_so(self, moved):
        app, c = _app(moved["inst"])
        assert c.post("/load", data={"folder": str(moved["after"])}).status_code in (200, 302)
        ctx = _ctx(app)
        listed = pending_tray.unrecovered(ctx["working_copy"])
        assert [r["path"] for d in listed for r in d["rows"]] == ["qubits.q3.T1"], listed
        assert str(moved["before"]) in listed[0]["reason"], listed[0]["reason"]
        note = ctx.get("_tray_recovery_notice") or {}
        assert str(moved["before"]) in note.get("text", "") and "sync panel" in note["text"], note
        # Restore stages it (collision-checked against what the chip holds)
        r = c.post("/state/recovery/restore", data={"id": listed[0]["id"]})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        assert [e.dot_path for e in _ctx(app)["store"].change_log] == ["qubits.q3.T1"]
        assert _ctx(app)["store"].state["qubits"]["q3"]["T1"] == 3.3e-5

    def test_it_is_offered_once(self, moved):
        app, c = _app(moved["inst"])
        c.post("/load", data={"folder": str(moved["after"])})
        app, c = _app(moved["inst"])
        c.post("/load", data={"folder": str(moved["after"])})
        ctx = _ctx(app)
        listed = pending_tray.unrecovered(ctx["working_copy"])
        assert len(listed) == 1, listed                     # the first hand-over only
        assert not (ctx.get("_tray_recovery_notice") or {}).get("text"), "said again"

    def test_a_saved_draft_comes_over_too(self, tmp_path):
        before = tmp_path / "b1" / "quam_state"
        _write(before, _state())
        inst = tmp_path / "_inst"
        app, c = _app(inst)
        c.post("/load", data={"folder": str(before)})
        c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "2.2e-05"})
        assert c.post("/save").status_code == 200           # saved, not applied
        pending_tray.flush_all()
        shutil.move(str(tmp_path / "b1"), str(tmp_path / "a1"))
        app, c = _app(inst)
        c.post("/load", data={"folder": str(tmp_path / "a1" / "quam_state")})
        listed = pending_tray.unrecovered(_ctx(app)["working_copy"])
        rows = [r for d in listed for r in d["rows"]]
        assert [(r["path"], r["new"]) for r in rows] == [("qubits.q2.T1", 2.2e-5)], listed

    def test_saved_changes_that_no_longer_match_are_named_with_their_folder(self, tmp_path):
        before = tmp_path / "b2" / "quam_state"
        _write(before, _state())
        inst = tmp_path / "_inst"
        app, c = _app(inst)
        c.post("/load", data={"folder": str(before)})
        c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "2.2e-05"})
        c.post("/save")
        old_wf = str(_ctx(app)["working_copy"].working_folder)
        shutil.move(str(tmp_path / "b2"), str(tmp_path / "a2"))
        st = _state()
        st["qubits"]["q1"]["T1"] = 9.9e-6                   # the lab wrote since
        _write(tmp_path / "a2" / "quam_state", st)
        app, c = _app(inst)
        c.post("/load", data={"folder": str(tmp_path / "a2" / "quam_state")})
        note = (_ctx(app).get("_tray_recovery_notice") or {}).get("text", "")
        # R2 M5: no evidence of a move (the lab wrote since): nothing is
        # brought over, the notice names where the work stays
        assert "content differs" in note and old_wf in note, note
        assert pending_tray.unrecovered(_ctx(app)["working_copy"]) == []

    def test_another_chip_in_a_vanished_folder_is_left_alone(self, moved):
        other = moved["tmp"] / "other" / "quam_state"
        st = _state()
        w = dict(_WIRING, network={"host": "10.9.9.9", "cluster_name": "C9"})
        other.mkdir(parents=True)
        (other / "state.json").write_text(json.dumps(st), encoding="utf-8")
        (other / "wiring.json").write_text(json.dumps(w), encoding="utf-8")
        app, c = _app(moved["inst"])
        c.post("/load", data={"folder": str(other)})
        assert pending_tray.unrecovered(_ctx(app)["working_copy"]) == []


class TestTheCollectorKeepsCheckpointedCopies:
    def test_a_tray_only_copy_is_kept_with_its_checkpoint(self, moved):
        inst, key = moved["inst"], moved["old_key"]
        root = working_copy.working_state_root(inst)
        cp = root / f"{key}.pending_tray.json"
        assert cp.exists()
        res = working_copy.gc_working_copies(inst)
        assert (root / key).exists() and (root / f"{key}.meta.json").exists(), res
        assert cp.exists(), "the checkpoint was orphaned"

    def test_a_deleted_copy_takes_its_tray_bookkeeping(self, tmp_path):
        live = tmp_path / "c" / "quam_state"
        _write(live, _state())
        inst = tmp_path / "_inst"
        app, c = _app(inst)
        c.post("/load", data={"folder": str(live)})
        c.post("/field/edit", data={"dot_path": "qubits.q3.T1", "value": "3.3e-05"})
        pending_tray.flush_all()
        c.post("/discard_all")
        pending_tray.flush_all()
        key = _ctx(app)["working_copy"].key
        root = working_copy.working_state_root(inst)
        _app(inst)                                       # nothing open
        working_copy.gc_working_copies(inst)
        assert not (root / key).exists()
        assert not [p.name for p in root.glob(f"{key}.pending_tray*")], \
            [p.name for p in root.glob(f"{key}.pending_tray*")]
