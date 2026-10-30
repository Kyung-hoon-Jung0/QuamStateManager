"""Gaussian CZ "to several pairs": plan groups, one-batch apply, stale plan,
person-only door, and the jsdom selfcheck of the dialog (gcz-several.js)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.test_gaussian_cz import _WIRING, _chip

ROOT = Path(__file__).resolve().parent.parent
PLAN = "/api/pulse/gaussian-cz/plan"
APPLY = "/api/pulse/gaussian-cz/apply-several"


def _chain_state(with_third=True):
    """q1-2 and q2-3 both move q2; q3-4 is a spare pair."""
    state = _chip(moving="target")           # q1-2 moves q2
    for n in ("q3", "q4"):
        state["qubits"][n] = deepcopy(state["qubits"]["q1"])
        state["qubits"][n]["id"] = n
    second = deepcopy(state["qubit_pairs"]["q1-2"])
    second.update(id="q2-3", qubit_control="#/qubits/q2",
                  qubit_target="#/qubits/q3", moving_qubit="control")   # moves q2
    state["qubit_pairs"]["q2-3"] = second
    if with_third:
        third = deepcopy(state["qubit_pairs"]["q1-2"])
        third.update(id="q3-4", qubit_control="#/qubits/q3",
                     qubit_target="#/qubits/q4", moving_qubit="control")
        state["qubit_pairs"]["q3-4"] = third
    return state


def _env(tmp_path, state, prove=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    if prove:
        app.config["PROVE_CALLERS"] = True
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    store = next(iter(app.config["contexts"].values()))["store"]
    return app, c, store


def _by_pair(plan):
    return {r["pair"]: r for r in plan["ready"]}


def test_plan_gives_distinct_names_for_a_shared_moving_qubit(tmp_path):
    _, c, _ = _env(tmp_path, _chain_state())
    r = c.post(PLAN, json={})
    assert r.status_code == 200, r.data
    plan = r.get_json()
    assert plan["total"] == 3 and plan["cant"] == []
    ready = _by_pair(plan)
    assert set(ready) == {"q1-2", "q2-3", "q3-4"}
    # q1-2 and q2-3 both move q2, yet no pulse name is shared
    assert ready["q1-2"]["moving"] == ready["q2-3"]["moving"] == "q2"
    names = [n for p in ("q1-2", "q2-3") for n in ready[p]["pulses"]]
    assert len(names) == len(set(names)) and "cz_gaussian_unipolar_pulse_q1_q2" in names
    assert "cz_gaussian_unipolar_pulse_q2_q3" in names
    assert plan["reason_suffix"] == "by chip f_01" and len(plan["plan_sig"]) == 40


def test_an_existing_operation_is_cant_with_its_reason_and_no_replace(tmp_path):
    state = _chain_state()
    # q3-4's moving qubit already carries the op the plan would write
    state["qubits"]["q3"]["z"]["operations"]["cz_gaussian_unipolar_pulse_q3_q4"] = {"amplitude": 0.1}
    # q2-3 already has a gate of the first macro's name
    state["qubit_pairs"]["q2-3"]["macros"]["cz_gaussian_unipolar"] = {"x": 1}
    _, c, store = _env(tmp_path, state)
    plan = c.post(PLAN, json={}).get_json()
    assert set(_by_pair(plan)) == {"q1-2"}
    reasons = {x["reason"]: x["pairs"] for x in plan["cant"]}
    assert reasons["q3 z already carries cz_gaussian_unipolar_pulse_q3_q4 from another pair"] == ["q3-4"]
    assert reasons["a gate named cz_gaussian_unipolar already exists"] == ["q2-3"]
    # a Can't pair cannot be written, even when named
    r = c.post(APPLY, json={"pairs": ["q3-4"], "plan_sig": plan["plan_sig"]})
    assert r.status_code == 400 and "Not ready" in r.get_json()["error"]
    assert "Replace" not in json.dumps(plan)
    assert "cz_gaussian_bipolar" not in store.state["qubit_pairs"]["q3-4"]["macros"]


def test_apply_writes_every_ticked_pair_as_one_undo_group(tmp_path):
    _, c, store = _env(tmp_path, _chain_state())
    plan = c.post(PLAN, json={}).get_json()
    r = c.post(APPLY, json={"pairs": ["q1-2", "q2-3"], "plan_sig": plan["plan_sig"]})
    assert r.status_code == 200, r.data
    j = r.get_json()
    assert j["ok"] and j["count"] == 2 and "tray_html" in j
    macros = lambda p: store.state["qubit_pairs"][p]["macros"]
    assert "cz_gaussian_unipolar" in macros("q1-2") and "cz_gaussian_bipolar" in macros("q2-3")
    ops = store.state["qubits"]["q2"]["z"]["operations"]
    assert "cz_gaussian_unipolar_pulse_q1_q2" in ops and "cz_gaussian_unipolar_pulse_q2_q3" in ops
    assert "cz_gaussian_unipolar" not in macros("q3-4")            # not ticked
    assert c.post("/undo").status_code == 200                      # ONE press
    assert "cz_gaussian_unipolar" not in macros("q1-2")
    assert "cz_gaussian_unipolar" not in macros("q2-3")
    ops = store.state["qubits"]["q2"]["z"]["operations"]
    assert not [k for k in ops if k.startswith("cz_gaussian")]


def test_a_stale_plan_signature_writes_nothing(tmp_path):
    _, c, store = _env(tmp_path, _chain_state())
    plan = c.post(PLAN, json={}).get_json()
    # the chip moves after the dialog was opened
    c.post("/api/pulse/gaussian-cz", data={"pair_id": "q3-4", "suffix_ids": '["q3","q4"]'})
    store.change_log.clear()
    r = c.post(APPLY, json={"pairs": ["q1-2"], "plan_sig": plan["plan_sig"]})
    assert r.status_code == 409 and r.get_json()["stale"] is True
    assert "cz_gaussian_unipolar" not in store.state["qubit_pairs"]["q1-2"]["macros"]


def test_a_changed_number_changes_the_signature(tmp_path):
    _, c, _ = _env(tmp_path, _chain_state())
    a = c.post(PLAN, json={}).get_json()["plan_sig"]
    b = c.post(PLAN, json={"params": {"qubit_filter_mhz": 33}}).get_json()["plan_sig"]
    assert a != b
    r = c.post(PLAN, json={"params": {"qubit_filter_mhz": "nan"}})
    assert r.status_code == 400


def test_the_apply_door_is_person_only(tmp_path):
    app, c, store = _env(tmp_path, _chain_state(), prove=True)
    plan = c.post(PLAN, json={}).get_json()          # the plan is a read: open
    assert plan["ok"]
    body = {"pairs": ["q1-2"], "plan_sig": plan["plan_sig"]}
    r = c.post(APPLY, json=body, headers={"X-SM-Agent": "claude"})
    assert r.status_code == 403
    r = c.post(APPLY, json=body)                      # no window proof either
    assert r.status_code == 403
    assert "cz_gaussian_unipolar" not in store.state["qubit_pairs"]["q1-2"]["macros"]
    c.set_cookie("sm_person", app.config["SM_PERSON_SECRET"], domain="localhost")
    assert c.post(APPLY, json=body).status_code == 200


def test_the_single_pair_form_offers_the_dialog_and_keeps_replace(tmp_path):
    _, c, _ = _env(tmp_path, _chain_state())
    html = c.get("/pulse/gaussian-cz").get_data(as_text=True)
    assert "…to several pairs" in html and "Gaussian CZ — to several pairs" in html
    assert "e.g. cz_gaussian_unipolar_pulse_q1_q2." in html
    assert c.post("/api/pulse/gaussian-cz", data={"pair_id": "q1-2"}).status_code == 200
    r = c.post("/api/pulse/gaussian-cz", data={"pair_id": "q1-2"})
    assert r.status_code == 409 and "Replace existing" in r.get_data(as_text=True)


# ---- the dialog under jsdom ------------------------------------------------

def test_the_dialog_selfcheck(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    env = dict(os.environ)
    env.setdefault("NODE_PATH", str(ROOT / "node_modules"))
    if not any((Path(p) / "jsdom").exists() for p in env["NODE_PATH"].split(os.pathsep)):
        pytest.skip("jsdom not installed")
    state = _chain_state()
    state["qubit_pairs"]["q3-4"]["macros"]["cz_gaussian_unipolar"] = {"x": 1}  # one Can't
    _, c, _ = _env(tmp_path / "chip", state)
    form = c.get("/pulse/gaussian-cz").get_data(as_text=True)
    plan = c.post(PLAN, json={}).get_json()
    (tmp_path / "form.html").write_text(form, encoding="utf-8")
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    env.update(GCZ_FORM=str(tmp_path / "form.html"), GCZ_PLAN=str(tmp_path / "plan.json"))
    r = subprocess.run([node, str(ROOT / "tests" / "gaussian_cz_several_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=300, cwd=str(ROOT), env=env)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "all ok" in r.stdout, out
