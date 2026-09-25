"""QA diagnostics-r2-04: a live write names the crash-class values it carries.

Apply to live, the ⚡ pull-and-apply, Auto-Sync and "Keep mine" wrote a value
SM itself flags as "would crash a node run" onto the live chip with a plain
green "Applied" (Auto-Sync: nothing at all). The fix is ADVISORY ONLY -- the
docs/104 #1 owner decision (no confirm on Apply) and the researcher-trust rule
stand: nothing is blocked or asked, the door's existing result line / confirm
names the values. A clean chip's responses are unchanged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

READOUT = "quam.components.pulses.SquareReadoutPulse"
AMP = "qubits.q1.resonator.operations.readout.amplitude"


def _state(amp=0.3):
    return {
        "qubits": {"q1": {
            "id": "q1",
            "resonator": {
                "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                "opx_input": "#/wiring/qubits/q1/rr/opx_input",
                "operations": {"readout": {"__class__": READOUT,
                                           "length": 640, "amplitude": amp}},
            },
        }},
        "qubit_pairs": {},
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {
                "band": 2, "upconverter_frequency": 7.0e9, "full_scale_power_dbm": 0}}}},
            "analog_outputs": {},
            "mw_inputs": {"con1": {"1": {"1": {}}}},
        },
    }


def _wiring():
    return {"wiring": {"qubits": {"q1": {
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
               "opx_input": "#/ports/mw_inputs/con1/1/1"},
    }}}, "network": {"host": "x", "cluster_name": "t"}}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"client": c, "live": live}


def _live_amp(env):
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    return doc["qubits"]["q1"]["resonator"]["operations"]["readout"]["amplitude"]


def _crash_edit(env):
    r = env["client"].post("/field/edit", data={"dot_path": AMP, "value": "1.5"})
    assert r.get_json()["ok"] is True


def test_apply_to_live_names_the_crash_values_and_still_writes(env):
    _crash_edit(env)
    r = env["client"].post("/state/apply-to-live")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert _live_amp(env) == 1.5                        # advisory: never blocks
    assert "would crash a node run" in body
    assert "toast-warning" in body and "toast-success" not in body
    assert "Applied to the live chip." not in body      # not the plain green line


def test_clean_apply_is_unchanged(env):
    env["client"].post("/field/edit", data={"dot_path": AMP, "value": "0.25"})
    r = env["client"].post("/state/apply-to-live")
    assert r.headers.get("HX-Trigger") == "liveDriftChanged, stateHistoryChanged"
    assert "Applied to the live chip." in r.get_data(as_text=True)
    assert "would crash" not in r.get_data(as_text=True)


def test_auto_sync_flush_signals_the_crash_values(env):
    c = env["client"]
    c.post("/auto-apply/arm")
    _crash_edit(env)
    r = c.post("/state/apply-to-live")
    trig = json.loads(r.headers["HX-Trigger"])
    assert set(trig) == {"liveDriftChanged", "stateHistoryChanged", "autoApplyApplied"}
    crash = trig["autoApplyApplied"]["crash"]
    assert crash["count"] >= 1 and "would crash a node run" in crash["sentence"]
    assert crash["sig"]                                 # the client dedups on it
    assert _live_amp(env) == 1.5


def test_pull_and_apply_names_them(env):
    _crash_edit(env)
    d = env["client"].post("/state/sync", data={"mode": "apply"}).get_json()
    assert d["status"] == "ok"
    assert "would crash a node run" in d["crash_values"]["sentence"]
    assert _live_amp(env) == 1.5


def test_pull_and_apply_clean_carries_no_key(env):
    env["client"].post("/field/edit", data={"dot_path": AMP, "value": "0.25"})
    d = env["client"].post("/state/sync", data={"mode": "apply"}).get_json()
    assert d["status"] == "ok" and "crash_values" not in d


def test_keep_mine_preflight_names_them(env):
    c = env["client"]
    assert c.get("/state/overwrite-live/preflight").get_json()["crash_values"] is None
    _crash_edit(env)
    d = c.get("/state/overwrite-live/preflight").get_json()
    assert d["ok"] and d["crash_values"]["count"] >= 1
    assert any("amplitude" in (v["jump_path"] or "") for v in d["crash_values"]["values"])


def test_client_renders_the_advisory_everywhere():
    """The three client doors read the server's one sentence (no second wording)."""
    root = Path(__file__).resolve().parent.parent / "quam_state_manager" / "web" / "static"
    app_js = (root / "app.js").read_text(encoding="utf-8")
    auto_js = (root / "auto-apply.js").read_text(encoding="utf-8")
    assert "data.crash_values.sentence" in app_js      # doStateSync apply toast
    assert "d.crash_values.sentence" in app_js         # Keep-mine confirm clause
    assert "e.detail.crash" in auto_js                 # Auto-Sync, once per set
