"""v1.5.1 fix round 1 review: dataset "Apply to chip" after a press that fails.

M1. Every non-ok push was rolled back -- including a write that LANDED and
then failed its read-back. The answer said "Nothing was applied to the live
chip" while the chip held the run's state, the working copy went back to the
old pair (so the run's own write would look like outside drift) and the
read-only pill went up. Now only a push that provably changed nothing is
rolled back; a landed (or unverifiable) write keeps the run's state staged,
says UNVERIFIED, and sets the replaced rows aside.

MINOR 7. Set-aside rows carry their own reason, not "the working files changed
after these edits were staged".
MINOR 8. The rows are on disk BEFORE the stage clears the tray (a crash in
between leaves them listed), and leave the list once back or replaced.
MINOR 9. An edit another window stages during the press is never dropped by
the rollback.
MINOR 10. A run state that cannot be read, or a stage that cannot be written,
is a named 409, never a 500.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import pending_tray, safe_io, working_copy
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}
RUN_OFF, RUN_F01 = 0.079, 5.2e9


def _state(off=0.08, f01=5.0e9):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": f01, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


def _write_chip(folder: Path, state: dict):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")


def _readonly(folder: Path, on: bool):
    for name in ("state.json", "wiring.json"):
        os.chmod(folder / name, stat.S_IREAD if on else (stat.S_IREAD | stat.S_IWRITE))
    _set_dir_readonly(folder, on)


def _set_dir_readonly(folder: Path, on: bool) -> None:
    """On POSIX a read-only FILE is still replaced by rename; the refusal is
    real only when the containing directory denies new entries too. Windows
    already refuses ReplaceFileW on a read-only file, and its directory
    attribute is not enforced, so the directory is left alone there."""
    if sys.platform != "win32" and folder.is_dir():
        os.chmod(folder, 0o555 if on else 0o755)



@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    _write_chip(live, _state())
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    root = tmp_path / "data"
    run = root / "2026-12-30" / "#31_08_spec_010000"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": "08_spec", "status": "successful",
                     "run_start": "2026-12-30T01:00:00", "run_end": "2026-12-30T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["qA1"]}}, "outcomes": {}},
        "id": 31, "parents": [], "created_at": "2026-12-30T01:00:00"}), encoding="utf-8")
    (run / "data.json").write_text("{}", encoding="utf-8")
    _write_chip(run / "quam_state", _state(off=RUN_OFF, f01=RUN_F01))
    c.post("/workspace/add", data={"folder": str(root)})
    e = {"app": app, "client": c, "live": live, "run": run,
         "uid": f"{routes._folder_key(root)}:31"}
    yield e
    _readonly(live, False)


def _ctx(env):
    return env["app"].config["contexts"][env["app"].config["active_context"]]


def _edit(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _press(env):
    return env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1")


def _live(env, key="joint_offset"):
    q = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]
    return q["z"]["joint_offset"] if key == "joint_offset" else q[key]


def _tray(env):
    return [(e.dot_path, e.new_value) for e in _ctx(env)["store"].change_log]


def _listed(env):
    return pending_tray.unrecovered(_ctx(env)["working_copy"])


def _landed_then_readback_fails(env, monkeypatch, *, unreadable=False):
    """The run's write lands on the live chip; reading it back then fails --
    its mtimes (the journal settles it as landed), or every read (nobody can
    tell: unknown)."""
    live = env["live"].resolve()
    landed = {"v": False}
    real_write = safe_io.write_state_wiring_bytes
    real_mt = safe_io.state_wiring_mtimes
    real_hash = working_copy.live_content_hash

    def write(folder, *a, **kw):
        out = real_write(folder, *a, **kw)
        if Path(folder).resolve() == live:
            landed["v"] = True
        return out

    def mtimes(folder, *a, **kw):
        if landed["v"] and Path(folder).resolve() == live:
            raise PermissionError(13, "simulated: the read-back was refused", str(folder))
        return real_mt(folder, *a, **kw)

    def content_hash(wc, *a, **kw):
        if landed["v"] and unreadable:
            raise OSError(5, "simulated: the live chip cannot be read back")
        return real_hash(wc, *a, **kw)

    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", write)
    monkeypatch.setattr(safe_io, "state_wiring_mtimes", mtimes)
    monkeypatch.setattr(working_copy, "live_content_hash", content_hash)
    return landed


# --------------------------------------------------------------------------
# M1: a write that landed is never "nothing was applied"
# --------------------------------------------------------------------------

def test_a_landed_but_unverified_write_keeps_the_run_state_staged(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    landed = _landed_then_readback_fails(env, monkeypatch)
    r = _press(env)
    monkeypatch.undo()
    assert landed["v"]
    html = r.get_data(as_text=True)
    assert r.status_code == 409, (r.status_code, html[:400])
    assert _live(env) == RUN_OFF                      # the run's state IS on the chip
    assert "Nothing was applied to the live chip" not in html, html[:2000]
    assert "UNVERIFIED" in html
    ctx = _ctx(env)
    # the working copy keeps what the chip got: the run's own write is not drift
    assert ctx["store"].get_value("qubits.qA1.z.joint_offset") == RUN_OFF
    assert ctx.get("staged_base") and ctx.get("working_dirty")
    assert not ctx.get("live_readonly_hint"), "a landed write proves the folder writable"
    # the replaced edit is set aside -- listed, named, with its true reason
    listed = _listed(env)
    assert len(listed) == 1, listed
    assert [(x["path"], x["new"]) for x in listed[0]["rows"]] == [("qubits.qA1.z.joint_offset", 0.09)]
    assert "Apply to chip" in listed[0]["reason"], listed[0]["reason"]
    assert listed[0]["file"] in html.replace("&#39;", "'"), html[:2000]
    # nothing is stuck: the next Apply finds the chip holding it and settles
    r = env["client"].post("/state/apply-to-live", headers={"Accept": "application/json"})
    assert r.status_code == 200, r.data[:400]
    assert _live(env) == RUN_OFF and not _ctx(env).get("working_dirty")


def test_an_unreadable_chip_after_the_write_is_unverified_too(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    landed = _landed_then_readback_fails(env, monkeypatch, unreadable=True)
    r = _press(env)
    monkeypatch.undo()
    assert landed["v"] and r.status_code == 409
    html = r.get_data(as_text=True)
    assert "Nothing was applied to the live chip" not in html, html[:2000]
    assert "UNVERIFIED" in html and "cannot tell" in html
    assert _ctx(env).get("staged_base")
    assert _ctx(env).get("live_diverged") is True
    assert len(_listed(env)) == 1


def test_a_write_that_provably_failed_still_rolls_back(env):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    _readonly(env["live"], True)
    r = _press(env)
    _readonly(env["live"], False)
    assert r.status_code == 409
    assert "Nothing was applied to the live chip" in r.get_data(as_text=True)
    assert _tray(env) == [("qubits.qA1.z.joint_offset", 0.09)]
    assert not _ctx(env).get("staged_base")
    assert _live(env) == 0.08
    # MINOR 8: the copy set aside before the stage leaves the list once the
    # rows are back (its bytes stay on disk)
    assert _listed(env) == []
    folder = _ctx(env)["working_copy"].working_folder.parent
    assert list(folder.glob("*.pending_tray.restored-*.json"))


# --------------------------------------------------------------------------
# MINOR 8: on disk before the stage
# --------------------------------------------------------------------------

def test_the_rows_are_on_disk_while_the_push_runs(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    seen = {}
    real = routes._sync_pull_apply_to_live

    def spy(ctx, *a, **kw):
        # what survives a crash right now: the tray is already cleared
        seen.setdefault("tray", list(ctx["store"].change_log))
        seen.setdefault("listed", pending_tray.unrecovered(ctx["working_copy"]))
        return real(ctx, *a, **kw)

    monkeypatch.setattr(routes, "_sync_pull_apply_to_live", spy)
    r = _press(env)
    assert r.status_code == 200, r.data[:400]
    assert seen["tray"] == []
    assert [[x["path"] for x in d["rows"]] for d in seen["listed"]] == [["qubits.qA1.z.joint_offset"]]
    # landed: the replaced rows are named in the result and leave the list
    assert _listed(env) == []
    folder = _ctx(env)["working_copy"].working_folder.parent
    assert list(folder.glob("*.pending_tray.replaced-*.json"))


# --------------------------------------------------------------------------
# MINOR 7: the reason
# --------------------------------------------------------------------------

def test_rows_that_cannot_go_back_say_why_they_were_set_aside(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")

    def boom(*_a, **_k):
        raise ValueError("simulated replay failure")

    monkeypatch.setattr(pending_tray, "reapply_rows", boom)
    _readonly(env["live"], True)
    r = _press(env)
    assert r.status_code == 409
    listed = _listed(env)
    assert len(listed) == 1, listed
    assert "working files changed" not in listed[0]["reason"], listed[0]["reason"]
    assert "Apply to chip" in listed[0]["reason"], listed[0]["reason"]
    assert "Apply to chip" in env["client"].get("/state/review").get_data(as_text=True)


# --------------------------------------------------------------------------
# MINOR 9: another window's edit during the press
# --------------------------------------------------------------------------

def test_an_edit_staged_by_another_window_during_the_write_is_kept(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    live = env["live"].resolve()
    real_write = safe_io.write_state_wiring_bytes

    def write(folder, *a, **kw):
        if Path(folder).resolve() == live:
            # another window's /field/edit lands while the write is under way
            _ctx(env)["modifier"].set_value("qubits.qA1.f_01", 5.3e9)
            raise PermissionError(13, "Access is denied", str(folder))
        return real_write(folder, *a, **kw)

    monkeypatch.setattr(safe_io, "write_state_wiring_bytes", write)
    r = _press(env)
    monkeypatch.undo()
    assert r.status_code == 409, r.data[:400]
    tray = dict(_tray(env))
    assert tray.get("qubits.qA1.z.joint_offset") == 0.09
    assert tray.get("qubits.qA1.f_01") == 5.3e9, (
        f"the other window's edit was dropped: {tray} / {r.get_data(as_text=True)[:1500]}")
    assert "another window" in r.get_data(as_text=True)


def test_an_edit_saved_by_the_press_for_another_window_is_kept(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    real = routes._sync_pull_apply_to_live

    def spy(ctx, *a, **kw):
        # another window stages right before this press saves and pushes
        ctx["modifier"].set_value("qubits.qA1.f_01", 5.3e9)
        return real(ctx, *a, **kw)

    monkeypatch.setattr(routes, "_sync_pull_apply_to_live", spy)
    _readonly(env["live"], True)
    r = _press(env)
    assert r.status_code == 409, r.data[:400]
    tray = dict(_tray(env))
    assert tray.get("qubits.qA1.z.joint_offset") == 0.09
    assert tray.get("qubits.qA1.f_01") == 5.3e9, (
        f"the other window's edit was dropped: {tray} / {r.get_data(as_text=True)[:1500]}")


# --------------------------------------------------------------------------
# MINOR 10: named 409s
# --------------------------------------------------------------------------

def test_a_run_state_that_cannot_be_read_is_a_named_409(env):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    (env["run"] / "quam_state" / "state.json").write_text("{ not json", encoding="utf-8")
    # past the identity confirm (an unreadable run cannot be fingerprinted)
    r = env["client"].post(f"/dataset/{env['uid']}/load-state?apply=1&force_chip=1")
    assert r.status_code == 409, r.status_code
    assert r.headers.get("X-SM-Error-Code") == routes.RUN_STATE_UNREADABLE
    assert _tray(env) == [("qubits.qA1.z.joint_offset", 0.09)]


def test_a_stage_that_cannot_be_written_is_a_named_409_and_keeps_the_edits(env, monkeypatch):
    _edit(env, "qubits.qA1.z.joint_offset", "0.09")
    wf = Path(_ctx(env)["working_copy"].working_folder).resolve()
    real = safe_io.write_state_wiring
    calls = {"n": 0}

    def write(folder, *a, **kw):
        if Path(folder).resolve() == wf and calls["n"] == 0:
            calls["n"] += 1
            raise PermissionError(13, "simulated: the working folder refused", str(folder))
        return real(folder, *a, **kw)

    monkeypatch.setattr(safe_io, "write_state_wiring", write)
    r = _press(env)
    monkeypatch.undo()
    assert r.status_code == 409, (r.status_code, r.data[:400])
    assert r.headers.get("X-SM-Error-Code") == routes.WORKING_SAVE_FAILED
    assert _tray(env) == [("qubits.qA1.z.joint_offset", 0.09)]
    assert not _ctx(env).get("live_readonly_hint")
    assert _live(env) == 0.08
    assert _listed(env) == []
