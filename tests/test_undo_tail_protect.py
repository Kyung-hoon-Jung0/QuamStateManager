"""A Ctrl+Z that reached the chip stays protected for the whole window from
the undo, even after a later action on another field truncated the redo
branch (user decision 2026-11-05). The truncation keeps a small record of
each live-undone unit in the journal sidecar (``undone_tail``)."""
from __future__ import annotations

import json
import time as _time

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.core import undo_journal
from tests.test_autosync_lost_applied import _apply_name, _outside_save_and_pull
from tests.test_autosync_merge import _arm, _state, env  # noqa: F401

T1 = "qubits.qA1.T1"


def _live(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]


def _apply_f01(env, v="5100000000.0"):
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": v}).status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    assert _live(env)["f_01"] == float(v)


def _undo_live(env):
    assert env["client"].post("/undo").status_code == 200
    assert _live(env)["T1"] == 2e-05


def _sidecar(env):
    with env["app"].test_request_context():
        return undo_journal.sidecar_path(env["app"].instance_path, routes_mod._active_ctx()["path"])


def _held(env):
    with env["app"].test_request_context():
        return routes_mod._applied_values_held(routes_mod._active_ctx())


def _undo_then_other_apply(env):
    _apply_name(env)          # T1 3.3e-5 live
    _undo_live(env)           # T1 back to 2e-5 live
    _apply_f01(env)           # a new action: the redo branch is truncated
    units, cur = undo_journal.load_state(_sidecar(env))
    assert cur == len(units)
    assert not any(e.get("path") == T1 for u in units for e in u.get("entries") or ()), \
        "the truncation must have dropped the T1 unit"


def test_armed_a_node_writing_the_pre_undo_value_is_restored(env):  # noqa: F811
    _arm(env, pull=True, push=True)
    _undo_then_other_apply(env)
    r = _outside_save_and_pull(env, _state(f01=5.1e9, t1=3.3e-05))
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["autoSyncRestored"]["count"] == 1, trig
    assert _live(env)["T1"] == 2e-05


def test_off_a_node_writing_the_pre_undo_value_is_flagged(env):  # noqa: F811
    _undo_then_other_apply(env)
    held = _held(env)
    assert held.get(T1) == 2e-05 and held.get("qubits.qA1.f_01") == 5.1e9
    assert routes_mod._applied_value_lost(held, T1, 3.3e-05)
    assert not routes_mod._applied_value_lost(held, T1, 2e-05)


def test_a_stage_only_undo_is_not_protected(env):  # noqa: F811
    assert env["client"].post("/settings/undo-live", data={"enabled": "0"}).status_code == 200
    _apply_name(env)
    assert env["client"].post("/undo").status_code == 200
    assert _live(env)["T1"] == 3.3e-05, "stage-only: the chip is untouched"
    assert undo_journal.load_tail(_sidecar(env)) == []
    assert T1 not in _held(env) or _held(env)[T1] != 2e-05


def test_a_newer_apply_of_the_path_supersedes_the_record(env):  # noqa: F811
    _undo_then_other_apply(env)
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": T1, "value": "4.4e-05"}).status_code == 200
    assert c.post("/state/apply-to-live").status_code == 200
    held = _held(env)
    assert held.get(T1) == 4.4e-05
    # the chain extends: both earlier values read as a loss of the newest
    assert routes_mod._applied_value_lost(held, T1, 2e-05)


def test_the_record_expires_six_hours_after_the_undo(env, monkeypatch):  # noqa: F811
    _undo_then_other_apply(env)
    now = _time.time()
    monkeypatch.setattr(routes_mod.time, "time", lambda: now + 5 * 3600 + 50 * 60)
    monkeypatch.setattr(undo_journal.time, "time", lambda: now + 5 * 3600 + 50 * 60)
    assert _held(env).get(T1) == 2e-05, "inside the window"
    monkeypatch.setattr(routes_mod.time, "time", lambda: now + 6 * 3600 + 60)
    monkeypatch.setattr(undo_journal.time, "time", lambda: now + 6 * 3600 + 60)
    assert T1 not in _held(env)
    assert undo_journal.load_tail(_sidecar(env)) == []


def _u(i, path="a.b", undone=None):
    meta = {} if undone is None else {"undone_ts": undone}
    return {"id": f"u{i}", "ts": _time.time(), "meta": meta, "entries": [
        {"path": path, "old": i, "new": i + 1, "source_file": "state",
         "created": False, "deleted": False}]}


def test_two_processes_share_the_record_and_every_writer_keeps_it(tmp_path):
    """The record lives in the sidecar file: another process (a fresh read)
    sees it, and every later writer (cursor, mark, drop, append) keeps it."""
    p = tmp_path / "j.undo_journal.json"
    undo_journal.append_units(p, [_u(1), _u(2, "x.y")])
    undo_journal.save_cursor(p, 1)                 # u2 undone live
    undo_journal.append_units(p, [_u(3, "z")])     # truncates u2
    raw = json.loads(p.read_text(encoding="utf-8"))
    assert [e["path"] for r in raw["undone_tail"] for e in r["entries"]] == ["x.y"]
    undo_journal.mark_unit(p, "u1", {"k": 1})
    undo_journal.mark_units(p, ["u3"], {"k": 2})
    undo_journal.save_cursor(p, 2)
    undo_journal.drop_units(p, ["u1"])
    undo_journal.insert_units(p, [_u(4, "w")], before_tail=0)
    tail = undo_journal.load_tail(p)               # a second reader
    assert [e["path"] for r in tail for e in r["entries"]] == ["x.y"]
    assert tail[0]["entries"][0]["old"] == 2 and tail[0]["entries"][0]["new"] == 3


def test_a_unit_never_undone_live_leaves_no_record(tmp_path):
    p = tmp_path / "j.undo_journal.json"
    undo_journal.append_units(p, [_u(1)])
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["cursor"] = 0                               # past the cursor, no stamp
    p.write_text(json.dumps(doc), encoding="utf-8")
    undo_journal.append_units(p, [_u(2)])
    assert undo_journal.load_tail(p) == []


def test_the_tail_is_bounded(tmp_path):
    t = _time.time()
    tail = [{"undone_ts": t + i, "entries": [{"path": f"p{i}", "old": 0, "new": 1}]}
            for i in range(undo_journal.UNDONE_TAIL_MAX + 50)]
    kept = undo_journal._clean_tail(tail, now=t + 1000)
    assert len(kept) == undo_journal.UNDONE_TAIL_MAX
    assert kept[-1]["entries"][0]["path"] == f"p{undo_journal.UNDONE_TAIL_MAX + 49}"
