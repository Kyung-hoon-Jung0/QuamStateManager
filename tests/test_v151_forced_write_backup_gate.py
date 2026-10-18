"""v1.5.1 SA-F1: a forced live write never destroys a pair no version holds.

A lab's "delete the live folder, then recreate it" save could land inside an
↑ Keep mine press: SM looked once, found no live files ("nothing to back
up"), spent its read retries, and then wrote over the pair the lab had just
recreated -- no snapshot held it and its undo unit said ``no_previous_state``.

Now "is there live content to lose" is decided under the chip's write lock,
right before the write: live files there must be exactly what the press's
backup holds, or nothing is written (a named 409). Pinned for Keep mine
(including the record's fsync window), restore-live, and the "nothing there"
case that must keep working.
"""
from __future__ import annotations

import json
import shutil
import time as _real_time
from pathlib import Path

import pytest

from quam_state_manager.core import doc_cache, hub, safe_io, undo_journal
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
JSON = {"Accept": "application/json"}


def _state(t1=1.0e-5, off=0.08):
    return {"qubits": {"q1": {"id": "q1", "T1": t1, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["q1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    r = c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.2e-05"})
    assert r.status_code == 200 and r.get_json()["ok"]
    return {"app": app, "client": c, "live": live}


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _live_t1(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]["T1"]


LAB_T1 = 1.9e-05


def _lab_recreates(env):
    _write_chip(env["live"], _state(t1=LAB_T1))


def _versions_holding_lab_pair(env) -> list[str]:
    hm = env["app"].config["history_manager"]
    out = []
    for meta in hm.list_snapshots(_ctx(env)["path"]):
        st = hm.load_snapshot(_ctx(env)["path"], meta.timestamp).state
        if st.get("qubits", {}).get("q1", {}).get("T1") == LAB_T1:
            out.append(meta.timestamp)
    return out


class _LabSleep:
    """``time`` for the read retries (doc_cache, safe_io): the first retry
    sleep AFTER SM decided the live folder had nothing to back up is when the
    lab's recreate lands -- the ~1 s of retries reading the missing pair is
    the window the field report measured."""

    def __init__(self, env, decided):
        self.env, self.decided, self.fired = env, decided, False

    def __getattr__(self, name):
        return getattr(_real_time, name)

    def sleep(self, s):
        if self.decided and not self.fired:
            self.fired = True
            _lab_recreates(self.env)
        _real_time.sleep(0)


def test_keep_mine_during_delete_and_recreate_never_overwrites_the_lab_pair(env, monkeypatch):
    shutil.rmtree(env["live"])                       # the lab's delete
    decided: list = []
    real_present = routes._live_files_present

    def present(wc):
        out = real_present(wc)
        decided.append(out)
        return out

    monkeypatch.setattr(routes, "_live_files_present", present)
    lab = _LabSleep(env, decided)
    monkeypatch.setattr(doc_cache, "time", lab)
    monkeypatch.setattr(safe_io, "time", lab)
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    assert decided and decided[0] is False, "the press decided while the folder was gone"
    assert lab.fired, "the lab's recreate landed during the read retries"
    lab_pair_kept = _live_t1(env) == LAB_T1
    backed_up = bool(_versions_holding_lab_pair(env))
    assert lab_pair_kept or backed_up, (
        f"the lab's recreated pair was overwritten with no version holding it "
        f"(status {r.status_code}, live T1 {_live_t1(env)})")
    assert lab_pair_kept, r.data
    body = r.get_json()
    assert r.status_code == 409 and body["conflict"] == "live_unbacked", body
    assert "Nothing was written" in body["message"]
    # the edit is not lost: still staged, still unapplied
    assert [e.dot_path for e in _ctx(env)["store"].change_log] == ["qubits.q1.T1"]
    assert _ctx(env).get("live_diverged") is True
    # no journal unit claims there was nothing before
    path = undo_journal.sidecar_path(env["app"].instance_path, _ctx(env)["path"])
    units, _cur = undo_journal.load_state(path)
    assert not any((u.get("meta") or {}).get("no_previous_state") for u in units), units


def test_a_recreate_during_the_record_fsync_is_refused_too(env, monkeypatch):
    shutil.rmtree(env["live"])
    real_commit = hub.Pending.commit

    def commit(self, *a, **kw):
        out = real_commit(self, *a, **kw)
        _lab_recreates(env)                          # lands while the line is fsync'd
        return out

    monkeypatch.setattr(hub.Pending, "commit", commit)
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    assert _live_t1(env) == LAB_T1 or _versions_holding_lab_pair(env), (
        f"overwritten unbacked (status {r.status_code})")
    assert _live_t1(env) == LAB_T1
    assert r.status_code == 409 and r.get_json()["conflict"] == "live_unbacked", r.data


def test_keep_mine_onto_a_folder_that_stays_empty_still_writes(env):
    # docs/86 point 7: nothing on the live chip to lose -> the push proceeds
    shutil.rmtree(env["live"])
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    assert r.status_code == 200, r.data
    assert _live_t1(env) == 1.2e-05


def test_keep_mine_over_a_backed_up_live_chip_still_writes(env):
    _write_chip(env["live"], _state(t1=3.3e-05))     # an outside write SM backs up
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    assert r.status_code == 200, r.data
    assert _live_t1(env) == 1.2e-05
    hm = env["app"].config["history_manager"]
    assert any(hm.load_snapshot(_ctx(env)["path"], m.timestamp).state["qubits"]["q1"]["T1"] == 3.3e-05
               for m in hm.list_snapshots(_ctx(env)["path"]))


def test_restore_live_refuses_a_write_that_landed_after_its_backup(env, monkeypatch):
    c = env["client"]
    hm = env["app"].config["history_manager"]
    hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
    ts = hm.list_snapshots(_ctx(env)["path"])[0].timestamp
    real_tree = routes._live_merged_tree

    def tree(wc):
        _lab_recreates(env)                          # a lab write after the backup
        return real_tree(wc)

    monkeypatch.setattr(routes, "_live_merged_tree", tree)
    r = c.post(f"/state-history/{ts}/restore-live?force=1")
    assert _live_t1(env) == LAB_T1 or _versions_holding_lab_pair(env), (
        f"the lab write was overwritten unbacked (status {r.status_code})")
    assert _live_t1(env) == LAB_T1
    assert r.status_code == 409, r.data
    assert b"Nothing was written" in r.data
