"""v1.5.1 U P1-5: every undo / redo result says whether the chip file was
written, and Help describes the setting that decides it.

Help said Ctrl+Z only STAGES ("the live chip is written by an explicit
Apply"), while the default "Ctrl+Z writes live: ON" made Ctrl+Z on an applied
edit write state.json at once. The toasts differed only by "-> live"
("Undone -> live on 'x'" / "Undone (staged)" / "Undone: ..."), so a user
could not tell from a staged undo whether the chip changed.

Pinned on the real routes: the RAM undo of an unapplied edit, the live
journal step (setting ON), the staged journal step (setting OFF) and each
redo say "written to the chip file of '<chip>'" or "in SM only, the chip
file is unchanged" -- and the live file agrees. With Auto-Sync push armed a
staged undo is written within a second; it says so. Help states the setting,
its default and its value now.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_WIRING = {"network": {"host": "1.1.1.1", "cluster_name": "C1"}}
LIVE = "— written to the chip file of 'live'"
SM_ONLY = "— in SM only, the chip file is unchanged"


def _state(off=0.08):
    return {"qubits": {"qA1": {"id": "qA1", "f_01": 5.0e9, "z": {"joint_offset": off}}},
            "qubit_pairs": {}, "active_qubit_names": ["qA1"]}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chips" / "live"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "c": c, "live": live}


def _edit(c, value):
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.qA1.z.joint_offset", "value": str(value)}]})
    assert r.status_code == 200 and r.get_json()["ok"], r.data


def _live_off(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["z"]["joint_offset"]


def _msg(r) -> str:
    raw = r.headers.get("HX-Trigger")
    assert raw, r.data[:300]
    return json.loads(raw)["cellsReverted"]["message"]


def _setting(env, on: bool):
    r = env["c"].post("/settings/undo-live", data={"enabled": "1" if on else "0"})
    assert r.status_code == 200 and r.get_json()["enabled"] is on


def test_an_unapplied_edits_undo_and_redo_say_sm_only(env):
    c = env["c"]
    _edit(c, 0.09)
    m = _msg(c.post("/undo"))
    assert m.startswith("Undone " + SM_ONLY + ": qubits.qA1.z.joint_offset"), m
    m = _msg(c.post("/redo"))
    assert m.startswith("Redone " + SM_ONLY + ": qubits.qA1.z.joint_offset"), m
    assert _live_off(env) == 0.08


def test_an_applied_edits_undo_with_the_default_setting_says_written(env):
    c = env["c"]
    _edit(c, 0.09)
    assert c.post("/state/apply-to-live").status_code == 200
    assert _live_off(env) == 0.09
    m = _msg(c.post("/undo"))
    assert m.startswith("Undone " + LIVE + ": qubits.qA1.z.joint_offset"), m
    assert _live_off(env) == 0.08, "the chip file was written, as the toast says"
    m = _msg(c.post("/redo"))
    assert m.startswith("Redone " + LIVE), m
    assert _live_off(env) == 0.09


def test_an_applied_edits_undo_with_the_setting_off_says_sm_only(env):
    c = env["c"]
    _setting(env, False)
    _edit(c, 0.09)
    assert c.post("/state/apply-to-live").status_code == 200
    m = _msg(c.post("/undo"))
    assert m.startswith("Undone " + SM_ONLY), m
    assert _live_off(env) == 0.09, "the chip file is unchanged, as the toast says"
    m = _msg(c.post("/redo"))
    assert m.startswith("Redone " + SM_ONLY), m


def test_auto_sync_push_armed_says_it_writes_the_staged_undo(env):
    c = env["c"]
    _edit(c, 0.09)
    ctx = env["app"].config["contexts"][env["app"].config["active_context"]]
    ctx["auto_apply"] = routes._new_auto_session(pull=False, pull_replace=False, push=True)
    m = _msg(c.post("/undo"))
    assert m.startswith("Undone — staged; Auto-Sync writes it to the chip file"), m


def test_help_states_the_setting_its_default_and_its_value(env):
    c = env["c"]
    html = c.get("/help").get_data(as_text=True)
    assert "crosses saves by STAGING" not in html
    assert "Ctrl+Z writes live</b> ON (Settings; the default)" in html
    assert '<b class="help-undo-now">ON</b>' in html
    _setting(env, False)
    html = c.get("/help").get_data(as_text=True)
    assert '<b class="help-undo-now">OFF</b>' in html
