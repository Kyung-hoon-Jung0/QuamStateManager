"""QA diagnostics-r2-05: the Experiment Runner pre-flight must see what the red
Diagnostics banner sees.

The banner said "4 values ... would crash a node run -- Fix before running an
experiment" while the pre-flight right under it said "All blocking checks pass
-- ready to run", because no pre-flight input could see Diagnostics. The count
must be the banner's own (same findings, same summarize() error tier).
"""

from __future__ import annotations

import json
import re

from quam_state_manager.core import history, scheduler
from quam_state_manager.web.app import create_app

READOUT = "quam.components.pulses.SquareReadoutPulse"


# ---------------------------------------------------------------------------
# build_preflight (pure)
# ---------------------------------------------------------------------------

def _good_ctx(tmp_path) -> dict:
    chip = tmp_path / "quam_state"
    chip.mkdir()
    inst = tmp_path / "superconducting"
    cal = inst / "calibrations" / "1Q_2Q_calibrations"
    cal.mkdir(parents=True)
    ds = tmp_path / "dataset" / "LabA_1Q"
    ds.mkdir(parents=True)
    return {
        "chip_open": True, "chip_type": "quam",
        "open_chip_folder": str(chip), "target_quam_state": str(chip),
        "calibrations_folder": str(cal),
        "effective_config": {"state_path": str(chip),
                             "storage_location": str(tmp_path / "dataset"),
                             "calibration_library_folder": str(cal)},
        "editable_install_path": str(inst),
        "align_result": history.ALIGN_ALIGNED,
        "env_usable": True, "env_missing": [], "chip_clean": True,
        "dataset_roots": [str(ds)], "workspace_roots": [str(ds)],
    }


def _check(result, key):
    return next(c for c in result["checks"] if c["key"] == key)


def test_crash_class_errors_block_the_run(tmp_path):
    ctx = _good_ctx(tmp_path)
    assert scheduler.build_preflight(ctx)["ok"] is True      # the premise
    ctx["diagnostics_errors"] = 4
    ctx["diagnostics_examples"] = ["pulses.q1.x180", "pulses.q2.x180",
                                   "qubits.q1.xy", "qubits.q2.xy"]
    r = scheduler.build_preflight(ctx)
    c = _check(r, "diagnostics")
    assert c["status"] == "fail"
    assert r["ok"] is False
    assert "4 errors" in c["detail"]      # QA F-N: findings are errors, not values
    assert "would crash a node run" in c["detail"]
    assert "Fix before running an experiment" in c["detail"]
    # at most three examples, never the whole list
    assert "pulses.q1.x180" in c["detail"] and "qubits.q2.xy" not in c["detail"]


def test_one_error_reads_singular(tmp_path):
    ctx = _good_ctx(tmp_path)
    ctx["diagnostics_errors"] = 1
    c = _check(scheduler.build_preflight(ctx), "diagnostics")
    assert c["detail"].startswith("1 error on the open chip")


def test_zero_errors_passes(tmp_path):
    ctx = _good_ctx(tmp_path)
    ctx["diagnostics_errors"] = 0
    r = scheduler.build_preflight(ctx)
    assert _check(r, "diagnostics")["status"] == "pass"
    assert r["ok"] is True


def test_not_computed_skips_and_never_blocks(tmp_path):
    r = scheduler.build_preflight(_good_ctx(tmp_path))
    assert _check(r, "diagnostics")["status"] == "skip"
    assert r["ok"] is True


# ---------------------------------------------------------------------------
# the route: the SAME count the banner shows
# ---------------------------------------------------------------------------

def _state(readout_amp: float) -> dict:
    return {
        "qubits": {"q1": {
            "id": "q1",
            "resonator": {
                "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                "opx_input": "#/wiring/qubits/q1/rr/opx_input",
                "operations": {"readout": {"__class__": READOUT,
                                           "length": 640, "amplitude": readout_amp}},
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


def _client(tmp_path, readout_amp):
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_state(readout_amp)), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"wiring": {"qubits": {"q1": {
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
               "opx_input": "#/ports/mw_inputs/con1/1/1"}}}},
        "network": {"host": "x", "cluster_name": "t"}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(chip)})
    return client


def test_preflight_route_reports_the_banners_count(tmp_path):
    client = _client(tmp_path, 1.5)
    banner = client.get("/diagnostics/banner").get_data(as_text=True)
    assert "would crash a node run" in banner                # the premise
    body = client.post("/scheduler/preflight", json={}).get_json()
    c = _check(body, "diagnostics")
    assert c["status"] == "fail"
    assert body["ok"] is False
    n = int(re.match(r"(\d+) error", c["detail"]).group(1))
    # the banner's own number ("<b>N</b> error(s) on ... would crash", QA F-N)
    banner_n = int(re.search(r"<b>(\d+)</b>\s*error", banner).group(1))
    assert n == banner_n >= 1


def test_preflight_route_passes_on_a_clean_chip(tmp_path):
    client = _client(tmp_path, 0.3)
    assert client.get("/diagnostics/banner").get_data(as_text=True) == ""
    body = client.post("/scheduler/preflight", json={}).get_json()
    assert _check(body, "diagnostics")["status"] == "pass"


def test_preflight_route_without_a_chip_skips(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    body = app.test_client().post("/scheduler/preflight", json={}).get_json()
    assert _check(body, "diagnostics")["status"] == "skip"
