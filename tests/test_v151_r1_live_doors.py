"""v1.5.1 fix round 1 review: the live-write doors after a refused or failed write.

M2. restore-live wrote the version into the working folder BEFORE its live
write, and a refused or failed write never put it back: the store and the tray
stayed stale against their files, so every later Apply and Save was refused
("another SM window changed the shared working copy... Take live"). Now the
working pair goes back byte for byte; a stale refusal also marks the chip
diverged. Pinned by pressing Save and Apply AFTER the failed restore.

M3. Only Apply and restore-live took the read-only pill down after a landed
write. Pull & apply, the Auto-Sync merge (the same door), dataset Apply to
chip, the Ctrl+Z walk to live and the Auto Calibrate writer now do too.

MINOR 1. A same-content re-save (or a touch) of the live pair during a forced
write's journal fsync is not "the live files changed": the content decides.
MINOR 2. A forced write whose "before" read failed says so, not "live files
appeared... it had found none".
MINOR 3. The pill is raised only by the LIVE folder refusing: never by SM's own
history journal, nor by the working folder.
MINOR 6. The recovery panel's Restore said "Nothing is written to the live
chip until Apply" while Auto-Sync push was armed -- it writes at once.
MINOR 10. A failed pull is a named 409, never a 500.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from quam_state_manager.core import hub, pending_tray, safe_io, working_copy
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


def _readonly(folder: Path, on: bool):
    for name in ("state.json", "wiring.json"):
        os.chmod(folder / name, stat.S_IREAD if on else (stat.S_IREAD | stat.S_IWRITE))


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    e = {"app": app, "client": c, "live": live, "tmp": tmp_path}
    yield e
    _readonly(live, False)


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _edit(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _live_q1(env, key):
    q = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["q1"]
    return q["z"]["joint_offset"] if key == "joint_offset" else q[key]


def _version_then_edit(env):
    """A version A (T1 = 0.5e-5) in history, live moved on (T1 = 1e-5),
    taken, and one staged edit -- the scene restore-live is pressed in."""
    hm = env["app"].config["history_manager"]
    _write_chip(env["live"], _state(t1=0.5e-5))
    r = env["client"].post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code == 200, r.data
    hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
    ts = hm.list_snapshots(_ctx(env)["path"])[0].timestamp
    _write_chip(env["live"], _state(t1=1.0e-5))
    r = env["client"].post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code == 200, r.data
    _edit(env, "qubits.q1.z.joint_offset", "0.09")
    return ts


def _working_file_t1(env):
    wf = Path(_ctx(env)["working_copy"].working_folder) / "state.json"
    return json.loads(wf.read_text(encoding="utf-8"))["qubits"]["q1"]["T1"]


# --------------------------------------------------------------------------
# M2: restore-live never leaves SM stuck
# --------------------------------------------------------------------------

def test_a_denied_restore_live_leaves_save_and_apply_working(env):
    ts = _version_then_edit(env)
    _readonly(env["live"], True)
    r = env["client"].post(f"/state-history/{ts}/restore-live?force=1")
    assert r.status_code == 409, r.data[:400]
    ctx = _ctx(env)
    # the store, the tray and the working FILES agree again
    assert _working_file_t1(env) == ctx["store"].get_value("qubits.q1.T1") == 1.0e-5
    assert [e.dot_path for e in ctx["store"].change_log] == ["qubits.q1.z.joint_offset"]
    assert not routes._shared_working_changed(ctx)
    # Save works (it used to be refused as "another window changed the working copy")
    r = env["client"].post("/save")
    assert r.status_code == 200, r.data[:400]
    assert b"Another SM window" not in r.data
    assert not ctx["store"].change_log and ctx.get("working_dirty")
    # ...and once the folder is writable again, Apply lands the edit
    _readonly(env["live"], False)
    r = env["client"].post("/state/apply-to-live", headers=JSON)
    assert r.status_code == 200, r.data[:400]
    assert _live_q1(env, "joint_offset") == 0.09
    assert _live_q1(env, "T1") == 1.0e-5


def test_a_refused_restore_live_marks_the_chip_diverged_and_keeps_save(env, monkeypatch):
    ts = _version_then_edit(env)
    real_tree = routes._live_merged_tree

    def tree(wc):
        _write_chip(env["live"], _state(t1=1.9e-5))     # a lab write after SM's backup
        return real_tree(wc)

    monkeypatch.setattr(routes, "_live_merged_tree", tree)
    r = env["client"].post(f"/state-history/{ts}/restore-live?force=1")
    monkeypatch.undo()
    assert r.status_code == 409 and b"Nothing was written" in r.data
    ctx = _ctx(env)
    assert ctx.get("live_diverged") is True
    assert _working_file_t1(env) == ctx["store"].get_value("qubits.q1.T1") == 1.0e-5
    assert not routes._shared_working_changed(ctx)
    r = env["client"].post("/save")
    assert r.status_code == 200 and b"Another SM window" not in r.data, r.data[:400]
    assert _live_q1(env, "T1") == 1.9e-5                 # the lab's write stands


def test_a_restore_whose_working_write_fails_names_the_working_copy(env, monkeypatch):
    ts = _version_then_edit(env)
    wf = Path(_ctx(env)["working_copy"].working_folder).resolve()
    real = safe_io.write_state_wiring

    def write(folder, *a, **kw):
        if Path(folder).resolve() == wf:
            raise PermissionError(13, "Access is denied", str(Path(folder) / "state.json"))
        return real(folder, *a, **kw)

    monkeypatch.setattr(safe_io, "write_state_wiring", write)
    r = env["client"].post(f"/state-history/{ts}/restore-live?force=1")
    monkeypatch.undo()
    html = r.get_data(as_text=True)
    assert r.status_code == 409 and r.headers.get("X-SM-Error-Code") == routes.WORKING_SAVE_FAILED
    assert "working copy could not be written" in html
    assert "live folder refused" not in html                    # MINOR 3
    assert not _ctx(env).get("live_readonly_hint")
    assert not routes._shared_working_changed(_ctx(env))


# --------------------------------------------------------------------------
# M3: every door that lands takes the pill down
# --------------------------------------------------------------------------

def test_pull_and_apply_that_lands_clears_the_pill(env):
    _edit(env, "qubits.q1.T1", "1.1e-05")
    _readonly(env["live"], True)
    r = env["client"].post("/state/sync", data={"mode": "apply"})
    assert r.status_code == 409 and _ctx(env).get("live_readonly_hint")
    _readonly(env["live"], False)
    r = env["client"].post("/state/sync", data={"mode": "apply"})
    assert r.status_code == 200 and r.get_json()["status"] == "ok", r.data[:400]
    assert _live_q1(env, "T1") == 1.1e-05
    assert not _ctx(env).get("live_readonly_hint")
    with env["app"].test_request_context():
        assert routes._auto_apply_armable(_ctx(env))[0] is True


def test_a_ctrl_z_walk_to_live_clears_the_pill(env):
    c = env["client"]
    r = c.post("/field/edit-batch", json={"updates": [{"dot_path": "qubits.q1.T1", "value": "1.1e-05"}],
                                          "expect_chip": ""})
    assert r.status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    _ctx(env)["live_readonly_hint"] = True          # an earlier denial raised it
    r = c.post("/undo")
    assert json.loads(r.headers["HX-Trigger"])["cellsReverted"]["live"] is True
    assert _live_q1(env, "T1") == 1.0e-5
    assert not _ctx(env).get("live_readonly_hint")


def test_dataset_apply_to_chip_that_lands_clears_the_pill(env, tmp_path):
    root = tmp_path / "data"
    run = root / "2026-12-30" / "#31_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": 31, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", _state(t1=2.5e-5))
    env["client"].post("/workspace/add", data={"folder": str(root)})
    _ctx(env)["live_readonly_hint"] = True
    r = env["client"].post(f"/dataset/{routes._folder_key(root)}:31/load-state?apply=1")
    assert r.status_code == 200, r.data[:400]
    assert _live_q1(env, "T1") == 2.5e-5
    assert not _ctx(env).get("live_readonly_hint")


def test_the_auto_calibrate_writer_reports_a_landed_write(monkeypatch):
    from quam_state_manager.core.autofit import writer
    seen = []
    chip = writer.ChipHandle(store=None, modifier=None, saver=None, wc=object(),
                             build_lock=None, live_path="x",
                             on_live_landed=lambda: seen.append(1))
    monkeypatch.setattr(writer.working_copy, "apply_to_live", lambda *a, **k: None)
    monkeypatch.setattr(writer, "_pending", lambda *a, **k: None)
    assert writer._apply_live_with_one_retry(chip, lambda: None) is None
    assert seen == [1]


def test_the_auto_calibrate_route_wires_the_landed_hook():
    src = Path(routes.__file__).read_text(encoding="utf-8")
    i = src.index("def _autofit_start_real(")
    seg = src[i:src.index("adapter = RealAdapter(", i)]
    assert "on_live_landed=lambda: _live_write_landed(ctx)" in seg


# --------------------------------------------------------------------------
# MINOR 3: only the live folder's refusal raises the pill
# --------------------------------------------------------------------------

def test_a_history_journal_failure_raises_no_read_only_pill(env, monkeypatch):
    _edit(env, "qubits.q1.T1", "1.1e-05")
    def record(self, *a, **kw):
        try:
            # no file name on it: only "it is SM's own journal" can tell
            raise PermissionError(13, "Access is denied")
        except PermissionError as exc:
            raise hub.RecordError("the chip's history could not be written") from exc

    monkeypatch.setattr(hub.Hub, "record", record)
    r = env["client"].post("/state/apply-to-live", headers=JSON)
    monkeypatch.undo()
    assert r.status_code == 409, r.data[:400]
    assert "live folder refused" not in r.get_json()["message"]
    assert not _ctx(env).get("live_readonly_hint")
    assert _live_q1(env, "T1") == 1.0e-5


def test_a_live_folder_denial_still_raises_the_pill(env):
    _edit(env, "qubits.q1.T1", "1.1e-05")
    _readonly(env["live"], True)
    r = env["client"].post("/state/apply-to-live", headers=JSON)
    assert r.status_code == 409
    assert "live folder refused" in r.get_json()["message"]
    assert _ctx(env).get("live_readonly_hint") is True


# --------------------------------------------------------------------------
# MINOR 1 / MINOR 2: the forced-write gate
# --------------------------------------------------------------------------

def test_a_same_content_resave_during_the_fsync_is_not_a_refusal(env, monkeypatch):
    _write_chip(env["live"], _state(t1=3.3e-05))         # live moved: Keep mine forces
    _edit(env, "qubits.q1.T1", "1.2e-05")
    real_commit = hub.Pending.commit

    def commit(self, *a, **kw):
        out = real_commit(self, *a, **kw)
        # the lab re-saves the SAME content while the line is fsync'd
        for name in ("state.json", "wiring.json"):
            p = env["live"] / name
            data = p.read_bytes()
            p.write_bytes(data)
            st = p.stat()
            os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 7_000_000))
        return out

    monkeypatch.setattr(hub.Pending, "commit", commit)
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    monkeypatch.undo()
    assert r.status_code == 200, r.data[:400]
    assert _live_q1(env, "T1") == 1.2e-05
    assert not _ctx(env).get("live_diverged")


def test_a_changed_content_during_the_fsync_is_still_refused(env, monkeypatch):
    _write_chip(env["live"], _state(t1=3.3e-05))
    _edit(env, "qubits.q1.T1", "1.2e-05")
    real_commit = hub.Pending.commit

    def commit(self, *a, **kw):
        out = real_commit(self, *a, **kw)
        _write_chip(env["live"], _state(t1=4.4e-05))     # a real change
        return out

    monkeypatch.setattr(hub.Pending, "commit", commit)
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    monkeypatch.undo()
    assert r.status_code == 409, r.data[:400]
    assert _live_q1(env, "T1") == 4.4e-05


def test_an_unreadable_before_read_is_named_as_such(env, monkeypatch):
    _write_chip(env["live"], _state(t1=3.3e-05))
    _edit(env, "qubits.q1.T1", "1.2e-05")
    monkeypatch.setattr(routes, "_live_merged_tree", lambda wc: None)
    r = env["client"].post("/state/apply-to-live?force=1", headers=JSON)
    monkeypatch.undo()
    assert r.status_code == 409, r.data[:400]
    msg = r.get_json()["message"]
    assert "it had found none" not in msg and "appeared" not in msg, msg
    assert "could not read the live chip" in msg, msg
    assert _live_q1(env, "T1") == 3.3e-05


# --------------------------------------------------------------------------
# MINOR 10: a failed pull is a named 409
# --------------------------------------------------------------------------

def test_a_pull_that_fails_is_a_named_409(env, monkeypatch):
    def boom(wc, *a, **kw):
        raise PermissionError(13, "simulated: the working folder refused", str(wc.working_folder))

    monkeypatch.setattr(working_copy, "sync_from_live", boom)
    r = env["client"].post("/state/sync", data={"mode": "discard"})
    monkeypatch.undo()
    assert r.status_code == 409, r.status_code
    body = r.get_json()
    assert body["error_code"] == routes.SYNC_FAILED and "not changed" in body["message"]


def test_a_working_folder_denial_inside_the_write_raises_no_pill(env, monkeypatch):
    """The write reads the WORKING pair first; that folder refusing is not the
    live folder refusing (MINOR 3)."""
    from quam_state_manager.core import doc_cache
    _edit(env, "qubits.q1.T1", "1.1e-05")
    wf = Path(_ctx(env)["working_copy"].working_folder).resolve()
    real_read, real_apply = doc_cache.read_pair, working_copy.apply_to_live
    armed = {"on": False}

    def read_pair(folder, *a, **kw):
        if armed["on"] and Path(folder).resolve() == wf:
            raise PermissionError(13, "Access is denied", str(wf / "state.json"))
        return real_read(folder, *a, **kw)

    def apply(*a, **kw):
        armed["on"] = True          # after the press's own save
        return real_apply(*a, **kw)

    monkeypatch.setattr(doc_cache, "read_pair", read_pair)
    monkeypatch.setattr(working_copy, "apply_to_live", apply)
    r = env["client"].post("/state/apply-to-live", headers=JSON)
    monkeypatch.undo()
    assert r.status_code == 409, r.data[:400]
    assert "live folder refused" not in r.get_json()["message"], r.get_json()
    assert not _ctx(env).get("live_readonly_hint")
    assert _live_q1(env, "T1") == 1.0e-5


def test_a_restore_that_landed_but_was_not_confirmed_shows_the_restored_version(env, monkeypatch):
    ts = _version_then_edit(env)
    live = env["live"].resolve()
    landed = {"v": False}
    real_write, real_mt = safe_io.write_state_wiring_bytes, safe_io.state_wiring_mtimes

    def write(folder, *a, **kw):
        out = real_write(folder, *a, **kw)
        if Path(folder).resolve() == live:
            landed["v"] = True
        return out

    def mtimes(folder, *a, **kw):
        if landed["v"] and Path(folder).resolve() == live:
            raise PermissionError(13, "simulated: the read-back was refused", str(folder))
        return real_mt(folder, *a, **kw)

    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", write)
    monkeypatch.setattr(safe_io, "state_wiring_mtimes", mtimes)
    r = env["client"].post(f"/state-history/{ts}/restore-live?force=1")
    monkeypatch.undo()
    html = r.get_data(as_text=True)
    assert r.status_code == 409 and "UNVERIFIED" in html, html[:600]
    assert "Nothing was written" not in html
    ctx = _ctx(env)
    assert _live_q1(env, "T1") == 0.5e-5                    # the version reached the chip
    assert ctx["store"].get_value("qubits.q1.T1") == 0.5e-5  # and SM shows it
    assert not ctx.get("live_readonly_hint")
    assert ctx.get("last_apply"), "Revert last apply points at the backup"
    assert not routes._shared_working_changed(ctx)


# --------------------------------------------------------------------------
# MINOR 6: Restore says what happens while Auto-Sync push is armed
# --------------------------------------------------------------------------

def test_restore_says_auto_sync_writes_at_once(env):
    _edit(env, "qubits.q1.z.joint_offset", "0.09")
    ctx = _ctx(env)
    path = pending_tray.set_aside(ctx["working_copy"], list(ctx["store"].change_log),
                                  reason="a test set them aside")
    env["client"].post("/undo")                              # the tray is empty again
    assert not ctx["store"].change_log
    off = env["client"].get("/state/review").get_data(as_text=True)
    assert "Nothing is written to the live chip until Apply" in off
    assert env["client"].post("/auto-apply/arm").status_code == 200
    on = env["client"].get("/state/review").get_data(as_text=True)
    assert "sp-unrec-restore" in on
    assert "Nothing is written to the live chip until Apply" not in on
    assert "ARMED" in on.split("sp-unrec-restore", 1)[1][:400]
    file_id = pending_tray._file_id(path, ctx["working_copy"])
    r = env["client"].post("/state/recovery/restore", json={"id": file_id})
    assert r.status_code == 200, r.data[:300]
    msg = r.get_json()["message"]
    assert "not on the live chip yet" not in msg and "ARMED" in msg, msg
