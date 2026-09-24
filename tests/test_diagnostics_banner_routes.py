"""Route tests for the on-load diagnostics surfacing: the auto error-banner,
the tray health badge, and the waveform findings appearing in the Explorer feed.
"""

from __future__ import annotations

import json

from quam_state_manager.web.app import create_app

READOUT = "quam.components.pulses.SquareReadoutPulse"


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


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
               "opx_input": "#/ports/mw_inputs/con1/1/1"},
    }}}, "network": {"host": "x", "cluster_name": "t"}}


def _client(tmp_path, readout_amp):
    (tmp_path / "state.json").write_text(json.dumps(_state(readout_amp)), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def test_banner_pops_when_chip_has_error(tmp_path):
    body = _client(tmp_path, 1.5).get("/diagnostics/banner").get_data(as_text=True)
    assert "diag-error-banner" in body
    assert "would crash a node run" in body


def test_banner_empty_when_chip_is_clean(tmp_path):
    # QA F-A: an EMPTY 200, never a 204 -- htmx 2.x does not swap a 204
    # (responseHandling {code:"204", swap:false}), so the slot kept the old
    # red banner after the error set emptied, until a full page reload.
    resp = _client(tmp_path, 0.3).get("/diagnostics/banner")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == ""


def test_banner_no_chip_is_empty_200(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    resp = app.test_client().get("/diagnostics/banner")
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == ""


def test_shipped_htmx_still_skips_204_swaps():
    # the premise the 200-always contract rests on (if a later htmx swaps a
    # 204 the contract is merely redundant, never wrong)
    from pathlib import Path
    js = (Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"
          / "static" / "htmx.min.js").read_text(encoding="utf-8")
    assert '{code:"204",swap:false}' in js


# QA F-B: the dismissal signature must name WHICH values are errors, not only
# how many -- a new same-count error set used to match the dismissed
# "count:chip" string and stay hidden for the rest of the tab session.
def _two_qubit_client(tmp_path, amp1, amp2):
    st = _state(amp1)
    q2 = json.loads(json.dumps(st["qubits"]["q1"]))
    q2["id"] = "q2"
    q2["resonator"]["operations"]["readout"]["amplitude"] = amp2
    st["qubits"]["q2"] = q2
    (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    client = app.test_client()
    client.post("/load", data={"folder": str(tmp_path)})
    return client


def _sig(client):
    import re
    body = client.get("/diagnostics/banner").get_data(as_text=True)
    m = re.search(r'data-diag-sig="([^"]*)"', body)
    assert m, body[:300]
    return m.group(1)


def test_dismissal_sig_differs_for_a_new_same_count_error_set(tmp_path):
    c = _two_qubit_client(tmp_path, 1.5, 0.3)
    sig_q1 = _sig(c)
    c.post("/field/edit", data={"dot_path": "qubits.q1.resonator.operations.readout.amplitude",
                                "value": "0.3"})
    c.post("/field/edit", data={"dot_path": "qubits.q2.resonator.operations.readout.amplitude",
                                "value": "1.6"})
    sig_q2 = _sig(c)
    # same count, same chip ...
    assert sig_q1.split(":")[:2] == sig_q2.split(":")[:2]
    # ... but a different error set, so a dismissal of the first must not hide it
    assert sig_q1 != sig_q2


def test_dismissal_sig_is_stable_while_lowering_a_still_bad_value(tmp_path):
    # A13: the signature must NOT carry the value, or the banner would re-pop
    # on every edit while the user is fixing the chip
    c = _two_qubit_client(tmp_path, 1.5, 0.3)
    before = _sig(c)
    c.post("/field/edit", data={"dot_path": "qubits.q1.resonator.operations.readout.amplitude",
                                "value": "1.3"})
    assert _sig(c) == before


def test_summary_badge_reflects_error(tmp_path):
    body = _client(tmp_path, 1.5).get("/diagnostics/summary").get_data(as_text=True)
    assert "diag-error" in body and "issue" in body


def test_summary_badge_healthy_when_clean(tmp_path):
    body = _client(tmp_path, 0.3).get("/diagnostics/summary").get_data(as_text=True)
    assert "healthy" in body


def test_waveform_finding_in_explorer_feed(tmp_path):
    feed = _client(tmp_path, 1.5).get("/diagnostics/findings.json").get_json()
    # the waveform range finding is Explorer-jumpable → in the value_spec bucket
    cats = [f["category"] for f in feed["value_spec"]]
    assert "waveform_range" in cats
    assert any(f["jump_path"].endswith("readout.amplitude") for f in feed["value_spec"])
