"""v1.5.1 SA-F7: a pull or a forced write never takes or makes a pair that
never existed.

A lab writer that replaces state.json and then wiring.json leaves a moment
in which the folder holds the new state next to the previous save's wiring.
A pull inside that moment adopted the mixed pair as the working copy and the
sync point; a forced write (Keep mine) backed up the mixed pair and wrote
SM's pair, and the lab's late wiring.json then landed on top -- live held
SM's state with the lab's wiring.

Now a pair whose files are far apart in time while the newer one just landed
is waited for (pull, backup) or refused by name (the forced write's gate).
The guard is off in every other test (conftest); these pins turn it on.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import safe_io, working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

JSON = {"Accept": "application/json"}
OLD_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
LAB_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"},
              "wiring": {"qubits": {"q1": {"xy": {"opx_output": "#/ports/x/2"}}}}}
LAB_T1 = 1.9e-05


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch):
    monkeypatch.setattr(safe_io, "_MID_SAVE_WINDOW_S", 3.0)


def _state(t1=1.0e-5):
    return {"qubits": {"q1": {"id": "q1", "T1": t1}}, "qubit_pairs": {},
            "active_qubit_names": ["q1"]}


def _age(folder: Path, seconds: float = 120.0):
    """Both files as a save long ago left them."""
    t = time.time() - seconds
    for name in ("state.json", "wiring.json"):
        os.utime(folder / name, (t, t))


def _write_chip(folder: Path, state: dict, wiring: dict = OLD_WIRING):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    _age(folder)


def _replace(path: Path, data: dict):
    tmp = path.with_name(path.name + ".lab-new")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


class _SplitSave:
    """The lab's two-file save: state.json now, wiring.json *gap* s later."""

    def __init__(self, folder: Path, gap: float):
        self.folder, self.gap = folder, gap
        self.first = threading.Event()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        _replace(self.folder / "state.json", _state(LAB_T1))
        self.first.set()
        time.sleep(self.gap)
        _replace(self.folder / "wiring.json", LAB_WIRING)
        self.done.set()

    def __enter__(self):
        self.thread.start()
        assert self.first.wait(5)
        return self

    def __exit__(self, *exc):
        self.thread.join(10)


def _read(folder: Path):
    return (json.loads((folder / "state.json").read_text(encoding="utf-8")),
            json.loads((folder / "wiring.json").read_text(encoding="utf-8")))


# ---------------------------------------------------------------- stat rule

def test_a_half_saved_pair_is_named_and_a_whole_save_is_not(tmp_path):
    live = tmp_path / "live"
    _write_chip(live, _state())
    assert safe_io.live_mid_save(live) is None          # an old, settled pair
    _replace(live / "state.json", _state(LAB_T1))
    found = safe_io.live_mid_save(live)
    assert found is not None and found[0] == "state.json", found
    msg = safe_io.mid_save_message(found)
    assert "mid-save" in msg and "wiring.json has not followed" in msg
    _replace(live / "wiring.json", LAB_WIRING)          # the save completes
    assert safe_io.live_mid_save(live) is None


def test_a_settled_pair_is_read_without_waiting(tmp_path, monkeypatch):
    """The normal path: a pair written together (SM's own writes, a quam save
    whose files land a json.dump apart) costs no sleep at all."""
    live = tmp_path / "live"
    live.mkdir()
    (live / "wiring.json").write_text(json.dumps(OLD_WIRING), encoding="utf-8")
    t = time.time()
    os.utime(live / "wiring.json", (t - 0.3, t - 0.3))   # quam: wiring, then state
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    slept: list = []
    real = safe_io.time

    class _T:
        def __getattr__(self, n):
            return getattr(real, n)

        def sleep(self, s):
            slept.append(s)

    monkeypatch.setattr(safe_io, "time", _T())
    safe_io.read_state_wiring(live)
    assert safe_io.wait_pair_settled(live) is True
    assert slept == [], slept


def test_a_pair_read_waits_out_a_short_split(tmp_path):
    live = tmp_path / "live"
    _write_chip(live, _state())
    with _SplitSave(live, gap=0.15):
        state, wiring = safe_io.read_state_wiring(live)
    assert state["qubits"]["q1"]["T1"] == LAB_T1
    assert wiring == LAB_WIRING, "read the new state.json beside the old wiring.json"


# ---------------------------------------------------------------- pulls

def test_a_pull_inside_a_split_save_adopts_the_whole_pair(tmp_path):
    live = tmp_path / "live"
    _write_chip(live, _state())
    wc = working_copy.create(tmp_path / "_inst", live)
    with _SplitSave(live, gap=0.8):
        state, wiring = working_copy.sync_from_live(wc)
    assert state["qubits"]["q1"]["T1"] == LAB_T1
    assert wiring == LAB_WIRING, "the pull adopted a pair that never existed"
    w_state, w_wiring = _read(Path(wc.working_folder))
    assert w_wiring == LAB_WIRING and w_state["qubits"]["q1"]["T1"] == LAB_T1
    assert wc.synced_live_hash == working_copy.content_hash(*_read(live))


# ---------------------------------------------------------------- forced writes

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


def _versions(env) -> list[tuple]:
    hm = env["app"].config["history_manager"]
    out = []
    for meta in hm.list_snapshots(_ctx(env)["path"]):
        snap = hm.load_snapshot(_ctx(env)["path"], meta.timestamp)
        out.append((snap.state["qubits"]["q1"]["T1"], snap.wiring))
    return out


def test_keep_mine_inside_a_split_save_never_leaves_a_mixed_pair(env):
    with _SplitSave(env["live"], gap=0.8) as lab:
        r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
        lab_done_before_reply = lab.done.is_set()
    state, wiring = _read(env["live"])
    t1 = state["qubits"]["q1"]["T1"]
    assert not (t1 == 1.2e-05 and wiring == LAB_WIRING), (
        "live holds SM's state.json with the lab's late wiring.json -- a pair "
        "nobody wrote")
    assert lab_done_before_reply, "the write did not wait for the lab's save"
    if r.status_code == 200:
        assert t1 == 1.2e-05 and wiring == OLD_WIRING, (t1, wiring)
        # the backup the press took holds the lab's whole pair
        assert (LAB_T1, LAB_WIRING) in _versions(env), _versions(env)
        assert (LAB_T1, OLD_WIRING) not in _versions(env), "a backup of a half save"
    else:
        body = r.get_json()
        assert r.status_code == 409 and body["conflict"] == "live_mid_save", body


def test_a_forced_write_on_a_save_that_began_after_its_backup_is_refused_by_name(
        env, monkeypatch):
    hm = env["app"].config["history_manager"]
    real = hm.check_and_snapshot

    def snapshot_then_lab_starts(*a, **kw):
        out = real(*a, **kw)
        if kw.get("kind") == "backup":
            _replace(env["live"] / "state.json", _state(LAB_T1))   # first half only
        return out

    monkeypatch.setattr(hm, "check_and_snapshot", snapshot_then_lab_starts)
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    body = r.get_json()
    assert r.status_code == 409 and body["conflict"] == "live_mid_save", body
    assert "mid-save" in body["message"] and "Try again" in body["message"]
    state, wiring = _read(env["live"])
    assert state["qubits"]["q1"]["T1"] == LAB_T1 and wiring == OLD_WIRING   # untouched
    assert [e.dot_path for e in _ctx(env)["store"].change_log] == ["qubits.q1.T1"]
